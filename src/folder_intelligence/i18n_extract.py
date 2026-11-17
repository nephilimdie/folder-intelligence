"""Estrae dal codice i testi da tradurre (argomenti di _(), _n(), N_()) e le categorie.

    python -m folder_intelligence.i18n_extract            # elenco dei testi
    python -m folder_intelligence.i18n_extract --missing en  # testi senza traduzione inglese
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PACKAGE = Path(__file__).parent
WRAPPERS = {"_": 1, "N_": 1, "_n": 2}


def messages() -> dict[str, list[str]]:
    """{testo: [file:riga, …]} per ogni testo da tradurre."""
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in ("i18n.py", "i18n_extract.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in WRAPPERS:
                for arg in node.args[: WRAPPERS[node.func.id]]:
                    text = _literal(arg)
                    if text is not None:
                        found.setdefault(text, []).append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    from .classifier import CATEGORIES

    for cat, folder in CATEGORIES.items():  # identificatori e nomi delle cartelle per categoria
        found.setdefault(cat, []).append("classifier.py:CATEGORIES")
        found.setdefault(folder, []).append("classifier.py:CATEGORIES")
    return found


def _literal(node: ast.AST) -> str | None:
    """Stringa costante (anche concatenazione implicita); None se dinamica."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        return left + right if left is not None and right is not None else None
    return None


def dynamic_calls() -> list[str]:
    """Chiamate _() con argomento non letterale (non estraibili): devono essere identificatori già tradotti."""
    out = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in ("i18n.py", "i18n_extract.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in WRAPPERS:
                if node.args and _literal(node.args[0]) is None:
                    out.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    return out


def main(argv: list[str]) -> int:
    msgs = messages()
    if len(argv) == 2 and argv[0] == "--missing":
        catalog = json.loads((PACKAGE / "locales" / f"{argv[1]}.json").read_text(encoding="utf-8"))
        missing = {m: w for m, w in msgs.items() if m not in catalog}
        print(json.dumps({m: "" for m in missing}, ensure_ascii=False, indent=2))
        return 1 if missing else 0
    for m, where in msgs.items():
        print(f"{where[0]:<40} {m!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
