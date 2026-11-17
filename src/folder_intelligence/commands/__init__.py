"""Comandi (skill) della chat: un file = un comando. Vedi base.py per crearne di nuovi."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..intents import Intent, extract_paths, normalize
from .base import Command, CommandResult, Request

_REGISTRY: "CommandRegistry | None" = None


class CommandRegistry:
    def __init__(self, commands: list[Command]) -> None:
        self.commands = sorted(commands, key=lambda c: (c.priority, c.name))
        self.by_name = {c.name: c for c in self.commands}

    def get(self, name: str) -> Command | None:
        return self.by_name.get(name)

    def parse(self, text: str, home: Path | None = None) -> Intent:
        norm = normalize(text)
        req = Request(text=text, norm=norm, words=set(re.findall(r"\w+", norm)),
                      paths=extract_paths(text, home))
        if not norm:
            return Intent("unknown")
        for cmd in self.commands:
            args = cmd.match(req)
            if args is None:
                continue
            path = args.pop("path") if "path" in args else (req.paths[0] if req.paths else None)
            target = args.pop("target") if "target" in args else (req.paths[1] if len(req.paths) > 1 else None)
            return Intent(cmd.name, path=path, target=target, args=args)
        return Intent("unknown", path=req.paths[0] if req.paths else None)

    def llm_system_prompt(self) -> str:
        lines = [f'- {c.name}: {c.description}' + (f' (es. "{c.examples[0]}")' if c.examples else "")
                 for c in self.commands if not c.hidden and c.name not in ("exit", "unknown")]
        return ("Sei il router di un assistente che gestisce file sul Mac. Scegli il comando più "
                "adatto alla frase dell'utente. Comandi disponibili:\n" + "\n".join(lines) +
                '\nRispondi SOLO con JSON: {"command": "<nome>", "path": "<percorso o null>", '
                '"query": "<testo da cercare/domanda/nota o null>"}. Se nessuno è adatto usa "unknown".')

    def from_llm(self, data: dict[str, Any]) -> Intent:
        name = str(data.get("command") or data.get("type") or "unknown")
        if name not in self.by_name or name == "exit":
            name = "unknown"
        raw = data.get("path")
        path = Path(str(raw)).expanduser() if raw and str(raw) not in ("null", "None") else None
        args: dict[str, Any] = {}
        q = data.get("query")
        if q and str(q) not in ("null", "None"):
            args.update(query=str(q), text=str(q))
        return Intent(name, path=path, args=args)

    def help_text(self) -> str:
        lines = ["Ecco cosa posso fare (scrivi in linguaggio naturale):"]
        for c in sorted(self.commands, key=lambda c: (c.name in ("help", "exit"), c.priority)):
            if c.hidden or not c.examples:
                continue
            lines.append(f"\n  • {c.description}")
            lines.append("      " + "  ·  ".join(f'"{e}"' for e in c.examples[:2]))
        lines.append("\nTab completa cartelle e comandi · ↑/↓ comandi precedenti · Ctrl+C interrompe")
        return "\n".join(lines)


def registry(reload: bool = False) -> CommandRegistry:
    global _REGISTRY
    if _REGISTRY is None or reload:
        from ..plugins import load

        _REGISTRY = CommandRegistry(load(__name__, Command, user_dir="commands"))
    return _REGISTRY


__all__ = ["Command", "CommandResult", "Request", "CommandRegistry", "registry"]
