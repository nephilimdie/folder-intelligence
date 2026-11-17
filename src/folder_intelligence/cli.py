"""Interfaccia a riga di comando e chat interattiva."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Callable, NoReturn

from . import __version__, completion, exporter
from . import history as enc_history
from .assistant import Assistant
from .ai import (DEFAULT_EMBED_ENGINE, DEFAULT_EMBED_MODEL, DEFAULT_ENGINE, DEFAULT_VISION_ENGINE,
                 DEFAULT_VISION_MODEL, create_ai)
from .engines import ENGINES
from .classifier import DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_URL
from .i18n import SUPPORTED, N_, _, is_question, set_language, strip_question_marker
from .intents import Intent
from .mcp_memory import MODES, McpMemory
from .memory import create_memory

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
                               TextColumn, TimeElapsedColumn)

    _console: Console | None = Console()
except ImportError:  # rich è opzionale
    _console = None


def out(text: str, style: str | None = None) -> None:
    if _console:
        _console.print(text, style=style, markup=False, highlight=False)
    else:
        print(text)


def status(msg: str) -> None:
    out(f"… {msg}", style="dim")


# --------------------------------------------------------------------- progress
PHASE_LABELS = {
    "scan": N_("🔍 Cerco i file"),
    "hash": N_("🧬 Confronto i contenuti"),
    "hash_full": N_("🧬 Verifica completa"),
    "classify": N_("🤖 Classifico"),
    "analyze": N_("🔎 Leggo i testi"),
    "enrich": N_("🧠 Comprendo con l'AI"),
    "fingerprint": N_("📷 Impronte delle foto"),
    "compare": N_("🔗 Confronto"),
}


def _short(detail: str, width: int = 38) -> str:
    return detail if len(detail) <= width else "…" + detail[-(width - 1):]


class ProgressDisplay:
    """Mostra l'avanzamento delle fasi lunghe. Thread-safe (la scansione gira in un thread)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._progress = None
        self._tasks: dict[str, int] = {}
        self._last_plain = ""

    def __enter__(self) -> "ProgressDisplay":
        if _console and _console.is_terminal:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("{task.description:<26}"),
                BarColumn(bar_width=28),
                MofNCompleteColumn(),
                TextColumn("[dim]{task.fields[detail]}"),
                TimeElapsedColumn(),
                console=_console,
                transient=True,
            )
            self._progress.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._progress:
            self._progress.stop()
        elif self._last_plain:
            sys.stderr.write("\r" + " " * len(self._last_plain) + "\r")
            sys.stderr.flush()
        self._progress = None
        self._tasks.clear()
        self._last_plain = ""

    def __call__(self, phase: str, done: int, total: int | None, detail: str = "") -> None:
        label = _(PHASE_LABELS.get(phase, phase))
        with self._lock:
            if self._progress is not None:
                if phase not in self._tasks:
                    # completa le fasi precedenti rimaste "aperte"
                    for tid in self._tasks.values():
                        t = self._progress.tasks[tid]
                        if t.total is None:
                            self._progress.update(tid, total=t.completed)
                    self._tasks[phase] = self._progress.add_task(label, total=total, detail="")
                self._progress.update(self._tasks[phase], completed=done, total=total,
                                      detail=_short(Path(detail).name if phase != "scan" else detail))
            elif sys.stderr.isatty():
                line = f"{label}: {done}" + (f"/{total}" if total else "") + f"  {_short(detail)}"
                pad = " " * max(0, len(self._last_plain) - len(line))
                sys.stderr.write("\r" + line + pad)
                sys.stderr.flush()
                self._last_plain = line


# ----------------------------------------------------------------------- input
class LineReader:
    """Legge una riga con autocompletamento (Tab) e storico (↑/↓) persistente."""

    def __init__(self, get_cwd: Callable[[], Path]) -> None:
        self.get_cwd = get_cwd
        self.session = None
        self._readline = None
        if not sys.stdin.isatty():
            return
        try:
            self._init_prompt_toolkit()
        except ImportError:
            self._init_readline()

    def _init_prompt_toolkit(self) -> None:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        self.session = PromptSession(
            history=enc_history.prompt_toolkit_history(),
            completer=completion.prompt_toolkit_completer(self.get_cwd),
            auto_suggest=AutoSuggestFromHistory(),
            complete_while_typing=False,  # menu con Tab; i suggerimenti grigi da storico restano
            enable_history_search=False,  # ↑/↓ scorrono tutto lo storico
        )

    def _init_readline(self) -> None:
        try:
            import readline
        except ImportError:
            return
        self._readline = readline
        for entry in enc_history.load():
            readline.add_history(entry)
        readline.set_history_length(1000)
        readline.set_completer_delims("")  # riceviamo tutta la riga fino al cursore
        matches: list[str] = []

        def _complete(text: str, state: int):
            nonlocal matches
            if state == 0:
                matches = [text[: len(text) + c.start] + c.text
                           for c in completion.complete(text, self.get_cwd())]
            return matches[state] if state < len(matches) else None

        readline.set_completer(_complete)
        if "libedit" in (readline.__doc__ or "") or getattr(readline, "backend", "") == "editline":
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")

    async def read(self, prompt: str) -> str:
        if self.session is not None:
            return await self.session.prompt_async(prompt)
        loop = asyncio.get_running_loop()
        line = await loop.run_in_executor(None, input, prompt)
        if self._readline is not None and line.strip():
            enc_history.append(line)
        return line


# ------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="folder-intelligence",
        description=_("🤖 folderIntelligence — trova duplicati e organizza le tue cartelle con AI locale."),
    )
    p.add_argument("directory", nargs="?", default="~/Downloads", help=_("cartella da analizzare (default: ~/Downloads)"))
    p.add_argument("-i", "--interactive", action="store_true", help=_("chat interattiva (default se non indichi altre azioni)"))
    p.add_argument("--lang", choices=SUPPORTED, default=None,
                   help=_("lingua dell'interfaccia e delle risposte (default: lingua di macOS; anche FI_LANG)"))
    p.add_argument("--full-scan", action="store_true", help=_("classifica i file (regole + AI)"))
    p.add_argument("--duplicates", action="store_true", help=_("elenca i duplicati"))
    p.add_argument("--dedupe", action="store_true", help=_("sposta nel Cestino le copie duplicate (dry-run senza --apply)"))
    p.add_argument("--organize", action="store_true", help=_("organizza in cartelle per categoria (dry-run senza --apply)"))
    p.add_argument("--target", help=_("cartella di destinazione per --organize"))
    p.add_argument("--apply", action="store_true", help=_("esegue davvero --dedupe/--organize"))
    p.add_argument("--history", action="store_true", help=_("mostra l'audit delle modifiche ai file"))
    p.add_argument("--undo", nargs="?", const="last", metavar="ID",
                   help=_("annulla l'ultima operazione, o quella indicata (id o numero dello storico)"))
    p.add_argument("--index", action="store_true", help=_("legge e comprende il contenuto dei file (indice)"))
    p.add_argument("--search", metavar=_("TESTO"), help=_("cerca per nome, contenuto e significato"))
    p.add_argument("--ask", metavar=_("DOMANDA"), help=_("domanda sul contenuto dei documenti indicizzati"))
    p.add_argument("--export", choices=exporter.FORMATS, help=_("esporta un report (json, md, csv)"))
    p.add_argument("-o", "--output", help=_("file di output per --export"))
    p.add_argument("--classic", action="store_true", help=_("chat semplice, senza pannello attività"))
    p.add_argument("--no-mouse", action="store_true",
                   help=_("non catturare il mouse (selezione testo nativa; si scorre con PagSu/PagGiù)"))
    p.add_argument("--serve", action="store_true", help=_("avvia le API HTTP locali per l'app Mac"))
    p.add_argument("--install-background", action="store_true",
                   help=_("installa il servizio che completa l'indicizzazione quando il Mac è inattivo e in carica"))
    p.add_argument("--uninstall-background", action="store_true", help=_("rimuove il servizio in background"))
    p.add_argument("--background-status", action="store_true", help=_("stato del servizio in background"))
    p.add_argument("--background", action="store_true", help=argparse.SUPPRESS)  # lanciato da launchd
    p.add_argument("--host", default="127.0.0.1", help=_("host API (default 127.0.0.1)"))
    p.add_argument("--port", type=int, default=8765, help=_("porta API (default 8765)"))
    p.add_argument("--memory", default="json", choices=["json", "inmemory", "lightrag"], help=_("backend memoria"))
    p.add_argument("--no-ai", action="store_true", help=_("non usare l'AI locale"))
    p.add_argument("--model", default=None,
                   help=_("modello testuale: nome Ollama (es. qwen2.5:14b, riusato anche da llama.cpp) o file "
                          ".gguf (default {model})", model=DEFAULT_OLLAMA_MODEL))
    engines = ["auto", *ENGINES]
    p.add_argument("--engine", choices=engines, default=DEFAULT_ENGINE,
                   help=_("motore AI per testo e router: auto (llama.cpp se disponibile, altrimenti Ollama), "
                          "llamacpp (dentro l'app), ollama, mlx"))
    p.add_argument("--vision-engine", choices=engines, default=DEFAULT_VISION_ENGINE,
                   help=_("motore per le immagini (auto = Ollama)"))
    p.add_argument("--embed-engine", choices=engines, default=DEFAULT_EMBED_ENGINE,
                   help=_("motore per gli embedding della ricerca semantica (auto = come --engine)"))
    p.add_argument("--router", choices=["ai", "rules"], default=None,
                   help=_("come interpretare le frasi: ai (default, piano di comandi deciso dal modello) o rules"))
    p.add_argument("--router-model", default=None,
                   help=_("modello per interpretare le frasi (default = --model; es. qwen2.5:7b, più veloce)"))
    p.add_argument("--vision-model", default=None,
                   help=_("modello per immagini (default {model})", model=DEFAULT_VISION_MODEL))
    p.add_argument("--embed-model", default=None,
                   help=_("modello embedding (default {model})", model=DEFAULT_EMBED_MODEL))
    p.add_argument("--no-vision", action="store_true", help=_("non usare il modello visivo (più veloce)"))
    p.add_argument("--bench-init", metavar=_("CARTELLA"),
                   help=_("banco di prova: crea bench.json con i file della cartella, da completare con le "
                          "risposte giuste"))
    p.add_argument("--bench", metavar=_("CARTELLA"),
                   help=_("banco di prova: confronta OCR e modelli sui file di bench.json"))
    p.add_argument("--bench-configs", metavar=_("NOMI"),
                   help=_("solo queste combinazioni, separate da virgola (prima, apple-ocr, …)"))
    p.add_argument("--index-limit", type=int, default=0,
                   help=_("massimo file analizzati per 'indicizza' (0 = tutti)"))
    p.add_argument("--mcp-mode", choices=MODES, default=None,
                   help=_("cosa registrare nella memoria MCP: sealed (default, solo inventario), summary, off"))
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--ai-limit", type=int, default=50, help=_("massimo file letti dall'AI per scansione"))
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def build_ai(args: argparse.Namespace):
    return create_ai(engine=args.engine, vision_engine=args.vision_engine, embed_engine=args.embed_engine,
                     model=args.model, vision_model=args.vision_model, embed_model=args.embed_model,
                     ollama_url=args.ollama_url)


async def ai_state_line(ai) -> str:
    if not ai:
        return _("disattivata")
    if await ai.is_available():
        return _("attiva — {summary}", summary=ai.summary())
    return _("non disponibile ({model}, {engine}): {why}", model=ai.model, engine=ai.engine_of("text"),
             why=ai.why_unavailable("text"))


def make_assistant(args: argparse.Namespace, progress: ProgressDisplay) -> Assistant:
    ai = None if args.no_ai else build_ai(args)
    return Assistant(
        args.directory, memory=create_memory(args.memory), ollama=ai,
        use_ai=not args.no_ai, ai_limit=args.ai_limit, status=status,
        source="cli", progress=progress, use_vision=not args.no_vision,
        index_limit=args.index_limit or None, mcp=McpMemory(args.mcp_mode),
        router_model=args.router_model, router_mode=args.router,
    )


async def run_batch(args: argparse.Namespace, assistant: Assistant, progress: ProgressDisplay) -> int:
    if args.history:
        out(await assistant.do_history(Intent("history")))
        return 0
    if args.undo:
        ref = None if args.undo == "last" else args.undo
        out(await assistant.do_undo(Intent("undo", args={"ref": ref} if ref else {})))
        return 0

    with progress:
        summary = await assistant.do_scan(Intent("scan"))
    out(summary)
    if args.full_scan:
        out("")
        with progress:
            reply = await assistant.do_classify(Intent("classify"))
        out(reply)
    if args.duplicates:
        out("")
        with progress:
            reply = await assistant.do_duplicates(Intent("duplicates"))
        out(reply)
    if args.index:
        out("")
        with progress:
            reply = await assistant.do_index(Intent("index"))
        out(reply)
    if args.search:
        out("")
        out(await assistant.do_search(Intent("search", args={"query": args.search})))
    if args.ask:
        out("")
        out(await assistant.do_ask(Intent("ask", args={"query": args.ask})))
    for flag, kind in ((args.dedupe, "dedupe"), (args.organize, "organize")):
        if not flag:
            continue
        intent = Intent(kind, target=Path(args.target) if args.target and kind == "organize" else None)
        out("")
        with progress:
            reply = await assistant.dispatch(intent)
        out(split_question(reply)[0])
        if assistant.pending:
            if args.apply:
                out(await assistant.do_confirm(Intent("confirm")))
            else:
                assistant.pending = None
                out(_("(dry-run: aggiungi --apply per eseguire)"), style="yellow")
    if args.export:
        with progress:
            scan = await assistant.get_scan()
            await assistant.get_duplicates(scan)
            await assistant.ensure_classified(scan)
        path = exporter.export(scan, args.export, Path(args.output) if args.output else None)
        out("\n" + _("📄 Report salvato in {path}", path=path))
    return 0


async def header_lines(assistant: Assistant) -> list:
    """Intestazione della chat a pannelli: una riga di identità, poi solo ciò che serve.

    Cifratura e memoria MCP compaiono solo se qualcosa non va; al posto dei dettagli tecnici,
    tre richieste d'esempio adatte alla cartella aperta."""
    from . import crypto
    from .tui import short_path

    ai = assistant.ai
    ai_ok = bool(ai) and await ai.is_available()
    ai_label = (_("AI: {model}", model=ai.model) if ai_ok else _("AI non attiva") if not ai else _("AI non disponibile"))
    lines: list = [f"folderIntelligence {__version__}  ·  {short_path(assistant.directory)}  ·  {ai_label}"]
    if ai and not ai_ok:
        lines.append(("warn", _("L'AI locale non risponde: {why}. Le funzioni base restano disponibili.",
                                why=ai.why_unavailable("text"))))
    try:
        crypto.master_key()
    except Exception as e:  # noqa: BLE001
        lines.append(("err", _("❗ Cifratura non disponibile: {error}", error=e)))
    mcp = assistant.mcp
    if mcp.mode != "off" and not await mcp.available():
        lines.append(("warn", _("Memoria MCP non raggiungibile: la comprensione dei file resta solo locale.")))
    from . import background

    done_while_away = background.take_report()
    if done_while_away:
        lines.append(("info", _("🌙 Mentre non usavi l'app ho compreso {n} file in background.", n=done_while_away)))
    indexed = assistant.intelligence.index.stats(assistant.directory)["documents"]
    starters = ([_("cerca la ricevuta dell'hotel"), _("trovami i duplicati"), _("cosa devo fare?")] if indexed
                else [_("indicizza la cartella"), _("trovami i duplicati"), _("mostrami i 10 file più grandi")])
    lines.append("")
    lines.append(("info", _("Scrivi come parleresti. Per esempio:")))
    lines += [("starter", f"  › {s}") for s in starters]
    lines.append(("muted", _("'aiuto' elenca tutto · le operazioni lunghe continuano nel riquadro Attività")))
    return lines


async def run_tui(assistant: Assistant, mouse: bool = True) -> int:
    from .jobs import JobManager
    from .tui import ChatTUI

    assistant.progress = None
    assistant.status = lambda _m: None
    ui = ChatTUI(assistant, JobManager(), await header_lines(assistant), mouse=mouse)
    return await ui.run()


async def run_chat(assistant: Assistant, progress: ProgressDisplay) -> int:
    ai_state = await ai_state_line(assistant.ai)
    from . import crypto

    try:
        crypto.master_key()
        sec = _("🔐 dati dell'app cifrati (AES-256-GCM, chiave in: {where})", where=crypto.key_location())
    except Exception as e:  # noqa: BLE001
        sec = _("❗ cifratura non disponibile: {error}", error=e)
    mcp = assistant.mcp
    mcp_state = (_("disattivata") if mcp.mode == "off" else
                 f"{mcp.mode} ({_('raggiungibile') if await mcp.available() else _('non raggiungibile')})")
    header = (_("Cartella: {dir}", dir=assistant.directory) + "\n" + _("AI locale: {state}", state=ai_state)
              + f"\n{sec}\n" + _("Memoria MCP: {state}", state=mcp_state) + "\n\n"
              + _("Scrivi 'aiuto' per i comandi, 'esci' per uscire.") + "\n"
              + _("Tab: completa cartelle e comandi · ↑/↓: comandi precedenti · Ctrl+C: interrompe"))
    if _console:
        _console.print(Panel(header, title="🤖 folderIntelligence", border_style="cyan"))
    else:
        print("=" * 60 + "\n🤖 folderIntelligence\n" + header + "\n" + "=" * 60)

    reader = LineReader(lambda: assistant.directory)
    interactive = reader.session is not None  # terminale vero con prompt_toolkit
    while True:
        try:
            text = (await reader.read("\n" + _("Tu › "))).strip()
        except (EOFError, KeyboardInterrupt):
            out("\n" + _("A presto! 👋"))
            return 0
        if not text:
            continue
        reply = await execute(assistant, progress, text)
        # conferme sì/no: selettore con le frecce invece di dover scrivere
        while interactive and assistant.pending and not _is_exit(reply):
            body, question = split_question(reply)
            show(body)
            answer = await ask_confirmation(assistant, question)
            reply = await execute(assistant, progress, "sì" if answer else "no")
        show(reply)
        if _is_exit(reply):
            return 0


def _is_exit(reply: str) -> bool:
    return getattr(reply, "data", {}).get("command") == "exit"


def show(reply: str) -> None:
    if reply:
        out("")
        out(reply, style="cyan")


def split_question(reply: str) -> tuple[str, str]:
    """Separa il testo dalla domanda finale ('… Confermi? (sì/no) …') da mostrare nel selettore."""
    lines = reply.rstrip().splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if is_question(lines[i]):
            body = "\n".join(lines[:i]).rstrip()
            return body, strip_question_marker(lines[i])
    return reply, _("Confermi?")


def confirmation_options(kind: str) -> list[tuple[bool, str]]:
    """Etichette del selettore sì/no (condivise con l'interfaccia a pannelli)."""
    if kind == "index":
        return [(True, _("Sì, leggi i file")), (False, _("No, non ora"))]
    if kind == "dedupe":
        return [(True, _("Sì, sposta nel Cestino")), (False, _("No, annulla"))]
    if kind == "organize":
        return [(True, _("Sì, organizza")), (False, _("No, annulla"))]
    return [(True, _("Sì, procedi")), (False, _("No"))]


async def ask_confirmation(assistant: Assistant, question: str) -> bool:
    from .assistant import PendingAction
    from .selector import select

    pending = assistant.pending
    if isinstance(pending, PendingAction):
        options = confirmation_options(pending.kind)
        default = 0
    else:  # modifiche ai file: il default è "No" per sicurezza
        options = confirmation_options(getattr(pending, "kind", ""))
        default = 1
    out("")
    try:
        choice = await select(question, options, default)
    except (KeyboardInterrupt, EOFError):
        choice = None
    return bool(choice)


async def execute(assistant: Assistant, progress: ProgressDisplay, text: str) -> str:
    """Esegue un comando con barra di avanzamento e Ctrl+C a due livelli."""
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(assistant.handle(text))

    def on_sigint() -> None:
        if assistant.cancel.is_set():
            task.cancel()  # secondo Ctrl+C: interrompi subito
            return
        assistant.cancel.set()
        # se l'operazione è bloccata (es. file iCloud) non risponde al flag: forza dopo 2s
        loop.call_later(2.0, lambda: task.done() or task.cancel())

    try:
        loop.add_signal_handler(signal.SIGINT, on_sigint)
    except (NotImplementedError, RuntimeError):
        pass
    try:
        with progress:
            return await task
    except (asyncio.CancelledError, KeyboardInterrupt):
        return _("🛑 Interrotto. Nessun file è stato modificato.")
    finally:
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, RuntimeError):
            pass


def shutdown(code: int) -> NoReturn:
    """Chiusura pulita: salva i dati cifrati, pulisce i temporanei, esce senza stacktrace.

    Usa os._exit per non attendere eventuali thread bloccati su file iCloud/rete
    (sono daemon e senza effetti collaterali: le scritture importanti sono già su disco).
    """
    try:
        from .index import flush_all
        from .intelligence import cleanup_temp

        flush_all()
        cleanup_temp()
    except Exception:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def main(argv: list[str] | None = None) -> int:
    try:
        code = _main(argv)
    except KeyboardInterrupt:
        out("\n" + _("A presto! 👋"))
        code = 130
    except Exception as e:  # errore inatteso: messaggio chiaro, niente traceback
        out("\n" + _("❌ Errore inatteso: {error}", error=e), style="red")
        if os.environ.get("FI_DEBUG"):
            import traceback

            traceback.print_exc()
        else:
            out("   " + _("(avvia con FI_DEBUG=1 per i dettagli tecnici)"), style="dim")
        code = 1
    shutdown(code)


def background_command(args: argparse.Namespace) -> int:
    from . import background

    if args.background:
        try:
            return asyncio.run(background.run_service())
        except KeyboardInterrupt:
            return 0
    if args.uninstall_background:
        removed = background.uninstall()
        out(_("Servizio in background rimosso.") if removed else _("Il servizio in background non era installato."))
        return 0
    if args.install_background:
        try:
            path = background.install()
        except Exception as e:  # noqa: BLE001
            out(_("❌ Installazione non riuscita: {error}", error=e), style="red")
            return 1
        out(_("✅ Servizio in background installato ({path}).", path=path))
        out(_("Completa la comprensione dei file quando il Mac è in carica e non lo usi da {n} minuti; "
              "si ferma da solo appena torni o apri la chat. Per rimuoverlo: --uninstall-background.",
              n=int(background.idle_minutes_required())))
        return 0
    st = background.status()
    out(_("Servizio in background: {state} · file completati finora: {n} · ultimo lavoro: {last}",
          state=_("attivo") if st["running"] else _("installato, in attesa") if st["loaded"]
          else _("non installato"), n=st["total_completed"], last=st["last_run"] or "—"))
    return 0


def _main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # la lingua serve già per i testi di --help: la leggiamo prima di costruire il parser
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            set_language(argv[i + 1])
        elif a.startswith("--lang="):
            set_language(a.split("=", 1)[1])
    args = build_parser().parse_args(argv)

    if args.background or args.install_background or args.uninstall_background or args.background_status:
        return background_command(args)
    if args.bench_init or args.bench:
        from .bench import bench_init, bench_run

        return bench_init(args.bench_init) if args.bench_init else bench_run(args.bench, args.bench_configs)

    # un solo processo alla volta scrive l'indice cifrato: se il servizio in background sta
    # lavorando, gli chiediamo di salvare e aspettiamo qualche secondo
    from .locks import IndexLock

    IndexLock().acquire(blocking=True, on_wait=lambda: out(
        _("⏳ Il servizio in background sta salvando il suo lavoro, un attimo…"), style="dim"))

    if args.serve:
        from .api import serve

        if args.mcp_mode:
            os.environ["FI_MCP_MODE"] = args.mcp_mode
        return serve(args.host, args.port, ai=None if args.no_ai else build_ai(args),
                     memory=args.memory, use_vision=not args.no_vision)

    directory = Path(args.directory).expanduser()
    if not directory.is_dir() and not (args.undo or args.history):
        out(_("❌ La cartella {path} non esiste.", path=directory), style="red")
        return 2
    progress = ProgressDisplay()
    assistant = make_assistant(args, progress)

    batch = any([args.full_scan, args.duplicates, args.dedupe, args.organize, args.export,
                 args.undo, args.history, args.index, args.search, args.ask])
    if batch and not args.interactive:
        return asyncio.run(run_batch(args, assistant, progress))
    if not args.classic and sys.stdin.isatty() and sys.stdout.isatty():
        mouse = not args.no_mouse and os.environ.get("FI_MOUSE", "1") != "0"
        return asyncio.run(run_tui(assistant, mouse))
    return asyncio.run(run_chat(assistant, progress))


if __name__ == "__main__":
    sys.exit(main())
