"""Registrazione della comprensione dei file nella memoria MCP condivisa (local-memory).

La memoria MCP è condivisa con TUTTI gli agenti (Claude Code, Codex, …) e alcuni di
questi inviano il contesto a modelli cloud; inoltre il suo database non è cifrato da
questa app. Per questo esistono tre modalità (`FI_MCP_MODE` o `--mcp-mode`):

- `sealed` (default) → per ogni cartella indicizzata un solo "inventario" senza contenuti:
  quanti file per categoria/tipo e periodo. Nessun nome, titolo, testo o percorso interno.
  I dettagli restano nell'indice locale cifrato di folder-intelligence.
- `summary` → anche una memoria per file con titolo, tipo, descrizione e tag *ripuliti*
  dai dati personali strutturati (email, IBAN, CF, telefoni…). I nomi propri possono restare.
- `off` → nessuna scrittura.

Le memorie vengono aggiornate con `supersedes`, così una nuova analisi sostituisce la
precedente invece di duplicarla. Gli id sono conservati in uno stato locale cifrato.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from . import crypto
from .audit import app_home
from .privacy import redact

MODES = ("off", "sealed", "summary")
DEFAULT_URL = os.environ.get("FI_MCP_URL", "http://127.0.0.1:8000")
DEFAULT_NAMESPACE = os.environ.get("FI_MCP_NAMESPACE", "files:folder-intelligence")
PURPOSE = "mcp-state"


def default_mode() -> str:
    mode = os.environ.get("FI_MCP_MODE", "sealed").lower()
    return mode if mode in MODES else "sealed"


def _display_root(root: Path) -> str:
    try:
        return "~/" + str(root.relative_to(Path.home()))
    except ValueError:
        return root.name


class McpMemory:
    def __init__(self, mode: str | None = None, url: str = DEFAULT_URL,
                 namespace: str = DEFAULT_NAMESPACE) -> None:
        self.mode = mode or default_mode()
        self.url = url.rstrip("/")
        self.namespace = namespace
        self._state_path = app_home() / "mcp_state.enc"
        self._state: dict[str, str] | None = None
        self.last_error = ""

    # ------------------------------------------------------------- stato locale
    def _load(self) -> dict[str, str]:
        if self._state is None:
            try:
                self._state = json.loads(crypto.read_encrypted(self._state_path, PURPOSE)) \
                    if self._state_path.exists() else {}
            except (crypto.CryptoError, json.JSONDecodeError):
                self._state = {}
        return self._state

    def _save(self) -> None:
        crypto.write_encrypted(self._state_path, json.dumps(self._load()).encode(), PURPOSE)

    @staticmethod
    def ref(path: str | Path) -> str:
        """Identificativo opaco di un file/cartella (HMAC con la chiave master: non rivela il percorso)."""
        return hmac.new(crypto.master_key(), str(path).encode(), hashlib.sha256).hexdigest()[:16]

    # ------------------------------------------------------------------ rete
    async def available(self) -> bool:
        if self.mode == "off":
            return False
        import aiohttp

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as s:
                async with s.get(f"{self.url}/health") as r:
                    return r.status == 200
        except Exception:
            return False

    async def _remember(self, text: str, key: str, kind: str = "fact") -> str | None:
        import aiohttp

        state = self._load()
        payload: dict[str, Any] = {
            "text": text[:15000], "namespace": self.namespace, "type": kind,
            "source": "folder-intelligence", "confidence": 0.9,
        }
        if state.get(key):
            payload["supersedes"] = state[key]
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.post(f"{self.url}/remember", json=payload) as r:
                    data = await r.json() if r.status < 300 else {}
                    if r.status >= 300:
                        self.last_error = f"HTTP {r.status}: {str(data)[:200]}"
                        return None
        except Exception as e:
            self.last_error = str(e)
            return None
        mem_id = data.get("id")
        if mem_id:
            state[key] = mem_id
        return mem_id

    # ---------------------------------------------------------------- sync
    async def sync_folder(self, root: Path, docs: list[dict[str, Any]]) -> dict[str, Any]:
        """Registra nella memoria MCP la comprensione della cartella secondo la modalità."""
        result = {"mode": self.mode, "written": 0, "error": ""}
        if self.mode == "off" or not docs:
            return result
        if not await self.available():
            result["error"] = f"memoria MCP non raggiungibile su {self.url}"
            return result

        when = datetime.now().strftime("%Y-%m-%d")
        cats = Counter(d.get("category") or "altro" for d in docs)
        types = Counter((d.get("metadata") or {}).get("doc_type") for d in docs
                        if (d.get("metadata") or {}).get("doc_type"))
        years = sorted({str((d.get("metadata") or {}).get("date"))[:4] for d in docs
                        if str((d.get("metadata") or {}).get("date") or "")[:4].isdigit()})
        inventory = (
            f"[folder-intelligence] Inventario dei contenuti di {_display_root(root)} "
            f"(analisi del {when}, {len(docs)} file compresi). "
            f"Categorie: {', '.join(f'{c} {n}' for c, n in cats.most_common())}. "
            + (f"Tipi di documento: {', '.join(f'{t} {n}' for t, n in types.most_common(15))}. " if types else "")
            + (f"Periodo: {years[0]}–{years[-1]}. " if years else "")
            + "I dettagli sono nell'indice cifrato locale di folder-intelligence: "
              "per cercarli usa la chat o le API di folder-intelligence."
        )
        if await self._remember(redact(inventory), f"folder:{self.ref(root)}", "project"):
            result["written"] += 1

        if self.mode == "summary":
            sem = asyncio.Semaphore(4)

            async def one(d: dict[str, Any]) -> None:
                meta = d.get("metadata") or {}
                folder = Path(d["path"]).parent
                text = (f"[folder-intelligence] File in {_display_root(folder)}: "
                        f"«{d.get('title') or d.get('name')}» ({meta.get('doc_type') or d.get('category')}"
                        + (f", {meta.get('date')}" if meta.get("date") else "") + "). "
                        f"{d.get('description') or ''} "
                        + (f"Tag: {', '.join(d.get('tags') or [])}." if d.get("tags") else ""))
                async with sem:
                    if await self._remember(redact(text), f"file:{self.ref(d['path'])}", "fact"):
                        result["written"] += 1

            await asyncio.gather(*(one(d) for d in docs))

        self._save()
        if self.last_error and not result["written"]:
            result["error"] = self.last_error
        return result
