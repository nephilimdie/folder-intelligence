"""Router: dalla frase dell'utente a un *piano* di comandi, deciso dall'AI locale.

Flusso:
1. **scorciatoie** senza AI: risposte sì/no a una domanda in sospeso, "esci", "aiuto",
   "annulla", "ferma N"… (immediate e sempre prevedibili);
2. **AI**: il modello riceve il catalogo dei comandi (raggruppati, con argomenti) e restituisce
   un piano JSON con 1-4 passi in ordine, vincolato da uno schema (nomi di comando ammessi);
3. **validazione**: comandi inesistenti scartati, argomenti filtrati, al massimo un comando che
   modifica file e sempre per ultimo (chiederà comunque conferma);
4. **riserva**: se l'AI non è disponibile o non risponde, si usano le regole a parole chiave.

Il piano viene eseguito da `Assistant.handle` un passo alla volta, nello stesso contesto
(cartella corrente, scansioni, indice), così i passi successivi riusano il lavoro dei precedenti.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .commands.base import PARAM_DOCS
from .i18n import _, language_name
from .intents import Intent

if TYPE_CHECKING:
    from .commands import CommandRegistry

MAX_STEPS = 4
ROUTER_TIMEOUT = 25.0
# comandi che non passano mai dall'AI: devono essere immediati e prevedibili
SHORTCUTS = {"exit", "help", "confirm", "cancel", "history", "change_dir"}
# comandi che il router non deve proporre (gestiti dalle scorciatoie o interni)
NOT_FOR_AI = {"exit", "confirm", "cancel", "unknown"}


@dataclass
class RoutePlan:
    steps: list[Intent]
    source: str                      # "shortcut" | "ai" | "rules"
    reply: str = ""                  # risposta diretta dell'AI quando non serve nessun comando
    notes: list[str] = field(default_factory=list)   # passi scartati, correzioni
    elapsed: float = 0.0

    @property
    def multi(self) -> bool:
        return len(self.steps) > 1

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "reply": self.reply, "notes": self.notes,
                "elapsed_s": round(self.elapsed, 2),
                "steps": [{"command": s.type, "path": str(s.path) if s.path else None,
                           "target": str(s.target) if s.target else None, "args": s.args}
                          for s in self.steps]}


def router_mode() -> str:
    """FI_ROUTER: 'ai' (predefinito: AI con riserva a regole) | 'rules' (solo parole chiave)."""
    mode = os.environ.get("FI_ROUTER", "ai").lower()
    return mode if mode in ("ai", "rules") else "ai"


class Router:
    def __init__(self, registry: "CommandRegistry", ai: Any = None, model: str | None = None,
                 mode: str | None = None) -> None:
        self.registry = registry
        self.ai = ai
        self.model = model or os.environ.get("FI_ROUTER_MODEL") or (ai.model if ai else None)
        self.mode = mode or router_mode()

    # ------------------------------------------------------------------ prompt
    def catalog(self) -> str:
        groups: dict[str, list[str]] = {}
        for c in self.registry.commands:
            if c.hidden or c.name in NOT_FOR_AI:
                continue
            params = ", ".join(f"{p}: {c.param_docs.get(p) or PARAM_DOCS.get(p, p)}" for p in c.params)
            line = f"  - {c.name}: {_(c.description)}"
            if params:
                line += f" [args: {params}]"
            if c.destructive:
                line += " (MODIFIES FILES: asks for confirmation)"
            if c.examples:
                line += f' — e.g. "{_(c.examples[0])}"'
            groups.setdefault(c.group, []).append(line)
        return "\n".join(f"{g}:\n" + "\n".join(lines) for g, lines in groups.items())

    def system_prompt(self) -> str:
        language = language_name()
        return (
            "You are the router of an assistant that manages the files on the user's Mac. Turn the "
            "request (in any language) into a plan of commands to run in order. Available commands:\n"
            f"{self.catalog()}\n\n"
            "Rules:\n"
            f"- use ONLY the listed commands; at most {MAX_STEPS} steps; no useless steps: commands scan "
            "the folder on their own, so do NOT add 'scan' or 'largest' unless the user asks for them;\n"
            "- several steps only if the user asks for several things, in the order they ask;\n"
            "- 'path' is the folder to work on as the user wrote it (e.g. ~/Documents, Downloads, "
            "Desktop, Scrivania), otherwise null (= current folder);\n"
            "- 'args' contains only the arguments listed for that command, with valid values; if you "
            "don't know a value, leave it out; text arguments (query, text) stay in the user's language;\n"
            "- commands that MODIFY FILES only if the user explicitly asks for that action;\n"
            "- if NO command does what the user asks, do NOT pick a similar command: steps = [] and in "
            "'reply' explain in one sentence that you can't do it and what you can do instead;\n"
            "- general-knowledge questions or greetings (not about the user's files): steps = [] and "
            "answer yourself in 'reply';\n"
            "- when there are steps, leave 'reply' empty;\n"
            f"- write 'reply' in {language}.\n\n"
            "Examples:\n"
            '"how much space do the duplicates in Downloads take, then give me a json report" → '
            '{"steps": [{"command": "duplicates", "path": "Downloads"}, '
            '{"command": "export", "path": "Downloads", "args": {"format": "json"}}], "reply": ""}\n'
            '"quanto spazio occupano i doppioni in Scaricati e poi fammi un report json" → (same plan)\n'
            '"rename all photos with their date" → {"steps": [], "reply": "<in ' + language + ': I can\'t '
            'rename files yet, but I can find similar photos, duplicates or organize them by type.>"}\n'
            '"annulla l\'ultima cosa che hai fatto" → {"steps": [{"command": "undo"}], "reply": ""}\n'
            '"cosa dice il contratto di affitto?" → {"steps": [{"command": "ask", "args": '
            '{"query": "cosa dice il contratto di affitto?"}}], "reply": ""}\n\n'
            'Answer ONLY with JSON: {"steps": [{"command": "...", "path": null, "target": null, '
            '"args": {}}], "reply": ""}'
        )

    def schema(self) -> dict[str, Any]:
        names = [c.name for c in self.registry.commands if c.name not in NOT_FOR_AI]
        return {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "maxItems": MAX_STEPS, "items": {
                    "type": "object",
                    "properties": {"command": {"type": "string", "enum": names},
                                   "path": {"type": ["string", "null"]},
                                   "target": {"type": ["string", "null"]},
                                   "args": {"type": "object"}},
                    "required": ["command"]}},
                "reply": {"type": "string"},
            },
            "required": ["steps"],
        }

    @staticmethod
    def context_lines(ctx: Any) -> str:
        if ctx is None:
            return ""
        lines = [f"Current folder: {getattr(ctx, 'directory', '')}"]
        try:
            indexed = ctx.intelligence.index.stats(ctx.directory)["documents"]
            lines.append(f"Documents already indexed in the current folder: {indexed}")
        except Exception:
            pass
        recent = [m for m in getattr(ctx, "history", [])[-4:] if m.get("role") == "user"]
        if recent:
            lines.append("Previous requests: " + " | ".join(m["content"][:120] for m in recent))
        return "\n".join(lines)

    # ------------------------------------------------------------------ route
    async def route(self, text: str, ctx: Any = None) -> RoutePlan:
        start = time.monotonic()
        rules = self.registry.parse(text)
        pending = getattr(ctx, "pending", None) if ctx is not None else None

        def done(plan: RoutePlan) -> RoutePlan:
            plan.elapsed = time.monotonic() - start
            return plan

        # 1) scorciatoie: risposte a domande in sospeso e comandi di servizio
        if rules.type in SHORTCUTS or (pending is not None and rules.type in ("confirm", "cancel")):
            return done(RoutePlan([rules], "shortcut"))
        if self.mode == "rules" or not await self._ai_ready():
            return done(RoutePlan([rules], "rules"))

        # 2) AI
        try:
            data = await asyncio.wait_for(self._ask_ai(text, ctx), ROUTER_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - qualsiasi problema → regole
            plan = RoutePlan([rules], "rules")
            plan.notes.append(_("router AI non disponibile ({error}): uso le regole", error=e.__class__.__name__))
            return done(plan)
        plan = self.validate(data, rules)
        return done(plan)

    async def _ai_ready(self) -> bool:
        try:
            return bool(self.ai) and await self.ai.is_available()
        except Exception:
            return False

    async def _ask_ai(self, text: str, ctx: Any) -> dict[str, Any]:
        prompt = (self.context_lines(ctx) + "\n\nRequest: " + text).strip()
        return await self.ai.generate_json(prompt, system=self.system_prompt(), schema=self.schema(),
                                           model=self.model)

    # ------------------------------------------------------------------ validazione
    def validate(self, data: dict[str, Any], rules: Intent) -> RoutePlan:
        raw_steps = data.get("steps") if isinstance(data, dict) else None
        reply = str(data.get("reply") or "").strip() if isinstance(data, dict) else ""
        notes: list[str] = []
        steps: list[Intent] = []
        for raw in (raw_steps or [])[: MAX_STEPS + 2]:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("command") or "")
            cmd = self.registry.get(name)
            if cmd is None or name in NOT_FOR_AI:
                notes.append(_("comando sconosciuto ignorato: {name}", name=name or "?"))
                continue
            args_in = raw.get("args") if isinstance(raw.get("args"), dict) else {}
            args = {k: v for k, v in args_in.items() if k in cmd.params and v not in (None, "", "null")}
            args = clean_args(args, notes)
            for k in ("query", "text"):  # alcuni modelli mettono la query fuori da args
                if k in cmd.params and k not in args and raw.get(k):
                    args[k] = raw[k]
            # se le regole hanno riconosciuto lo stesso comando, i loro argomenti fanno da base
            if rules.type == name:
                args = {**rules.args, **args}
            path = self._path(raw.get("path")) or (rules.path if rules.type == name else None)
            target = self._path(raw.get("target") or args.pop("target", None)) or \
                (rules.target if rules.type == name else None)
            if target is not None and path is not None and target == path:
                target = None
            steps.append(Intent(name, path=path, target=target, args=args))

        # "indicizza Documents e poi trova i simili": i passi senza cartella usano quella precedente
        for prev, cur in zip(steps, steps[1:]):
            cmd = self.registry.get(cur.type)
            if cur.path is None and prev.path is not None and cmd is not None and cmd.needs_folder:
                cur.path = prev.path

        # al massimo un comando che modifica file, e per ultimo
        destructive = [s for s in steps if self.registry.get(s.type).destructive]
        if len(destructive) > 1:
            notes.append(_("più operazioni sui file nella stessa richiesta: eseguo solo la prima"))
            steps = [s for s in steps if s not in destructive[1:]]
            destructive = destructive[:1]
        if destructive and steps[-1] is not destructive[0]:
            steps.remove(destructive[0])
            steps.append(destructive[0])
        # passi identici consecutivi
        dedup: list[Intent] = []
        for s in steps:
            if not dedup or (dedup[-1].type, dedup[-1].path, dedup[-1].args) != (s.type, s.path, s.args):
                dedup.append(s)
        steps = dedup[:MAX_STEPS]

        if steps:
            reply = ""  # con dei passi parla il risultato dei comandi, non l'AI (evita frasi inventate)
        if not steps:
            if reply:
                return RoutePlan([], "ai", reply=reply, notes=notes)
            return RoutePlan([rules], "rules", notes=notes + [_("l'AI non ha proposto comandi: uso le regole")])
        return RoutePlan(steps, "ai", reply=reply, notes=notes)

    @staticmethod
    def _path(raw: Any) -> Path | None:
        if not raw or str(raw).strip().lower() in ("null", "none", "", "."):
            return None
        from .intents import HOME_ALIASES, extract_paths

        s = str(raw).strip()
        if s.lower() in HOME_ALIASES:
            real = HOME_ALIASES[s.lower()]
            return Path.home() / real if real else Path.home()
        found = extract_paths(s)
        p = found[0] if found else Path(s).expanduser()
        if not p.exists() and p.name.lower() in HOME_ALIASES:  # "~/Scrivania" → ~/Desktop
            real = HOME_ALIASES[p.name.lower()]
            alias = p.parent / real if real else p.parent
            if alias.exists():
                return alias
        if found:
            return found[0]
        if not p.is_absolute() and (Path.home() / s).is_dir():
            return Path.home() / s
        return p


_ENUMS = {"kind": {"images", "documents", "all"}, "strictness": {"strict", "normal", "loose"},
          "format": {"md", "json", "csv"}}


def clean_args(args: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """Scarta valori non validi proposti dall'AI (es. n="tanti", ref="tutto")."""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k in _ENUMS:
            v = str(v).lower().strip()
            if v not in _ENUMS[k]:
                notes.append(_("argomento ignorato: {arg}", arg=f"{k}={v}"))
                continue
        elif k in ("n", "limit"):
            try:
                v = max(1, int(v))
            except (TypeError, ValueError):
                notes.append(_("argomento ignorato: {arg}", arg=f"{k}={v}"))
                continue
        elif k == "ref":
            v = str(v).strip()
            if not any(c.isdigit() for c in v):  # "ultimo", "tutto"… → ultima operazione
                continue
        elif isinstance(v, (dict, list)):
            continue
        out[k] = v
    return out


def describe_step(step: Intent, registry: "CommandRegistry") -> str:
    cmd = registry.get(step.type)
    label = _(cmd.description).split("(")[0].strip() if cmd else step.type
    extra = []
    if step.path:
        extra.append(str(step.path).replace(str(Path.home()), "~"))
    q = step.args.get("query") or step.args.get("text")
    if q:
        extra.append(f'"{q}"')
    return label + (f" — {' '.join(extra)}" if extra else "")
