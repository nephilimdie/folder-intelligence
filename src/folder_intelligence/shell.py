"""Esecuzione sicura di comandi di sistema (macOS/Linux) per comandi ed estrattori.

Regole:
- mai `shell=True`: gli argomenti sono sempre una lista, quindi il testo dell'utente
  non può iniettare altri comandi;
- timeout obbligatorio; output troncato;
- `which()` permette ai plugin di degradare con eleganza se un tool manca.
"""

from __future__ import annotations

from .i18n import _

import asyncio
import json
import os
import platform
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_OUTPUT = 2_000_000
IS_MAC = platform.system() == "Darwin"


@dataclass
class ShellResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


@lru_cache(maxsize=None)
def which(name: str) -> str | None:
    return shutil.which(name)


def tool_env() -> dict[str, str]:
    """Ambiente per i tool esterni, con correzioni note (es. TESSDATA_PREFIX errato)."""
    env = dict(os.environ)
    prefix = env.get("TESSDATA_PREFIX")
    if not prefix or not any(Path(prefix).glob("*.traineddata")):
        for cand in ("/opt/homebrew/share/tessdata", "/usr/local/share/tessdata",
                     "/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata"):
            if any(Path(cand).glob("*.traineddata")):
                env["TESSDATA_PREFIX"] = cand
                break
        else:
            env.pop("TESSDATA_PREFIX", None)
    return env


async def run(args: list[str], timeout: float = 60, cwd: str | Path | None = None,
              binary: bool = False) -> ShellResult:
    """Esegue un comando e ne restituisce l'output (testo). Non solleva su exit code != 0."""
    if not args or which(args[0]) is None:
        return ShellResult(127, "", _("comando non trovato: {cmd}", cmd=args[0] if args else ""))
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None, env=tool_env(),
        )
    except OSError as e:
        return ShellResult(126, "", str(e))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ShellResult(124, "", f"timeout dopo {timeout}s")
    return ShellResult(
        proc.returncode or 0,
        out[:MAX_OUTPUT].decode("utf-8", errors="ignore"),
        err[:10_000].decode("utf-8", errors="ignore"),
    )


# ---------------------------------------------------------------- helper comuni
async def mdfind(query: str, onlyin: Path | None = None, limit: int = 200) -> list[Path]:
    """Ricerca Spotlight (contenuto + metadati indicizzati da macOS)."""
    if not IS_MAC or not which("mdfind"):
        return []
    args = ["mdfind"]
    if onlyin:
        args += ["-onlyin", str(onlyin)]
    args.append(query)
    res = await run(args, timeout=20)
    return [Path(p) for p in res.stdout.splitlines()[:limit] if p]


MDLS_KEYS = [
    "kMDItemKind", "kMDItemContentType", "kMDItemWhereFroms", "kMDItemContentCreationDate",
    "kMDItemTitle", "kMDItemAuthors", "kMDItemCreator", "kMDItemNumberOfPages",
    "kMDItemPixelWidth", "kMDItemPixelHeight", "kMDItemDurationSeconds", "kMDItemAlbum",
    "kMDItemMusicalGenre", "kMDItemLatitude", "kMDItemLongitude", "kMDItemAcquisitionModel",
]


async def mdls(path: Path) -> dict[str, Any]:
    """Metadati Spotlight di un file (provenienza download, autore, pagine, GPS…)."""
    if not IS_MAC or not which("mdls"):
        return {}
    args = ["mdls"]
    for k in MDLS_KEYS:
        args += ["-name", k]
    args.append(str(path))
    res = await run(args, timeout=10)
    return parse_mdls(res.stdout)


def parse_mdls(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    key, buf = None, []
    for line in text.splitlines():
        if key is not None:
            if line.strip() == ")":
                out[key] = [v.strip().strip(",").strip('"') for v in buf if v.strip()]
                key, buf = None, []
            else:
                buf.append(line)
            continue
        if "=" not in line:
            continue
        k, v = (s.strip() for s in line.split("=", 1))
        if v == "(":
            key = k.replace("kMDItem", "")
            continue
        if v == "(null)":
            continue
        out[k.replace("kMDItem", "")] = v.strip('"')
    return out


async def file_type(path: Path) -> str:
    res = await run(["file", "-b", str(path)], timeout=10)
    return res.stdout.strip()


async def ffprobe(path: Path) -> dict[str, Any]:
    res = await run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
                     "-show_streams", str(path)], timeout=30)
    try:
        return json.loads(res.stdout) if res.ok else {}
    except json.JSONDecodeError:
        return {}
