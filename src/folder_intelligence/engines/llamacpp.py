"""Motore llama.cpp in-process (`llama-cpp-python`, GPU Metal su Apple Silicon).

- Nessun servizio esterno: i modelli GGUF vengono caricati dentro l'app.
- Riusa i modelli già scaricati da Ollama (`qwen2.5:14b`, `bge-m3`…): il nome viene risolto nel
  file GGUF in ~/.ollama/models/blobs. In alternativa si può indicare il percorso di un `.gguf`.
- JSON vincolato da grammatica: con uno schema il modello *non può* produrre JSON non valido o
  valori fuori dall'elenco (es. nomi di comando inesistenti).
- Caricamento pigro e scaricamento dopo `FI_LLAMA_IDLE` secondi di inattività (default 600),
  per non tenere ~9 GB occupati quando non servono.
- Le immagini non sono gestite qui (la facciata AIEngine usa un altro motore per la visione).
"""

from __future__ import annotations

import asyncio
import atexit
import gc
import json
import os
import threading
import time
import weakref
from pathlib import Path
from typing import Any

from .base import EMBED, TEXT, EngineStrategy, parse_json

_ENGINES: "weakref.WeakSet[LlamaCppEngine]" = weakref.WeakSet()


def close_all() -> None:
    """Libera i modelli prima che il processo termini: se restano allocati, la pulizia
    di Metal all'uscita fallisce con GGML_ASSERT e un crash."""
    for e in list(_ENGINES):
        e.close()


atexit.register(close_all)

IDLE_SECONDS = float(os.environ.get("FI_LLAMA_IDLE", "600"))
N_CTX = int(os.environ.get("FI_LLAMA_CTX", "8192"))
EMBED_CTX = 8192
N_BATCH = int(os.environ.get("FI_LLAMA_BATCH", "2048"))
FLASH_ATTN = os.environ.get("FI_LLAMA_FLASH_ATTN", "1") != "0"
MAX_EMBED_CHARS = 8000


def ollama_models_dir() -> Path:
    return Path(os.environ.get("OLLAMA_MODELS", "~/.ollama/models")).expanduser()


def resolve_gguf(name: str) -> Path | None:
    """'qwen2.5:14b' → file GGUF scaricato da Ollama; oppure un percorso .gguf esplicito.

    Restituisce None se non trovato o se il modello Ollama non è un GGUF unico (alcuni modelli
    recenti, es. qwen3.5, usano un formato a tensori separati che llama.cpp non legge).
    """
    if not name:
        return None
    p = Path(name).expanduser()
    if p.suffix == ".gguf" and p.is_file():
        return p
    repo, _, tag = name.partition(":")
    tag = tag or "latest"
    if "/" not in repo:
        repo = f"library/{repo}"
    manifest = ollama_models_dir() / "manifests" / "registry.ollama.ai" / repo / tag
    try:
        layers = json.loads(manifest.read_text()).get("layers", [])
    except (OSError, json.JSONDecodeError):
        return None
    models = [layer for layer in layers if layer.get("mediaType") == "application/vnd.ollama.image.model"]
    if len(models) != 1:
        return None
    blob = ollama_models_dir() / "blobs" / models[0]["digest"].replace(":", "-")
    if not blob.is_file():
        return None
    with open(blob, "rb") as f:
        if f.read(4) != b"GGUF":
            return None
    return blob


_LOG_SILENCED = False


def _silence_native_logs() -> None:
    """llama.cpp scrive molti log su stderr (inizializzazione Metal ecc.): li spegniamo."""
    global _LOG_SILENCED
    if _LOG_SILENCED or os.environ.get("FI_DEBUG"):
        return
    import llama_cpp

    @llama_cpp.llama_log_callback
    def _noop(level, text, user_data):  # noqa: ARG001
        return None

    llama_cpp.llama_log_set(_noop, None)
    _silence_native_logs.callback = _noop  # type: ignore[attr-defined]  # evita il garbage collect
    _LOG_SILENCED = True


class OutOfMemory(RuntimeError):
    pass


def _ollama_url() -> str:
    return os.environ.get("FI_OLLAMA_URL", "http://localhost:11434").rstrip("/")


def ollama_loaded() -> list[dict[str, Any]]:
    """Modelli che Ollama tiene in memoria (GET /api/ps); [] se Ollama non è attivo."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{_ollama_url()}/api/ps", timeout=2) as r:
            return json.loads(r.read()).get("models", [])
    except Exception:
        return []


def ollama_unload(name: str) -> bool:
    """Chiede a Ollama di liberare un modello (keep_alive=0). Lo ricaricherà da solo se serve."""
    import urllib.request

    body = json.dumps({"model": name, "keep_alive": 0}).encode()
    req = urllib.request.Request(f"{_ollama_url()}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception:
        return False


def free_ollama_memory(only: str | None = None) -> list[str]:
    """Libera la memoria GPU occupata da Ollama: solo il modello `only` (stessa copia che
    carichiamo noi) oppure tutti. Disattivabile con FI_LLAMA_FREE_OLLAMA=0."""
    if os.environ.get("FI_LLAMA_FREE_OLLAMA", "1") == "0":
        return []
    freed = []
    for m in ollama_loaded():
        name = m.get("name") or m.get("model") or ""
        if only is None or name in (only, f"{only}:latest"):
            if ollama_unload(name):
                freed.append(name)
    if freed:
        time.sleep(0.5)  # lascia al driver il tempo di restituire la memoria
    return freed


def _is_oom(e: Exception) -> bool:
    msg = str(e)
    return "llama_decode returned" in msg or "failed to create llama_context" in msg \
        or "Failed to load model" in msg or "out of memory" in msg.lower()


class _Loaded:
    def __init__(self, llm: Any) -> None:
        self.llm = llm
        self.lock = threading.Lock()   # un'istanza llama.cpp non è thread-safe
        self.last_used = time.monotonic()


class LlamaCppEngine(EngineStrategy):
    name = "llamacpp"
    label = "llama.cpp"
    supports_text = True
    supports_embeddings = True

    def __init__(self, model: str | None = None, vision_model: str | None = None,
                 embed_model: str | None = None, **options: Any) -> None:
        super().__init__(model, vision_model, embed_model, **options)
        self._loaded: dict[tuple[str, bool], _Loaded] = {}
        self._registry_lock = threading.Lock()
        self._reaper: threading.Thread | None = None
        _ENGINES.add(self)

    # ---------------------------------------------------------------- stato
    @staticmethod
    def installed() -> bool:
        """Controlla il pacchetto senza importarlo (l'import inizializza la GPU: lento)."""
        import importlib.util

        try:
            return importlib.util.find_spec("llama_cpp") is not None
        except (ImportError, ValueError):
            return False

    async def available(self, capability: str = TEXT) -> bool:
        if capability not in (TEXT, EMBED) or not self.installed():
            return False
        return resolve_gguf(self.model_for(capability)) is not None

    def why_unavailable(self, capability: str = TEXT) -> str:
        if not self.installed():
            return "llama.cpp non installato: pip install llama-cpp-python"
        model = self.model_for(capability)
        return (f"modello '{model}' non trovato come file GGUF (scaricalo con `ollama pull {model}` "
                "o indica il percorso di un file .gguf)")

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d["loaded"] = [m for (m, _e) in self._loaded]
        return d

    # ---------------------------------------------------------------- modelli
    def _get(self, name: str, embedding: bool = False) -> _Loaded:
        key = (name, embedding)
        with self._registry_lock:
            if key in self._loaded:
                entry = self._loaded[key]
                entry.last_used = time.monotonic()
                return entry
            path = resolve_gguf(name)
            if path is None:
                raise FileNotFoundError(self.why_unavailable(EMBED if embedding else TEXT))
            _silence_native_logs()
            from llama_cpp import Llama

            if not Path(name).suffix == ".gguf":
                free_ollama_memory(only=name)  # stessa copia caricata due volte: inutile

            kwargs: dict[str, Any] = dict(model_path=str(path), n_gpu_layers=-1, verbose=False,
                                          flash_attn=FLASH_ATTN)
            if embedding:
                kwargs.update(embedding=True, n_ctx=EMBED_CTX, n_batch=EMBED_CTX, n_ubatch=EMBED_CTX)
            else:
                kwargs.update(n_ctx=N_CTX, n_batch=N_BATCH)
            try:
                llm = Llama(**kwargs)
            except Exception as e:
                if not _is_oom(e) or not free_ollama_memory():
                    raise self._oom_error(name) from e
                llm = Llama(**kwargs)
            entry = _Loaded(llm)
            self._loaded[key] = entry
            self._start_reaper()
            return entry

    def _start_reaper(self) -> None:
        if self._reaper and self._reaper.is_alive():
            return

        def reap() -> None:
            while self._loaded:
                time.sleep(min(30.0, max(1.0, IDLE_SECONDS / 4)))
                now = time.monotonic()
                with self._registry_lock:
                    for key, entry in list(self._loaded.items()):
                        if now - entry.last_used > IDLE_SECONDS and entry.lock.acquire(blocking=False):
                            try:
                                del self._loaded[key]
                                entry.llm.close()
                            finally:
                                entry.lock.release()
                    gc.collect()

        self._reaper = threading.Thread(target=reap, name="llamacpp-idle", daemon=True)
        self._reaper.start()

    def close(self) -> None:
        with self._registry_lock:
            for entry in self._loaded.values():
                try:
                    entry.llm.close()
                except Exception:
                    pass
            self._loaded.clear()

    @staticmethod
    def _oom_error(name: str) -> OutOfMemory:
        return OutOfMemory(
            f"memoria GPU insufficiente per caricare {name} con llama.cpp. Chiudi altre app pesanti "
            "o controlla i modelli tenuti in memoria da Ollama (`ollama ps`), oppure usa --engine ollama.")

    async def _run(self, name: str, embedding: bool, fn) -> Any:
        def work() -> Any:
            entry = self._get(name, embedding)
            with entry.lock:
                try:
                    try:
                        return fn(entry.llm)
                    except RuntimeError as e:
                        # memoria GPU esaurita (es. Ollama ha caricato altri modelli): libera e riprova
                        if not _is_oom(e):
                            raise
                        free_ollama_memory()
                        entry.llm.reset()
                        try:
                            return fn(entry.llm)
                        except RuntimeError as e2:
                            if _is_oom(e2):
                                raise self._oom_error(name) from e2
                            raise
                finally:
                    entry.last_used = time.monotonic()

        return await asyncio.to_thread(work)

    # ---------------------------------------------------------------- testo
    async def generate_json(self, prompt: str, system: str = "", schema: dict[str, Any] | None = None,
                            model: str | None = None) -> dict[str, Any]:
        messages = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": "user", "content": prompt}]
        fmt: dict[str, Any] = {"type": "json_object"}
        if schema:
            fmt["schema"] = schema

        def call(llm) -> str:
            out = llm.create_chat_completion(messages=self._fit(llm, messages), response_format=fmt,
                                             temperature=0, max_tokens=1024)
            return out["choices"][0]["message"]["content"] or ""

        return parse_json(await self._run(model or self.model, False, call))

    async def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        def call(llm) -> str:
            out = llm.create_chat_completion(messages=self._fit(llm, messages), temperature=0.2,
                                             max_tokens=1024)
            return (out["choices"][0]["message"]["content"] or "").strip()

        return await self._run(model or self.model, False, call)

    @staticmethod
    def _fit(llm, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Accorcia l'ultimo messaggio se il totale supera il contesto (lascia spazio alla risposta)."""
        budget = llm.n_ctx() - 1100
        total = sum(len(llm.tokenize(m["content"].encode(), add_bos=False)) + 8 for m in messages)
        if total <= budget:
            return messages
        last = dict(messages[-1])
        tokens = llm.tokenize(last["content"].encode(), add_bos=False)
        keep = max(256, len(tokens) - (total - budget))
        last["content"] = llm.detokenize(tokens[:keep]).decode(errors="ignore") + "\n[…testo troncato…]"
        return messages[:-1] + [last]

    # ---------------------------------------------------------------- vettori
    async def embed(self, texts: list[str]) -> list[list[float]]:
        def call(llm) -> list[list[float]]:
            out = []
            for t in texts:
                t = t[:MAX_EMBED_CHARS]
                tokens = llm.tokenize(t.encode(), add_bos=True)
                if len(tokens) > EMBED_CTX:
                    t = llm.detokenize(tokens[:EMBED_CTX - 2]).decode(errors="ignore")
                out.append(llm.embed(t, normalize=True))
            return out

        return await self._run(self.embed_model, True, call)
