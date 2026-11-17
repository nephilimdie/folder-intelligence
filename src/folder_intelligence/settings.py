"""Impostazioni dell'app (pagina "impostazioni" nella chat, GET/PUT /settings nelle API).

Salvate cifrate in `settings.enc` (contengono percorsi personali). Le variabili d'ambiente FI_*
hanno sempre la precedenza: servono per test, script e configurazioni avanzate.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import crypto
from .audit import app_home

PURPOSE = "settings"
_lock = threading.RLock()
_cache: tuple[str, float, "Settings"] | None = None

IDLE_CHOICES = (2, 5, 10, 15, 30)
LOAD_CHOICES = ("low", "medium", "any")     # quanto deve essere "libero" il Mac
LANG_CHOICES = ("auto", "it", "en")
MCP_CHOICES = ("sealed", "summary", "off")
INDEX_MODEL_CHOICES = ("qwen2.5:7b", "qwen3.5:9b", "qwen2.5:14b")
CHAT_MODEL_CHOICES = ("qwen2.5:14b", "qwen3.5:9b")
VISION_MODEL_CHOICES = ("qwen3.5:9b", "qwen3-vl:8b")
DOCUMENT_MODEL_CHOICES = ("qwen3-vl:8b", "qwen3.5:9b")
OCR_ENGINE_CHOICES = ("auto", "apple", "tesseract", "model")
EMBED_MODEL_CHOICES = ("bge-m3", "qwen3-embedding:0.6b", "qwen3-embedding:4b")
DESTINATION_CHOICES = ("tag", "aliases")     # risultati nel Finder: etichetta o cartella di alias


@dataclass
class Settings:
    # indicizzazione automatica in background
    watched_folders: list[str] = field(default_factory=list)
    background_enabled: bool = False
    idle_minutes: int = 5
    require_ac_power: bool = True
    max_system_load: str = "low"           # "low": il Mac non sta facendo altro; "medium"; "any"
    # AI e integrazioni
    index_model: str = "qwen2.5:7b"
    chat_model: str = "qwen2.5:14b"        # chat, domande e router dei comandi
    vision_model: str = "qwen3.5:9b"       # foto
    document_model: str = "qwen3-vl:8b"    # documenti fotografati/scansionati (se installato)
    ocr_engine: str = "auto"               # auto = Apple Vision, riserva Tesseract
    ocr_model: str = "glm-ocr"             # modello OCR quando ocr_engine = "model"
    embed_model: str = "bge-m3"            # ricerca per significato (cambiarlo ricalcola i vettori)
    calendar: str = "folderIntelligence"   # "off" per non creare eventi
    language: str = "auto"
    mcp_mode: str = "sealed"
    # risultati delle ricerche nel Finder
    results_destination: str = "tag"
    aliases_folder: str = "~/Risultati"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def path() -> Path:
    return app_home() / "settings.enc"


def load() -> Settings:
    global _cache
    p = path()
    with _lock:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return Settings()
        if _cache and _cache[0] == str(p) and _cache[1] == mtime:
            return _cache[2]
        try:
            raw = json.loads(crypto.read_encrypted(p, PURPOSE))
        except Exception:
            return Settings()
        known = {f.name for f in fields(Settings)}
        s = Settings(**{k: v for k, v in raw.items() if k in known})
        _cache = (str(p), mtime, s)
        return s


def save(s: Settings) -> Settings:
    global _cache
    with _lock:
        crypto.write_encrypted(path(), json.dumps(asdict(s), ensure_ascii=False).encode(), PURPOSE)
        _cache = (str(path()), path().stat().st_mtime, s)
    return s


def update(**changes: Any) -> Settings:
    """Aggiorna e salva; valida i valori (scelte ammesse, cartelle esistenti)."""
    with _lock:
        s = load()
        for k, v in changes.items():
            if not hasattr(s, k):
                raise KeyError(k)
            setattr(s, k, v)
        validate(s)
        return save(s)


def validate(s: Settings) -> None:
    if s.idle_minutes not in IDLE_CHOICES:
        s.idle_minutes = min(IDLE_CHOICES, key=lambda m: abs(m - int(s.idle_minutes or 5)))
    if s.max_system_load not in LOAD_CHOICES:
        s.max_system_load = "low"
    if s.language not in LANG_CHOICES:
        s.language = "auto"
    if s.mcp_mode not in MCP_CHOICES:
        s.mcp_mode = "sealed"
    if s.ocr_engine not in OCR_ENGINE_CHOICES:
        s.ocr_engine = "auto"
    for name, default in (("chat_model", "qwen2.5:14b"), ("vision_model", "qwen3.5:9b"),
                          ("document_model", "qwen3-vl:8b"), ("ocr_model", "glm-ocr"), ("embed_model", "bge-m3")):
        setattr(s, name, str(getattr(s, name) or default).strip())
    if s.results_destination not in DESTINATION_CHOICES:
        s.results_destination = "tag"
    s.aliases_folder = s.aliases_folder or "~/Risultati"
    s.calendar = (s.calendar or "folderIntelligence").strip()
    folders: list[str] = []
    for f in s.watched_folders:
        p = str(Path(f).expanduser().resolve())
        if p not in folders:
            folders.append(p)
    s.watched_folders = folders


def add_folder(folder: str | Path) -> Settings:
    p = Path(folder).expanduser().resolve()
    if not p.is_dir():
        raise NotADirectoryError(str(p))
    s = load()
    return update(watched_folders=[*s.watched_folders, str(p)])


def remove_folder(folder: str | Path) -> Settings:
    p = str(Path(folder).expanduser().resolve())
    s = load()
    return update(watched_folders=[f for f in s.watched_folders if f != p])


# ------------------------------------------------- valori effettivi (env > impostazioni)
def env_or(name: str, value: Any) -> Any:
    v = os.environ.get(name)
    return v if v not in (None, "") else value


def idle_minutes() -> float:
    try:
        return float(env_or("FI_IDLE_MINUTES", load().idle_minutes))
    except ValueError:
        return 5.0


def index_model() -> str | None:
    return env_or("FI_INDEX_MODEL", load().index_model) or None


def chat_model() -> str:
    return str(env_or("FI_OLLAMA_MODEL", load().chat_model))


def vision_model() -> str:
    return str(env_or("FI_VISION_MODEL", load().vision_model))


def document_model() -> str:
    return str(env_or("FI_DOCUMENT_MODEL", load().document_model))


def ocr_engine() -> str:
    return str(env_or("FI_OCR_ENGINE", load().ocr_engine))


def ocr_model() -> str:
    return str(env_or("FI_OCR_MODEL", load().ocr_model))


def embed_model() -> str:
    return str(env_or("FI_EMBED_MODEL", load().embed_model))


def calendar_name() -> str:
    return str(env_or("FI_CALENDAR", load().calendar)).strip() or "folderIntelligence"


def language() -> str | None:
    lang = load().language
    return None if lang == "auto" else lang


def mcp_mode() -> str:
    return str(env_or("FI_MCP_MODE", load().mcp_mode))


def system_is_free(level: str | None = None) -> bool:
    """Il Mac "non sta facendo altro": carico medio dell'ultimo minuto basso rispetto ai core."""
    level = level or load().max_system_load
    if level == "any":
        return True
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        return True
    cores = os.cpu_count() or 4
    limit = 0.25 if level == "low" else 0.6
    return load1 <= cores * limit
