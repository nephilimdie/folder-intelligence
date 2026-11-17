"""Tabella finale del banco: risposte giuste per campo e secondi per file, per combinazione."""

from __future__ import annotations

from collections import defaultdict

from ..i18n import _
from .bench_result import BenchResult


def report(results: list[BenchResult], skipped: list[str]) -> str:
    by_config: dict[str, list[BenchResult]] = defaultdict(list)
    for r in results:
        by_config[r.config].append(r)
    fields = sorted({f for r in results for f in r.checks if not f.startswith("«")}) + ["words"]
    header = f"{_('combinazione'):<28}" + "".join(f"{f:>9}" for f in fields) + f"{_('totale'):>9}{'s/file':>8}"
    lines = [header, "─" * len(header)]
    for name, rows in by_config.items():
        cells = []
        for f in fields:
            checks = [ok for r in rows for k, ok in r.checks.items() if (k == f or (f == "words" and k.startswith("«")))]
            cells.append(f"{(100 * sum(checks) // len(checks)) if checks else '—':>8}" + ("%" if checks else " "))
        correct, total = sum(r.correct for r in rows), sum(r.total for r in rows)
        timed = [r.seconds for r in rows if not r.error]
        lines.append(f"{name:<28}" + "".join(cells) + f"{(100 * correct // total) if total else 0:>8}%"
                     + f"{(sum(timed) / len(timed)) if timed else 0:>8.1f}")
    wrong = [(r, [k for k, ok in r.checks.items() if not ok]) for r in results]
    wrong = [(r, keys) for r, keys in wrong if keys]
    if wrong:
        lines.append("")
        lines.append(_("Risposte sbagliate:"))
        lines += [f"  {r.config} · {r.file}: {', '.join(keys)}" for r, keys in wrong[:20]]
    errors = [r for r in results if r.error]
    if errors:
        lines.append("")
        lines += [f"❗ {r.config} · {r.file}: {r.error}" for r in errors[:10]]
    if skipped:
        lines.append("")
        lines.append(_("Saltate (modelli non installati): {list}", list="; ".join(skipped)))
    return "\n".join(lines)
