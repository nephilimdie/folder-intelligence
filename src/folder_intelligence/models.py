"""Strutture dati del dominio."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class FileInfo:
    """Informazioni su un singolo file, con eventuale classificazione."""

    path: Path
    size_bytes: int
    modified: float
    category: str = "altro"
    description: str = ""
    confidence: float = 0.0
    source: str = ""  # "rules" | "ollama" | "index"
    sha256: str = ""
    cloud_only: bool = False  # macOS: file solo su iCloud (contenuto non scaricato)
    device: int = 0
    inode: int = 0
    storage: str = ""  # id dello spazio fisico occupato (uguale per hardlink e cloni APFS)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def modified_iso(self) -> str:
        return datetime.fromtimestamp(self.modified).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        data["name"] = self.name
        data["extension"] = self.extension
        data["modified"] = self.modified_iso
        return data

    @classmethod
    def from_path(cls, path: Path) -> "FileInfo":
        st = path.stat()
        return cls(path=path, size_bytes=st.st_size, modified=st.st_mtime,
                   cloud_only=bool(getattr(st, "st_flags", 0) & 0x40000000),
                   device=st.st_dev, inode=st.st_ino)


@dataclass
class DuplicateGroup:
    """Gruppo di file con contenuto identico. Il primo è l'originale da tenere."""

    sha256: str
    files: list[FileInfo] = field(default_factory=list)

    @property
    def keep(self) -> FileInfo:
        return self.files[0]

    @property
    def extras(self) -> list[FileInfo]:
        return self.files[1:]

    @property
    def size_bytes(self) -> int:
        return self.files[0].size_bytes if self.files else 0

    @property
    def wasted_bytes(self) -> int:
        """Spazio davvero recuperabile: hardlink e cloni APFS non occupano spazio in più."""
        physical = {f.storage or f"{f.device}:{f.inode}:{id(f)}" for f in self.files}
        return self.size_bytes * max(0, len(physical) - 1)

    def shares_storage(self, f: FileInfo) -> bool:
        """True se `f` occupa lo stesso spazio fisico di un altro file del gruppo."""
        return bool(f.storage) and any(o is not f and o.storage == f.storage for o in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "wasted_bytes": self.wasted_bytes,
            "keep": str(self.keep.path),
            "duplicates": [str(f.path) for f in self.extras],
            "shared_storage": [str(f.path) for f in self.extras if self.shares_storage(f)],
        }


@dataclass
class ScanResult:
    """Risultato di una scansione di cartella."""

    root: Path
    files: list[FileInfo]
    duplicates: list[DuplicateGroup]
    errors: list[str] = field(default_factory=list)
    classified: bool = False
    duplicates_checked: bool = False
    scanned_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    created: float = field(default_factory=time.time)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def wasted_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.duplicates)

    @property
    def duplicate_file_count(self) -> int:
        return sum(len(g.extras) for g in self.duplicates)

    @property
    def cloud_only_count(self) -> int:
        return sum(1 for f in self.files if f.cloud_only)

    def by_category(self) -> dict[str, list[FileInfo]]:
        out: dict[str, list[FileInfo]] = {}
        for f in self.files:
            out.setdefault(f.category, []).append(f)
        return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "scanned_at": self.scanned_at,
            "summary": {
                "files": len(self.files),
                "total_bytes": self.total_bytes,
                "cloud_only_files": self.cloud_only_count,
                "duplicates_checked": self.duplicates_checked,
                "duplicate_groups": len(self.duplicates),
                "duplicate_files": self.duplicate_file_count,
                "wasted_bytes": self.wasted_bytes,
                "categories": {k: len(v) for k, v in self.by_category().items()},
            },
            "files": [f.to_dict() for f in self.files],
            "duplicates": [g.to_dict() for g in self.duplicates],
            "errors": self.errors,
        }


def format_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
