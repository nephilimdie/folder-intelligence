"""Classificazione file: regole locali (sempre disponibili) + Ollama (opzionale)."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Callable

from .models import FileInfo

DEFAULT_OLLAMA_URL = os.environ.get("FI_OLLAMA_URL", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("FI_OLLAMA_MODEL", "qwen2.5:14b")

CATEGORIES: dict[str, str] = {
    "documenti": "Documenti",
    "fatture_ricevute": "Documenti/Fatture e ricevute",
    "carte_certificati": "Documenti/Carte e certificati",
    "fogli_calcolo": "Documenti/Fogli di calcolo",
    "presentazioni": "Documenti/Presentazioni",
    "ebook": "Libri",
    "immagini": "Immagini",
    "screenshot": "Immagini/Screenshot",
    "video": "Video",
    "audio": "Audio",
    "archivi": "Archivi",
    "installer": "Installer",
    "codice": "Codice",
    "font": "Font",
    "altro": "Altro",
}

EXTENSIONS: dict[str, set[str]] = {
    "documenti": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages", ".tex"},
    "fogli_calcolo": {".xls", ".xlsx", ".ods", ".csv", ".tsv", ".numbers"},
    "presentazioni": {".ppt", ".pptx", ".odp", ".key"},
    "ebook": {".epub", ".mobi", ".azw", ".azw3", ".djvu"},
    "immagini": {".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp", ".bmp", ".tif",
                 ".tiff", ".svg", ".raw", ".cr2", ".nef", ".arw", ".dng", ".psd", ".ai"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".aiff", ".opus", ".wma"},
    "archivi": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".zst"},
    "installer": {".dmg", ".pkg", ".exe", ".msi", ".deb", ".rpm", ".apk", ".iso", ".appimage"},
    "codice": {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".go", ".rs",
               ".rb", ".php", ".swift", ".kt", ".sh", ".sql", ".html", ".css", ".json",
               ".yaml", ".yml", ".toml", ".xml", ".ipynb"},
    "font": {".ttf", ".otf", ".woff", ".woff2"},
}
_EXT_TO_CAT = {ext: cat for cat, exts in EXTENSIONS.items() for ext in exts}

# Parole chiave nel nome file che raffinano la categoria (solo per documenti/immagini).
NAME_KEYWORDS: list[tuple[str, set[str], tuple[str, ...]]] = [
    ("fatture_ricevute", {"documenti", "immagini"},
     ("fattura", "invoice", "ricevuta", "receipt", "scontrino", "bolletta", "f24", "estratto conto")),
    ("carte_certificati", {"documenti", "immagini"},
     ("carta identit", "carta_identit", "passaporto", "passport", "patente", "codice fiscale",
      "tessera sanitaria", "certificat", "diploma", "attestato")),
    ("screenshot", {"immagini"}, ("screenshot", "schermata", "screen shot", "cattura")),
]

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html",
                   ".rtf", ".tex", ".log", ".py", ".js", ".ts", ".sql", ".sh"}


class RuleClassifier:
    """Classificatore deterministico basato su estensione e nome file."""

    def classify(self, path: Path) -> dict[str, Any]:
        ext = path.suffix.lower()
        category = _EXT_TO_CAT.get(ext, "altro")
        name = re.sub(r"[_\-.]+", " ", path.stem.lower())
        reason = f"estensione {ext or '(nessuna)'}"

        for target, applies_to, words in NAME_KEYWORDS:
            if category in applies_to and any(w.replace("_", " ") in name for w in words):
                category = target
                reason = "nome file"
                break

        confidence = 0.9 if category != "altro" else 0.2
        return {"category": category, "description": f"Classificato per {reason}",
                "confidence": confidence, "source": "rules"}


def read_text_preview(path: Path, max_chars: int = 2000) -> str:
    """Estrae un'anteprima testuale. Per i PDF usa `pdftotext` se presente."""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return _pdf_preview(path, max_chars)
        if ext in TEXT_EXTENSIONS:
            with open(path, "rb") as f:
                return f.read(max_chars * 2).decode("utf-8", errors="ignore")[:max_chars]
    except OSError:
        pass
    return ""


def _pdf_preview(path: Path, max_chars: int) -> str:
    import shutil
    import subprocess

    if shutil.which("pdftotext"):
        try:
            out = subprocess.run(
                ["pdftotext", "-l", "2", "-q", str(path), "-"],
                capture_output=True, timeout=15, check=False,
            )
            return out.stdout.decode("utf-8", errors="ignore")[:max_chars]
        except (subprocess.SubprocessError, OSError):
            return ""
    return ""


from .engines.base import parse_json  # noqa: E402

_parse_json = parse_json  # compatibilità


_CLASSIFY_SYSTEM = (
    "Sei un assistente che organizza file su un Mac. Rispondi SOLO con JSON valido nella forma "
    '{"category": "<una tra: ' + ", ".join(CATEGORIES) + '>", '
    '"description": "<descrizione breve in italiano, max 15 parole>", "confidence": <0..1>}'
)


class FileClassifier:
    """Combina regole e (se disponibile) Ollama sul contenuto testuale."""

    def __init__(self, ollama: Any = None, concurrency: int = 2):
        self.rules = RuleClassifier()
        self.ollama = ollama
        self.concurrency = concurrency

    async def classify(self, info: FileInfo, use_ai: bool = True) -> FileInfo:
        result = self.rules.classify(info.path)
        if use_ai and self.ollama and await self.ollama.is_available():
            ai = await self._classify_ai(info.path)
            if ai:
                result = ai
        info.category = result["category"]
        info.description = result["description"]
        info.confidence = float(result["confidence"])
        info.source = result["source"]
        return info

    async def _classify_ai(self, path: Path) -> dict[str, Any] | None:
        preview = read_text_preview(path)
        if not preview.strip():
            return None
        prompt = f"Nome file: {path.name}\n\nAnteprima contenuto:\n{preview[:1500]}"
        try:
            data = await self.ollama.generate_json(prompt, system=_CLASSIFY_SYSTEM)  # type: ignore[union-attr]
        except Exception:
            return None
        category = str(data.get("category", "")).strip().lower()
        if category not in CATEGORIES:
            return None
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.7))))
        except (TypeError, ValueError):
            confidence = 0.7
        return {"category": category, "description": str(data.get("description", ""))[:200],
                "confidence": confidence, "source": "ollama"}

    async def classify_all(
        self, files: list[FileInfo], use_ai: bool = True, ai_limit: int = 50,
        progress: Callable[..., None] | None = None,
    ) -> list[FileInfo]:
        """Classifica tutti i file; l'AI viene usata solo sui primi `ai_limit` file testuali."""
        ai_enabled = use_ai and self.ollama is not None and await self.ollama.is_available()
        ai_budget = ai_limit if ai_enabled else 0
        sem = asyncio.Semaphore(self.concurrency)
        done = 0

        async def one(f: FileInfo, ai: bool) -> None:
            nonlocal done
            async with sem:
                await self.classify(f, use_ai=ai)
            done += 1
            if progress:
                progress("classify", done, len(files), f.name)

        if progress:
            progress("classify", 0, len(files))
        tasks = []
        for f in files:
            ai = ai_budget > 0 and (f.extension in TEXT_EXTENSIONS or f.extension == ".pdf")
            if ai:
                ai_budget -= 1
            tasks.append(one(f, ai))
        await asyncio.gather(*tasks)
        return files
