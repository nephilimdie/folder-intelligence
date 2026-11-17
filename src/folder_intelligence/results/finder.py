"""Mettere i risultati nel Finder: etichetta (default) o cartella di alias (impostazioni)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .finder_tags import add_tag, remove_tag


def safe_name(name: str) -> str:
    name = re.sub(r"[/:\\\x00-\x1f]+", " ", name).strip(" .")
    return (name or "Risultati")[:60]


def aliases_root() -> Path:
    from .. import settings

    return Path(settings.load().aliases_folder).expanduser()


def _link_all(folder: Path, paths: list[str]) -> tuple[int, list[str]]:
    done, failed = 0, []
    for p in paths:
        link = folder / Path(p).name
        n = 2
        while link.exists() or link.is_symlink():
            if link.is_symlink() and os.readlink(link) == p:
                break
            link = folder / f"{Path(p).stem} ({n}){Path(p).suffix}"
            n += 1
        else:
            try:
                link.symlink_to(p)
                done += 1
            except OSError as e:
                failed.append(f"{Path(p).name}: {e}")
    return done, failed


def to_finder(paths: list[str], name: str, mode: str | None = None, open_finder: bool = True) -> dict[str, Any]:
    """Mette i file nel Finder: `tag` (etichetta) o `aliases` (cartella di collegamenti)."""
    from .. import settings

    mode = mode or settings.load().results_destination
    name = safe_name(name)
    if mode == "aliases":
        folder = aliases_root() / name
        folder.mkdir(parents=True, exist_ok=True)
        done, failed = _link_all(folder, paths)
        if open_finder:
            subprocess.run(["open", str(folder)], capture_output=True)
        return {"mode": "aliases", "name": name, "folder": str(folder), "added": done, "failed": failed}
    done, failed = 0, []
    for p in paths:
        try:
            done += add_tag(p, name)
        except OSError as e:
            failed.append(f"{Path(p).name}: {e}")
    return {"mode": "tag", "name": name, "added": done, "tagged": len(paths) - len(failed), "failed": failed}


def remove_from_finder(paths: list[str], name: str) -> int:
    return sum(1 for p in paths if Path(p).exists() and remove_tag(p, safe_name(name)))
