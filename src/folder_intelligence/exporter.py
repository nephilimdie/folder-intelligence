"""Esportazione report di scansione in JSON, Markdown e CSV."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .i18n import _
from .models import ScanResult, format_size

FORMATS = ("json", "md", "csv")


def to_json(result: ScanResult) -> str:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


def to_csv(result: ScanResult) -> str:
    dup_of: dict[str, str] = {}
    for g in result.duplicates:
        for f in g.extras:
            dup_of[str(f.path)] = str(g.keep.path)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["path", "name", "extension", "size_bytes", "modified", "category",
                "confidence", "source", "description", "duplicate_of"])
    for f in result.files:
        w.writerow([f.path, f.name, f.extension, f.size_bytes, f.modified_iso, f.category,
                    f"{f.confidence:.2f}", f.source, f.description, dup_of.get(str(f.path), "")])
    return buf.getvalue()


def to_markdown(result: ScanResult) -> str:
    lines = [
        f"# Report folderIntelligence — `{result.root}`",
        "",
        "_" + _("Scansione del {when}", when=result.scanned_at) + "_",
        "",
        "## " + _("Riepilogo"),
        "",
        "| " + _("Voce") + " | " + _("Valore") + " |",
        "|---|---|",
        f"| {_('File analizzati')} | {len(result.files)} |",
        f"| {_('Spazio totale')} | {format_size(result.total_bytes)} |",
        f"| {_('Gruppi di duplicati')} | {len(result.duplicates)} |",
        f"| {_('Copie in eccesso')} | {result.duplicate_file_count} |",
        f"| {_('Spazio recuperabile')} | {format_size(result.wasted_bytes)} |",
        "",
        "## " + _("Categorie"),
        "",
        "| " + _("Categoria") + " | " + _("File") + " | " + _("Dimensione") + " |",
        "|---|---:|---:|",
    ]
    for cat, files in result.by_category().items():
        lines.append(f"| {_(cat)} | {len(files)} | {format_size(sum(f.size_bytes for f in files))} |")

    lines += ["", "## " + _("Duplicati"), ""]
    if not result.duplicates:
        lines.append(_("Nessun duplicato trovato."))
    for i, g in enumerate(result.duplicates, 1):
        lines.append(f"### {i}. {g.keep.name} — {format_size(g.size_bytes)} × {len(g.files)}")
        lines.append("")
        lines.append("- ✅ " + _("tieni: {path}", path=f"`{g.keep.path}`"))
        for f in g.extras:
            lines.append("- 🗑️ " + _("copia: {path}", path=f"`{f.path}`"))
        lines.append("")

    if result.errors:
        lines += ["## " + _("Errori"), ""] + [f"- {e}" for e in result.errors]
    return "\n".join(lines) + "\n"


def export(result: ScanResult, fmt: str, output: Path | None = None) -> Path:
    fmt = fmt.lower().lstrip(".")
    if fmt == "markdown":
        fmt = "md"
    renderers = {"json": to_json, "md": to_markdown, "csv": to_csv}
    if fmt not in renderers:
        raise ValueError(_("Formato non supportato: {fmt} (usa {formats})", fmt=fmt, formats=", ".join(FORMATS)))
    if output is None:
        output = Path.cwd() / f"folder-report-{result.root.name or 'root'}.{fmt}"
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(renderers[fmt](result), encoding="utf-8")
    return output
