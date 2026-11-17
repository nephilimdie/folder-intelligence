"""Chat a pannelli: conversazione a sinistra, attività in background nel box laterale.

Ogni frase diventa un'attività (Job). Se termina entro FOREGROUND_SECONDS la risposta
appare subito; altrimenti l'attività prosegue in background, il box "Attività" ne mostra
l'avanzamento e l'utente può continuare a scrivere. Al termine il risultato compare nella
conversazione; se serve una conferma appare il selettore sopra la riga di input.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (ConditionalContainer, DynamicContainer, Float, FloatContainer, HSplit,
                                   Layout, VSplit, Window)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

from . import completion
from . import history as enc_history
from . import agenda
from .i18n import N_, _, is_question
from .jobs import CANCELLED, DONE, ERROR, Job, JobManager

if TYPE_CHECKING:
    from .assistant import Assistant

FOREGROUND_SECONDS = 1.5
AGENDA_MAX_LINES = 10
PANEL_WIDTH = 42
WIDE_MIN_COLUMNS = 100
MAX_LINES = 4000
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PHASES = {"scan": N_("🔍 file"), "hash": N_("🧬 confronto"), "hash_full": N_("🧬 verifica"),
          "classify": N_("🤖 classifico"), "analyze": N_("🧠 lettura"),
          "fingerprint": N_("🖼️ impronte"), "compare": N_("🔗 confronto")}
EXIT_WORDS = {"esci", "exit", "quit", "q", "basta", "chiudi", "bye", "close"}

STYLE = Style.from_dict({
    "you": "bold",
    "reply": "ansicyan",
    "info": "ansibrightblack",
    "warn": "ansiyellow",
    "err": "ansired",
    "jobhead": "bold",
    "title": "bold ansicyan",
    "due": "ansicyan",
    "today": "bold ansiyellow",
    "overdue": "bold ansired",
    "done": "ansibrightblack",
    "frame.border": "ansibrightblack",
    "frame.label": "bold",
    "sel": "reverse bold ansicyan",
    "question": "bold",
    "hint": "ansibrightblack",
    "bar": "ansicyan",
    "bar.empty": "ansibrightblack",
    "status": "reverse",
})


class ScrollableControl(FormattedTextControl):
    """Testo che reagisce alla rotellina/trackpad (eventi mouse del terminale)."""

    def __init__(self, *args, on_scroll, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._on_scroll = on_scroll

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._on_scroll(3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._on_scroll(-3)
            return None
        return NotImplemented


@dataclass
class Confirmation:
    job: Job | None
    question: str
    options: list[tuple[Any, str]]
    selected: int
    on_answer: Any  # callable(bool|None)


def _fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s"


def _bar(fraction: float | None, width: int) -> list[tuple[str, str]]:
    if fraction is None:
        return [("class:bar.empty", "·" * width)]
    full = int(round(max(0.0, min(1.0, fraction)) * width))
    return [("class:bar", "█" * full), ("class:bar.empty", "░" * (width - full))]


class ChatTUI:
    def __init__(self, assistant: "Assistant", jobs: JobManager, header: list[str],
                 mouse: bool = True) -> None:
        self.assistant = assistant
        self.jobs = jobs
        self.lines: deque[list[tuple[str, str]]] = deque(maxlen=MAX_LINES)
        self.scroll = 0  # righe nascoste in fondo (0 = si vede l'ultima riga)
        self.unread = False  # arrivati messaggi mentre si leggeva più in alto
        self.confirms: deque[Confirmation] = deque()
        self.tick = 0
        self.last_ctrl_c = 0.0
        self.exit_code = 0

        for line in header:
            self.write(line, "info")
        self.write("")

        # ------------------------------------------------------------- input
        self.input = TextArea(
            height=1, prompt=_("Tu › "), multiline=False, wrap_lines=False,
            completer=completion.prompt_toolkit_completer(lambda: self.assistant.directory),
            complete_while_typing=False, auto_suggest=AutoSuggestFromHistory(),
            history=enc_history.prompt_toolkit_history(), accept_handler=self._on_accept,
        )

        # ------------------------------------------------------- conversazione
        self.output_control = ScrollableControl(self._render_output, focusable=False,
                                                get_cursor_position=self._cursor, on_scroll=self.scroll_by)
        self.output = Window(self.output_control, wrap_lines=True, always_hide_cursor=True)
        scrolled = Condition(lambda: self.scroll > 0)
        self.scroll_hint = ConditionalContainer(
            Window(FormattedTextControl(self._render_scroll_hint), height=1, style="class:warn"),
            filter=scrolled)

        # --------------------------------------------------------- conferma
        confirm_kb = KeyBindings()

        @confirm_kb.add("left")
        @confirm_kb.add("up")
        @confirm_kb.add("s-tab")
        def _key1(e) -> None:
            c = self.confirms[0]
            c.selected = (c.selected - 1) % len(c.options)

        @confirm_kb.add("right")
        @confirm_kb.add("down")
        @confirm_kb.add("tab")
        def _key2(e) -> None:
            c = self.confirms[0]
            c.selected = (c.selected + 1) % len(c.options)

        @confirm_kb.add("enter")
        def _key3(e) -> None:
            c = self.confirms[0]
            self._answer(c.options[c.selected][0])

        @confirm_kb.add("escape", eager=True)
        @confirm_kb.add("c-c")
        def _key4(e) -> None:
            self._answer(None)

        @confirm_kb.add("<any>")
        def _key5(e) -> None:
            key = e.data.lower()
            for i, (_value, label) in enumerate(self.confirms[0].options):
                if label.strip()[:1].lower() == key:
                    self.confirms[0].selected = i
                    self._answer(self.confirms[0].options[i][0])
                    return

        self.confirm_control = FormattedTextControl(self._render_confirm, focusable=True,
                                                    key_bindings=confirm_kb, show_cursor=False)
        self.confirm_window = Window(self.confirm_control, wrap_lines=True, dont_extend_height=True)
        has_confirm = Condition(lambda: bool(self.confirms))

        # ------------------------------------------------------------ pannello
        self.panel = Frame(Window(FormattedTextControl(self._render_jobs), wrap_lines=True),
                           title=lambda: _("Attività ({n})", n=len(self.jobs.active())), width=D(preferred=PANEL_WIDTH))
        # agenda sotto le attività: cose da fare con la data
        self.agenda_panel = Frame(
            Window(FormattedTextControl(self._render_agenda), wrap_lines=True,
                   height=lambda: D(min=3, max=self._agenda_height(), preferred=self._agenda_height())),
            title=lambda: _("📅 Agenda ({n})", n=len(agenda.pending())), width=D(preferred=PANEL_WIDTH))

        left = HSplit([
            self.output,
            self.scroll_hint,
            ConditionalContainer(HSplit([Window(height=1, char="─", style="class:info"), self.confirm_window]),
                                 filter=has_confirm),
            Window(height=1, char="─", style="class:info"),
            self.input,
        ])

        def body():
            cols = self._columns()
            if cols >= WIDE_MIN_COLUMNS:
                return VSplit([left, HSplit([self.panel, self.agenda_panel], width=D(preferred=PANEL_WIDTH))])
            # terminale stretto: i box vanno sotto, e solo se c'è qualcosa da mostrare
            show = Condition(lambda: bool(self.jobs.active()))
            show_agenda = Condition(lambda: bool(agenda.pending()))
            return HSplit([left, ConditionalContainer(
                Frame(Window(FormattedTextControl(self._render_jobs), height=D(max=6), wrap_lines=True),
                      title=lambda: _("Attività ({n})", n=len(self.jobs.active()))), filter=show),
                ConditionalContainer(
                Frame(Window(FormattedTextControl(self._render_agenda), height=D(max=5), wrap_lines=True),
                      title=lambda: _("📅 Agenda ({n})", n=len(agenda.pending()))), filter=show_agenda)])

        root = FloatContainer(
            content=HSplit([DynamicContainer(body),
                            Window(FormattedTextControl(self._render_status), height=1, style="class:status")]),
            floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=10, scroll_offset=1))],
        )

        kb = KeyBindings()

        @kb.add("c-c", filter=~has_confirm)
        def _key6(e) -> None:
            self._ctrl_c()

        @kb.add("c-d", filter=has_focus(self.input) & ~has_confirm)
        def _key7(e) -> None:
            if not self.input.text:
                self._request_exit()

        @kb.add("pageup")
        def _key8(e) -> None:
            self.scroll_by(self._page())

        @kb.add("pagedown")
        def _key9(e) -> None:
            self.scroll_by(-self._page())

        @kb.add("s-up")
        def _key10(e) -> None:
            self.scroll_by(1)

        @kb.add("s-down")
        def _key11(e) -> None:
            self.scroll_by(-1)

        @kb.add("escape", filter=scrolled & ~has_confirm, eager=True)
        @kb.add("end", filter=scrolled & ~has_confirm & Condition(lambda: not self.input.text))
        def _key12(e) -> None:
            self.scroll_to_bottom()

        self.app: Application = Application(
            layout=Layout(root, focused_element=self.input), key_bindings=kb, style=STYLE,
            full_screen=True, mouse_support=mouse,
        )
        jobs.subscribe(self._on_jobs_changed)

    # ================================================================ output
    def write(self, text: str, style: str = "") -> None:
        new = str(text).split("\n")
        for line in new:
            self.lines.append([(f"class:{style}" if style else "", line)])
        if self.scroll:
            # chi sta leggendo più in alto resta dov'è: avvisiamo dei nuovi messaggi
            self.scroll = min(self.scroll + len(new), max(0, len(self.lines) - 1))
            self.unread = True
        self._invalidate()

    # ------------------------------------------------------------- scorrimento
    def scroll_by(self, lines: int) -> None:
        self.scroll = max(0, min(self.scroll + lines, max(0, len(self.lines) - 1)))
        if self.scroll == 0:
            self.unread = False
        self._invalidate()

    def scroll_to_bottom(self) -> None:
        self.scroll = 0
        self.unread = False
        self._invalidate()

    def _page(self) -> int:
        info = self.output.render_info
        return max(3, (info.window_height if info else 20) - 2)

    def _render_scroll_hint(self) -> FormattedText:
        news = "  " + _("● nuovi messaggi") if self.unread else ""
        return FormattedText([("class:warn", " " + _("▼ {n} righe più sotto{news} — rotella/PagGiù per scendere, "
                                                     "Esc o Fine per tornare in fondo", n=self.scroll, news=news))])

    def _line_count(self) -> int:
        return len(self.lines)

    def _render_output(self) -> FormattedText:
        # mostriamo le righe fino a (fine - scroll): la finestra segue l'ultima visibile,
        # così ogni passo di scorrimento sposta subito la vista
        visible = list(self.lines)[: max(1, len(self.lines) - self.scroll)]
        out: list[tuple[str, str]] = []
        for i, line in enumerate(visible):
            if i:
                out.append(("", "\n"))
            out.extend(line)
        return FormattedText(out)

    def _cursor(self) -> Point:
        return Point(x=0, y=max(0, len(self.lines) - 1 - self.scroll))  # ultima riga visibile

    def _columns(self) -> int:
        try:
            return self.app.output.get_size().columns
        except Exception:
            return 120

    def _invalidate(self) -> None:
        try:
            self.app.invalidate()
        except Exception:
            pass

    # ================================================================ jobs
    def _render_jobs(self) -> FormattedText:
        width = max(10, min(PANEL_WIDTH, self._columns()) - 14)
        active = self.jobs.active()
        recent = [j for j in self.jobs.list() if not j.active][:4]
        out: list[tuple[str, str]] = []
        if not active and not recent:
            return FormattedText([("class:info", _("Nessuna attività.\n\nLe operazioni lunghe\n"
                                                  "continuano qui mentre\nusi la chat."))])
        for j in active:
            spin = SPINNER[self.tick % len(SPINNER)]
            title = j.title if len(j.title) <= width + 8 else j.title[: width + 7] + "…"
            out += [("class:jobhead", f"#{j.id} {spin} {title}\n")]
            phase = _(PHASES.get(j.phase, j.phase)) or (j.message[:width + 8] if j.message else _("avvio…"))
            count = f" {j.done}/{j.total}" if j.total else (f" {j.done}" if j.done else "")
            out += [("", f"   {phase}{count}\n   ")] + _bar(j.fraction, width) + [("class:info", f" {_fmt_secs(j.elapsed)}\n")]
            if j.detail:
                d = j.detail.rsplit("/", 1)[-1]
                out += [("class:info", f"   {d[: width + 6]}\n")]
            out += [("", "\n")]
        if recent:
            out += [("class:info", _("Recenti") + "\n")]
            for j in recent:
                icon = {DONE: "✅", ERROR: "❌", CANCELLED: "⏹️ "}.get(j.status, "•")
                if j.confirmation:
                    icon = "❓"
                secs = _fmt_secs(j.elapsed)
                room = width + 6 - len(f"#{j.id}") - len(secs) - 2  # titolo + tempo sulla stessa riga
                title = j.title if len(j.title) <= room else j.title[: max(4, room - 1)] + "…"
                out += [("", f"#{j.id} {icon} {title}"), ("class:info", f" {secs}\n")]
        if active:
            out += [("class:info", "\n" + _("'ferma {id}' interrompe #{id}", id=active[-1].id))]
        return FormattedText(out)

    # ================================================================ agenda
    def _agenda_height(self) -> int:
        n = len(agenda.pending()) + min(2, len(agenda.recently_done(2)))
        return max(3, min(AGENDA_MAX_LINES, n + 1))

    def _render_agenda(self) -> FormattedText:
        width = max(10, min(PANEL_WIDTH, self._columns()) - 4)
        todo = agenda.pending()
        done = agenda.recently_done(2)
        if not todo and not done:
            return FormattedText([("class:info", _("Nessun promemoria.\n'ricordami di … venerdì'"))])
        out: list[tuple[str, str]] = []
        style = {"overdue": "class:overdue", "today": "class:today", "soon": "class:due", "later": "class:due"}
        for item in todo[:AGENDA_MAX_LINES]:
            when = agenda.format_due(item.due_date)
            st = item.status()
            mark = "⚠" if st == "overdue" else "●" if st == "today" else " "
            head = f"{mark}#{item.id} "
            date_col = f"{when:<10} " if when else ""
            text = item.text if len(head + date_col + item.text) <= width else \
                item.text[: max(4, width - len(head + date_col) - 1)] + "…"
            out += [("class:info", head), (style.get(st, ""), date_col), ("", text + "\n")]
        if len(todo) > AGENDA_MAX_LINES:
            out.append(("class:info", _("… e altri {n}", n=len(todo) - AGENDA_MAX_LINES) + "\n"))
        for item in done:
            text = item.text if len(item.text) <= width - 6 else item.text[: width - 7] + "…"
            out.append(("class:done", f"✓#{item.id} {text}\n"))
        return FormattedText(out)

    def _render_status(self) -> FormattedText:
        n = len(self.jobs.active())
        jobs = " " + _("⏳ {n} in corso", n=n) + " " if n else ""
        return FormattedText([("class:status", f" 📁 {self.assistant.directory}  {jobs}"),
                              ("class:status", "  " + _("Tab completa · ↑/↓ storico · rotella/PagSu scorre · "
                                                       "Ctrl+C ferma · Ctrl+D esce") + " ")])

    def _on_jobs_changed(self) -> None:
        self._invalidate()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.15)
            if self.jobs.active():
                self.tick += 1
                self._invalidate()

    # ============================================================== conferme
    def _render_confirm(self) -> FormattedText:
        if not self.confirms:
            return FormattedText([])
        c = self.confirms[0]
        prefix = f"#{c.job.id} · " if c.job else ""
        out: list[tuple[str, str]] = [("class:question", f"{prefix}{c.question}\n  ")]
        for i, (_value, label) in enumerate(c.options):
            out.append(("class:sel" if i == c.selected else "", f" {'❯' if i == c.selected else ' '} {label} "))
            out.append(("", "  "))
        out.append(("class:hint", _("←/→ scegli · Invio conferma · Esc annulla")))
        if len(self.confirms) > 1:
            out.append(("class:hint", "   " + _("(+{n} in attesa)", n=len(self.confirms) - 1)))
        return FormattedText(out)

    def ask(self, question: str, options: list[tuple[Any, str]], default: int, on_answer, job: Job | None = None) -> None:
        self.confirms.append(Confirmation(job, question, options, default, on_answer))
        if len(self.confirms) == 1:
            self.app.layout.focus(self.confirm_window)
        self._invalidate()

    def _answer(self, value: Any) -> None:
        c = self.confirms.popleft()
        label = next((lbl for v, lbl in c.options if v == value), _("No"))
        self.write(f"   → {label}", "info")
        if self.confirms:
            self.app.layout.focus(self.confirm_window)
        else:
            self.app.layout.focus(self.input)
        c.on_answer(value)
        self._invalidate()

    def _confirm_job(self, job: Job) -> None:
        """Il comando di `job` chiede conferma: selettore, poi 'sì'/'no' sulla stessa sessione."""
        info = job.confirmation or {}
        from .cli import confirmation_options

        options = confirmation_options(info.get("kind", ""))
        default = 1 if info.get("destructive") else 0

        def answered(value: Any) -> None:
            job.confirmation = None
            self.run_text("sì" if value else "no", ctx=job.ctx, title=_("conferma #{id}", id=job.id), echo=False)

        self.ask(info.get("question") or _("Confermi?"), options, default, answered, job)

    # ================================================================ comandi
    def _on_accept(self, buff) -> bool:
        text = buff.text.strip()
        if not text:
            return False
        if text.lower() in EXIT_WORDS:
            self._request_exit()
            return False
        if self._jobs_command(text):
            return False
        self.run_text(text)
        return False  # svuota l'input (lo storico è già salvato)

    def _jobs_command(self, text: str) -> bool:
        """Comandi che riguardano le attività stesse: 'attività', 'ferma 3'."""
        import re

        low = text.lower().strip()
        m = re.match(r"^(?:ferma|stop|interrompi|annulla attivit[aà]|kill|cancel job)\s*#?(\d+)$", low)
        if m:
            self.write(_("Tu › ") + text, "you")
            job = self.jobs.cancel(m.group(1))
            self.write(_("⏹️  Interrompo #{id}…", id=m.group(1)) if job and job.active else
                       _("Nessuna attività in corso con numero {id}.", id=m.group(1)), "warn")
            return True
        if low in ("attivita", "attività", "jobs", "job", "processi", "cosa stai facendo", "tasks", "activity",
                   "what are you doing"):
            self.write(_("Tu › ") + text, "you")
            act = self.jobs.active()
            if not act:
                self.write(_("Nessuna attività in corso."), "reply")
            for j in act:
                self.write(f"#{j.id} {j.title} — {_(PHASES.get(j.phase, j.phase))} {j.done}/{j.total or '?'} "
                           f"({_fmt_secs(j.elapsed)})", "reply")
            return True
        return False

    def run_text(self, text: str, ctx: "Assistant | None" = None, title: str | None = None,
                 echo: bool = True) -> Job:
        if echo:
            self.scroll_to_bottom()
            self.write("")
            self.write(_("Tu › ") + text, "you")
        base = ctx or self.assistant
        holder: dict[str, Job] = {}

        def status(msg: str) -> None:
            if "job" in holder:
                holder["job"].set_message(msg)

        def progress(*a) -> None:
            if "job" in holder:
                holder["job"].progress(*a)

        if ctx is not None:
            # risposta a una conferma: stessa sessione (contiene il piano in sospeso)
            import threading

            child = ctx
            child.progress, child.status, child.cancel = progress, status, threading.Event()
        else:
            child = base.fork(progress=progress, status=status)
        job = self.jobs.start(title or text, "chat", child, lambda j: child.handle(text))
        holder["job"] = job

        async def watch() -> None:
            done, _pending = await asyncio.wait({job.task}, timeout=FOREGROUND_SECONDS)  # type: ignore[arg-type]
            if not done:
                job.background = True
                self.write(_("⏳ #{id} continua in background (box Attività): puoi continuare a scrivere.",
                             id=job.id), "info")
                await job.task  # type: ignore[misc]
            self._show_result(job)

        asyncio.ensure_future(watch())
        return job

    def _show_result(self, job: Job) -> None:
        job.seen = True
        # propagazione dello stato di sessione (es. 'vai in …')
        if job.ctx is not None and job.ctx.directory != self.assistant.directory and job.command == "chat":
            self.assistant.directory = job.ctx.directory
        if job.background:
            self.write("")
            self.write(f"── #{job.id} · {job.title} · {_fmt_secs(job.elapsed)} ──", "info")
        style = {ERROR: "err", CANCELLED: "warn"}.get(job.status, "reply")
        text = job.result_text
        if job.confirmation:
            # la domanda finale la mostra il selettore
            lines = text.rstrip().splitlines()
            text = "\n".join(line for line in lines if not is_question(line)).rstrip()
        self.write(text, style)
        if job.confirmation:
            self._confirm_job(job)
        if job.result_data.get("command") == "exit":
            self._exit()

    # ================================================================ uscita
    def _ctrl_c(self) -> None:
        if self.input.text:
            self.input.text = ""
            return
        active = self.jobs.active()
        if active:
            j = active[0]
            self.jobs.cancel(j.id)
            self.write(_("⏹️  Interrompo #{id} ({title})…", id=j.id, title=j.title), "warn")
            return
        now = time.monotonic()
        if now - self.last_ctrl_c < 1.5:
            self._exit()
        else:
            self.last_ctrl_c = now
            self.write(_("Premi di nuovo Ctrl+C (o Ctrl+D / 'esci') per uscire."), "info")

    def _request_exit(self) -> None:
        active = self.jobs.active()
        if not active:
            self._exit()
            return

        def answered(value: Any) -> None:
            if value:
                self.jobs.cancel_all()
                self._exit()

        self.ask(_("Ci sono {n} attività in corso. Uscire comunque (verranno interrotte)?", n=len(active)),
                 [(True, _("Sì, esci")), (False, _("No, resta"))], 1, answered)

    def _exit(self) -> None:
        if self.app.is_running:
            self.app.exit(result=0)

    async def run(self) -> int:
        ticker = asyncio.ensure_future(self._ticker())
        try:
            await self.app.run_async()
        finally:
            ticker.cancel()
        return self.exit_code
