"""Etichette del Finder (xattr `_kMDItemUserTags`): il file non viene spostato né modificato."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

TAG_XATTR = "com.apple.metadata:_kMDItemUserTags"


def read_tags(path: str | Path) -> list[str]:
    r = subprocess.run(["xattr", "-px", TAG_XATTR, str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    try:
        raw = bytes.fromhex(r.stdout.replace("\n", "").replace(" ", ""))
        return [str(t) for t in plistlib.loads(raw)]
    except Exception:
        return []


def write_tags(path: str | Path, tags: list[str]) -> None:
    data = plistlib.dumps(tags, fmt=plistlib.FMT_BINARY).hex()
    r = subprocess.run(["xattr", "-wx", TAG_XATTR, data, str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "xattr failed")


def add_tag(path: str | Path, tag: str) -> bool:
    """Aggiunge l'etichetta del Finder. True se è nuova."""
    current = read_tags(path)
    if any(t.split("\n")[0] == tag for t in current):
        return False
    write_tags(path, [*current, f"{tag}\n0"])
    return True


def remove_tag(path: str | Path, tag: str) -> bool:
    current = read_tags(path)
    kept = [t for t in current if t.split("\n")[0] != tag]
    if kept == current:
        return False
    write_tags(path, kept)
    return True
