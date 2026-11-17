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
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.auto_suggest import load_auto_suggest_bindings
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
          "classify": N_("🤖 classifico"), "analyze": N_("🔎 lettura"), "enrich": N_("🧠 comprensione"),
          "fingerprint": N_("📷 impronte"), "compare": N_("🔗 confronto")}
EXIT_WORDS = {"esci", "exit", "quit", "q", "basta", "chiudi", "bye", "close"}

STYLE = Style.from_dict({
    "you": "bold",
    "reply": "ansicyan",
    "info": "",                      # testo secondario: colore normale del terminale (contrasto pieno)
    "muted": "ansibrightblack",      # solo per l'accessorio: tempi, voci fatte, cornici
    "warn": "ansiyellow",
    "err": "ansired",
    "jobhead": "bold",
    "title": "bold ansicyan",
    "heading": "bold",
    "example": "italic ansicyan",
    "starter": "ansicyan",
    "due": "ansicyan",
    "today": "bold ansiyellow",
    "overdue": "bold ansired",
    "done": "ansibrightblack",
    "frame.border": "ansibrightblack",
    "frame.label": "bold",
    "sel": "reverse bold ansicyan",
    "opt": "bold",
    "question": "bold",
    "hint": "ansibrightblack",
    "bar": "ansicyan",
    "bar.empty": "ansibrightblack",
    "status": "reverse",
    "frame.box": "",
    "status.key": "reverse bold",
    "select": "reverse bold ansiyellow",
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


def short_path(path, limit: int = 40) -> str:
    """'/Users/me/Documents/Lavoro' → '~/Documents/Lavoro'; se troppo lungo '~/…/Lavoro'."""
    from pathlib import Path

    p = str(path)
    home = str(Path.home())
    if p == home or p.startswith(home + "/"):
        p = "~" + p[len(home):]
    if len(p) <= limit:
        return p
    last = p.rstrip("/").rsplit("/", 1)[-1]
    head = "~/…/" if p.startswith("~") else "…/"
    return (head + last) if len(head + last) <= limit else "…" + last[-(limit - 1):]


def wrap_fragments(line: list[tuple[str, str]], width: int) -> list[list[tuple[str, str]]]:
    """Va a capo per parole (mai a metà parola, salvo parole più lunghe della riga),
    mantenendo gli stili e il rientro iniziale della riga."""
    import re

    from prompt_toolkit.utils import get_cwidth

    text = "".join(t for _s, t in line)
    if get_cwidth(text) <= width:
        return [line]
    indent = len(text) - len(text.lstrip(" "))
    hang = " " * min(indent + 2, max(0, width // 3))
    rows: list[list[tuple[str, str]]] = [[]]
    used = 0
    for style, frag in line:
        for token in re.findall(r"\S+\s*|\s+", frag):
            w = get_cwidth(token)
            if used and used + get_cwidth(token.rstrip()) > width:
                rows.append([("", hang)] if hang else [])
                used = len(hang)
                token = token.lstrip(" ") or token
                w = get_cwidth(token)
            while w > width - used and w > 0:  # parola più lunga della riga: si spezza
                cut = max(1, width - used)
                rows[-1].append((style, token[:cut]))
                rows.append([("", hang)] if hang else [])
                used = len(hang)
                token = token[cut:]
                w = get_cwidth(token)
            if token:
                rows[-1].append((style, token))
                used += w
    return rows


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
        self.mouse = mouse
        self.select_mode = False   # Ctrl+T / F2: mouse al terminale per selezionare e copiare testo
        self.last_reply = ""
        self._frozen_changes = False
        self.exit_code = 0

        for i, line in enumerate(header):
            if isinstance(line, tuple):
                self.write(line[1], line[0])
            else:
                self.write(line, "title" if i == 0 else "info")
        self.write("")

        # ------------------------------------------------------------- input
        self.input = TextArea(
            height=1, prompt=_("Tu › "), multiline=False, wrap_lines=False,
            completer=completion.prompt_toolkit_completer(lambda: self.assistant.directory),
            complete_while_typing=False, auto_suggest=AutoSuggestFromHistory(),
            history=enc_history.prompt_toolkit_history(), accept_handler=self._on_accept,
            focus_on_click=True,  # click sulla riga di input: si torna a scrivere nella chat
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
            # la lettera iniziale sposta solo la selezione: per confermare serve Invio
            # (chi inizia a scrivere una frase non deve mai spostare file nel Cestino per sbaglio)
            key = e.data.lower()
            for i, (_value, label) in enumerate(self.confirms[0].options):
                if label.strip()[:1].lower() == key:
                    self.confirms[0].selected = i
                    self._invalidate()
                    return

        self.confirm_control = FormattedTextControl(self._render_confirm, focusable=True,
                                                    key_bindings=confirm_kb, show_cursor=False)
        self.confirm_window = Window(self.confirm_control, wrap_lines=True, dont_extend_height=True)
        has_confirm = Condition(lambda: bool(self.confirms))

        # ------------------------------------------------------------ pannello
        # pannelli laterali: alti quanto il contenuto (un box vuoto non ruba lo schermo)
        self.jobs_window = Window(FormattedTextControl(self._render_jobs), wrap_lines=True,
                                  height=lambda: D.exact(self._content_height(self._render_jobs, 14)))
        self.agenda_window = Window(FormattedTextControl(self._render_agenda), wrap_lines=True,
                                    height=lambda: D.exact(self._content_height(self._render_agenda,
                                                                                AGENDA_MAX_LINES + 3)))
        self.panel = Frame(self.jobs_window, title=self._jobs_title)
        self.agenda_panel = Frame(self.agenda_window, title=lambda: _("📅 Agenda ({n})", n=len(agenda.pending())))
        # andamento dell'indicizzazione automatica (servizio in background, cartelle sorvegliate)
        from .idle_indexer import IdleIndexer

        # a chat aperta ma ferma, il lavoro del servizio lo fa la chat stessa (tiene lei l'indice)
        self.idle_indexer = IdleIndexer(assistant, busy=lambda: bool(self.jobs.active() or self.confirms),
                                        on_change=self._on_idle_change)
        self.input.buffer.on_text_changed += lambda _b: self.idle_indexer.touch()
        self._bg_cache: tuple[float, FormattedText] | None = None
        self.bg_window = Window(FormattedTextControl(self._render_background), wrap_lines=False,
                                height=lambda: D.exact(self._content_height(self._render_background, 10)))
        self.bg_panel = Frame(self.bg_window, title=lambda: _("🌙 Indicizzazione automatica"))
        # in basso a destra: i comandi utili adesso (cambiano con ciò che si sta facendo)
        self.tips_window = Window(FormattedTextControl(self._render_tips), wrap_lines=True,
                                  height=lambda: D.exact(self._content_height(self._render_tips, 12)))
        self.tips_panel = Frame(self.tips_window, title=lambda: _("💡 Comandi utili"))

        conversation = HSplit([self.output, self.scroll_hint])
        # la riga di input sparisce mentre c'è una conferma: un solo posto dove agire
        prompt_area = HSplit([
            Window(height=1, char="─", style="class:muted"),
            ConditionalContainer(self.confirm_window, filter=has_confirm),
            ConditionalContainer(self.input, filter=~has_confirm),
        ])

        def body():
            if self._columns() >= WIDE_MIN_COLUMNS:
                side = HSplit([self.panel, self.agenda_panel, self.bg_panel,
                               DynamicContainer(self.results_area.container), Window(),
                               self.tips_panel],
                              width=lambda: D.exact(self._side_width()))  # fissa: la chat non la stringe
                return VSplit([HSplit([conversation, prompt_area]), side])
            # terminale stretto: i box stanno sopra l'input (che resta in fondo), solo se servono
            show = Condition(lambda: bool(self.jobs.active()))
            show_agenda = Condition(lambda: bool(agenda.pending()))
            # stessi riquadri del layout largo: larghezza e altezza misurate sul contenuto reale
            return HSplit([conversation,
                           ConditionalContainer(Frame(self.jobs_window, title=self._jobs_title), filter=show),
                           ConditionalContainer(Frame(self.agenda_window,
                                                      title=lambda: _("📅 Agenda ({n})", n=len(agenda.pending()))),
                                                filter=show_agenda),
                           DynamicContainer(self.results_area.container),
                           prompt_area])

        # pagina impostazioni: finestra sopra la conversazione (F3 o "impostazioni")
        from .settings_page import SettingsPage

        from .results_area import ResultsArea

        self.settings_open = False
        self.settings_page = SettingsPage(self)
        settings_visible = Condition(lambda: self.settings_open)
        # box dei risultati nella colonna di destra, uno per ricerca (F4 per entrarci)
        self.results_area = ResultsArea(self)
        root = FloatContainer(
            content=HSplit([DynamicContainer(body),
                            Window(FormattedTextControl(self._render_status), height=1, style="class:status")]),
            floats=[Float(top=1, left=4, right=4,
                          content=ConditionalContainer(self.settings_page.container, filter=settings_visible)),
                    Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=10, scroll_offset=1))],
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

        # suggerimento grigio dalla cronologia: Tab lo accetta (se non c'è un menu di completamenti);
        # → / Ctrl+E / Ctrl+F arrivano da load_auto_suggest_bindings più sotto
        suggestion = Condition(lambda: bool(self.input.buffer.suggestion)
                               and self.input.buffer.document.is_cursor_at_the_end
                               and self.input.buffer.complete_state is None)

        @kb.add("tab", filter=has_focus(self.input) & suggestion)
        def _key_accept(e) -> None:
            buf = self.input.buffer
            buf.insert_text(buf.suggestion.text)

        @kb.add("c-t")
        @kb.add("f2")
        def _key_select(e) -> None:
            self.toggle_select_mode()

        @kb.add("escape", filter=Condition(lambda: self.select_mode), eager=True)
        def _key_select_exit(e) -> None:
            self.toggle_select_mode()

        @kb.add("f4")
        def _key_results(e) -> None:
            # dalla chat si entra nei box dei risultati; da un box si torna alla chat
            if self.results_area.focused_box():
                self.focus_chat()
            else:
                self.open_results()

        @kb.add("f3")
        def _key_settings(e) -> None:
            self.close_settings() if self.settings_open else self.open_settings()

        @kb.add("escape", filter=has_focus(self.settings_page.path_input), eager=True)
        def _key_settings_cancel(e) -> None:
            self.settings_page.cancel_adding()

        @kb.add("c-y")
        def _key_copy(e) -> None:
            self.copy_last_reply()

        @kb.add("escape", filter=scrolled & ~has_confirm, eager=True)
        @kb.add("end", filter=scrolled & ~has_confirm & Condition(lambda: not self.input.text))
        def _key12(e) -> None:
            self.scroll_to_bottom()

        self.app: Application = Application(
            layout=Layout(root, focused_element=self.input),
            # i tasti di accettazione del suggerimento non sono inclusi di default in un'Application
            key_bindings=merge_key_bindings([load_auto_suggest_bindings(), kb]), style=STYLE,
            full_screen=True,
            # il mouse si "cattura" (rotellina) tranne in modalità selezione: lì lo gestisce il terminale
            mouse_support=Condition(lambda: self.mouse and not self.select_mode),
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

    def write_reply(self, text: str, style: str, command: str | None = None) -> None:
        """Risposta con un minimo di gerarchia: titoli in grassetto, esempi tra virgolette in corsivo."""
        if command != "help":
            self.write(text, style)
            return
        for line in str(text).split("\n"):
            stripped = line.strip()
            if stripped.startswith('"') or stripped.startswith("«"):
                self.write(line, "example")
            elif line and not line[0].isalnum() and not line.startswith((" ", '"', "(")):
                self.write(line, "heading")  # titoli dei gruppi ("🔍 Analisi")
            else:
                self.write(line, style)

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
        # così ogni passo di scorrimento sposta subito la vista. Le righe vanno a capo per
        # parole (non a metà parola), con il rientro della riga originale.
        visible = list(self.lines)[: max(1, len(self.lines) - self.scroll)]
        width = self._win_width(self.output, self._columns() - (self._side_width() if self._columns() >= WIDE_MIN_COLUMNS else 0))
        out: list[tuple[str, str]] = []
        rows = 0
        for line in visible:
            for wrapped in wrap_fragments(line, width):
                if rows:
                    out.append(("", "\n"))
                out.extend(wrapped)
                rows += 1
        self._rendered_rows = max(1, rows)
        return FormattedText(out)

    def _cursor(self) -> Point:
        return Point(x=0, y=max(0, getattr(self, "_rendered_rows", 1) - 1))  # ultima riga visibile

    @staticmethod
    def _win_width(window: Window, fallback: int) -> int:
        info = window.render_info
        return max(10, info.window_width if info else fallback)

    @staticmethod
    def _content_height(render, maximum: int) -> int:
        text = "".join(t for _style, t in render()).rstrip("\n")
        return max(1, min(maximum, text.count("\n") + 1))

    def _jobs_title(self) -> str:
        waiting = sum(1 for j in self.jobs.list() if j.confirmation)
        n = len(self.jobs.active()) + waiting
        return _("Attività ({n})", n=n)

    def _columns(self) -> int:
        try:
            return self.app.output.get_size().columns
        except Exception:
            return 120

    def _invalidate(self) -> None:
        # in modalità selezione lo schermo resta fermo: Terminale di macOS annulla la selezione
        # a ogni ridisegno (spinner, avanzamento…); gli aggiornamenti arrivano all'uscita
        if self.select_mode:
            self._frozen_changes = True
            return
        try:
            self.app.invalidate()
        except Exception:
            pass

    # ================================================================ jobs
    def _render_jobs(self) -> FormattedText:
        inner = self._win_width(self.jobs_window, PANEL_WIDTH - 4) if hasattr(self, "jobs_window") else PANEL_WIDTH - 4
        width = max(10, inner - 10)
        active = self.jobs.active()
        # tra i recenti solo ciò che è andato in background o aspetta una risposta:
        # i comandi istantanei ("aiuto") hanno già la risposta nella conversazione
        recent = [j for j in self.jobs.list() if not j.active and (j.background or j.confirmation)][:4]
        out: list[tuple[str, str]] = []
        if not active and not recent:
            return FormattedText([("class:muted", _("Nessuna attività in corso."))])
        for j in active:
            spin = SPINNER[self.tick % len(SPINNER)]
            title = j.title if len(j.title) <= width + 8 else j.title[: width + 7] + "…"
            out += [("class:jobhead", f"#{j.id} {spin} {title}\n")]
            phase = _(PHASES.get(j.phase, j.phase)) or (j.message[:width + 8] if j.message else _("avvio…"))
            count = f" {j.done}/{j.total}" if j.total else (f" {j.done}" if j.done else "")
            out += [("", f"   {phase}{count}\n   ")] + _bar(j.fraction, width) + [("class:muted", f" {_fmt_secs(j.elapsed)}\n")]
            if j.detail:
                d = j.detail.rsplit("/", 1)[-1]
                out += [("class:muted", f"   {d[: width + 6]}\n")]
            out += [("", "\n")]
        if recent:
            out += [("class:heading", _("Recenti") + "\n")]
            for j in recent:
                icon = {DONE: "✅", ERROR: "❌", CANCELLED: "🛑 "}.get(j.status, "•")
                if j.confirmation:
                    icon = "❓"
                secs = _fmt_secs(j.elapsed)
                room = width + 6 - len(f"#{j.id}") - len(secs) - 2  # titolo + tempo sulla stessa riga
                title = j.title if len(j.title) <= room else j.title[: max(4, room - 1)] + "…"
                out += [("", f"#{j.id} {icon} {title}"), ("class:muted", f" {secs}\n")]
        if active:
            out += [("class:muted", "\n" + _("'ferma {id}' interrompe #{id}", id=active[0].id))]
        return FormattedText(out)

    # ================================================================ agenda
    def _render_agenda(self) -> FormattedText:
        # larghezza reale del riquadro (non una costante): nessun troncamento inutile
        width = self._win_width(self.agenda_window, PANEL_WIDTH - 4) if hasattr(self, "agenda_window") \
            else PANEL_WIDTH - 4
        todo = agenda.pending()
        done = agenda.recently_done(2)
        if not todo and not done:
            return FormattedText([("class:muted", _("Niente in agenda.") + "\n"),
                                  ("class:muted", _("Prova: ricordami di … domani alle 10"))])
        out: list[tuple[str, str]] = []
        style = {"overdue": "class:overdue", "today": "class:today", "soon": "class:due", "later": "class:due"}
        dates = [agenda.format_due(i.when) for i in todo[:AGENDA_MAX_LINES]]
        date_w = max((len(d) for d in dates), default=0)  # colonna della data larga quanto serve
        for item, when in zip(todo[:AGENDA_MAX_LINES], dates):
            st = item.status()
            # stato anche con un segno, non solo col colore: ! scaduto, ● oggi
            mark = "!" if st == "overdue" else "●" if st == "today" else " "
            head = f"{mark}{item.id:>2} "
            date_col = f"{when:<{date_w}} " if date_w else ""
            room = width - len(head) - len(date_col)
            text = item.text if len(item.text) <= room else item.text[: max(4, room - 1)] + "…"
            out += [(style.get(st, "class:muted") if mark != " " else "class:muted", head),
                    (style.get(st, ""), date_col), ("", text + "\n")]
        if len(todo) > AGENDA_MAX_LINES:
            out.append(("class:muted", _("… e altri {n}", n=len(todo) - AGENDA_MAX_LINES) + "\n"))
        for item in done:
            text = item.text if len(item.text) <= width - 5 else item.text[: width - 6] + "…"
            out.append(("class:done", f"✓{item.id:>2} {text}\n"))
        return FormattedText(out)

    def open_settings(self) -> None:
        self.settings_open = True
        self.settings_page.message = ""
        self.app.layout.focus(self.settings_page.control)
        self._invalidate()

    def open_results(self) -> None:
        if not self.results_area.focus():
            self.write(_("Nessuna ricerca da mostrare: scrivi 'cerca …'."), "info")

    def focus_chat(self) -> None:
        self.app.layout.focus(self.input)
        self._invalidate()

    def focus_input_with(self, text: str) -> None:
        """Porta il cursore nella riga di input con un testo già scritto (il riquadro resta visibile)."""
        self.input.text = text
        self.input.buffer.cursor_position = len(text)
        self.app.layout.focus(self.input)
        self._invalidate()

    def close_settings(self) -> None:
        self.settings_open = False
        self.settings_page.adding = False
        self.app.layout.focus(self.input)
        self._invalidate()

    def _status_hints(self) -> list[tuple[str, str]]:
        """Al massimo 3 scorciatoie, quelle utili in questo momento: (tasto, azione)."""
        if self.settings_open:
            return [("↑/↓", _("scegli")), (_("Invio"), _("cambia")), ("Esc", _("chiudi"))]
        if self.results_area.focused_box():
            return [(_("Invio"), _("apri")), ("Tab", _("box successivo")), ("Esc", _("chat"))]
        if self.confirms:
            return [("←/→", _("scegli")), (_("Invio"), _("conferma")), ("Esc", _("annulla"))]
        if self.select_mode:
            return [(_("trascina"), _("seleziona")), ("⌘C", _("copia")), ("Ctrl+T/Esc", _("fine selezione"))]
        if self.scroll:
            return [("Esc", _("torna in fondo")), (_("PagSu/PagGiù"), _("scorri")), ("Ctrl+T", _("seleziona"))]
        return [("Ctrl+T", _("seleziona testo")), ("Ctrl+Y", _("copia risposta")), (_("aiuto"), _("comandi"))]

    def _on_idle_change(self) -> None:
        self._bg_cache = None
        self._invalidate()

    def _render_background(self) -> FormattedText:
        """Stato del servizio, avanzamento per cartella sorvegliata, ultimi eventi (ricalcolato ogni 5 s)."""
        now = time.monotonic()
        if self._bg_cache and now - self._bg_cache[0] < 5:
            return self._bg_cache[1]
        try:
            text = FormattedText(self._background_lines())
        except Exception:  # un problema qui non deve mai rompere lo schermo
            text = FormattedText([("class:muted", _("Stato non disponibile."))])
        self._bg_cache = (now, text)
        return text

    def _background_lines(self) -> list[tuple[str, str]]:
        from datetime import datetime
        from pathlib import Path

        from . import background, settings

        prefs = settings.load()
        snap = background.snapshot()
        width = self._win_width(self.bg_window, self._side_width() - 2) if hasattr(self, "bg_window") \
            else self._side_width() - 2
        out: list[tuple[str, str]] = []
        if not prefs.watched_folders:
            return [("class:muted", _("Nessuna cartella sorvegliata.") + "\n"),
                    ("class:muted", _("F3 → aggiungi una cartella"))]
        work = self.idle_indexer.state
        if not prefs.background_enabled:
            out.append(("class:warn", _("○ spenta: F3 per attivarla") + "\n"))
        elif work.get("phase") == "enrich":
            out.append(("class:today", _("● al lavoro: comprensione {done}/{total}", done=work.get("done", 0),
                                         total=work.get("total", 0)) + "\n"))
            if work.get("file"):
                out.append(("class:muted", f"  {work['file']}"[:width] + "\n"))
        elif work.get("phase") == "scan":
            out.append(("class:today", _("● al lavoro: controllo le cartelle") + "\n"))
        elif work.get("phase") == "vectors":
            out.append(("class:today", _("● al lavoro: ricerca per significato ({n} file)",
                                         n=work.get("remaining", 0)) + "\n"))
        elif work.get("phase") == "done":
            out.append(("class:today", _("✓ tutto letto e compreso") + "\n"))
        elif work.get("phase") == "paused":
            reason = {"battery": _("Mac a batteria"), "busy": _("Mac impegnato")}.get(work.get("reason"), "")
            out.append(("class:muted", _("○ in pausa: {reason}", reason=reason) + "\n"))
        elif work.get("phase") in ("waiting", "enrich-next") and self.jobs.active():
            out.append(("class:muted", _("○ in attesa: la chat sta lavorando") + "\n"))
        elif work.get("phase") in ("waiting", "enrich-next"):
            secs = max(0, int(self.idle_indexer.idle_seconds - (time.monotonic() - self.idle_indexer.last_activity)))
            out.append(("class:muted", (_("○ riparte tra {n} s se non scrivi", n=secs) if secs
                                        else _("○ sto per ripartire…")) + "\n"))
        index = self.assistant.intelligence.index
        for folder in prefs.watched_folders[:4]:
            total, understood = index.progress(Path(folder))
            name = Path(folder).name or folder
            if not total:
                line = f"{name[:width - 18]:<{max(4, width - 18)}} " + _("da leggere")
                out.append(("", line[:width] + "\n"))
                continue
            pct = understood * 100 // total
            label = f"{understood}/{total}"
            bar_w = max(4, min(10, width - len(label) - 16))
            fill = round(bar_w * understood / total)
            room = max(4, width - bar_w - len(label) - 8)
            shown = name if len(name) <= room else name[: room - 1] + "…"
            out += [("", f"{shown:<{room}} "), ("class:today" if pct == 100 else "class:due", "█" * fill),
                    ("class:muted", "░" * (bar_w - fill)), ("class:muted", f" {pct:>3}% {label}\n")]
        if len(prefs.watched_folders) > 4:
            out.append(("class:muted", _("… e altre {n}", n=len(prefs.watched_folders) - 4) + "\n"))
        labels = {"scanned": _("+{n} file resi cercabili"), "enriched": _("+{n} file compresi dall'AI"),
                  "tagged": _("+{n} file aggiunti alle etichette")}
        for ev in reversed(snap["events"][-2:]):
            try:
                at = datetime.fromisoformat(ev["at"]).strftime("%d/%m %H:%M")
            except (KeyError, ValueError):
                continue
            msg = labels.get(ev.get("code"), "").format(n=ev.get("n", 0))
            if msg:
                out.append(("class:muted", f"{at} {msg}"[:width] + "\n"))
        if out:
            out[-1] = (out[-1][0], out[-1][1].rstrip("\n"))
        return out

    def _side_width(self) -> int:
        """Larghezza fissa della colonna di destra: 42 colonne, fino a 56 sugli schermi larghi."""
        return max(PANEL_WIDTH, min(56, self._columns() // 4))

    def _tips(self) -> list[tuple[str, str]]:
        """Comandi utili nel contesto attuale: (tasto o frase da scrivere, a cosa serve)."""
        if self.settings_open:
            return [("↑/↓", _("scegli una voce")), (_("Invio"), _("cambia il valore")),
                    ("a", _("aggiungi una cartella")), ("Esc", _("chiudi le impostazioni"))]
        if self.confirms:
            return [("←/→", _("scegli la risposta")), (_("Invio"), _("conferma")), ("Esc", _("annulla"))]
        if self.select_mode:
            return [(_("trascina"), _("seleziona il testo")), ("⌘C", _("copia")), ("Ctrl+T", _("fine selezione"))]
        if self.results_area.focused_box():
            return [(_("Invio/2 click"), _("apri il file")), (_("spazio"), _("anteprima Quick Look")),
                    ("f", _("mostra nel Finder")), ("c", _("copia il percorso")), ("?", _("chiedi su questo file")),
                    ("e", _("etichetta nel Finder")), ("s", _("salva la ricerca")), ("/", _("filtra scrivendo")),
                    ("Tab", _("box successivo")), ("x", _("chiudi il box")), ("Esc", _("torna alla chat"))]
        if self.assistant.last_results is not None:
            return [("F4", _("vai ai risultati (o click)")), (_("solo i pdf"), _("filtra l'ultima ricerca")),
                    (_("ordina per data"), _("riordina")),
                    (_("etichetta questi risultati come …"), _("etichetta nel Finder")),
                    (_("salva questa ricerca come …"), _("ritrovala dopo")), ("F3", _("impostazioni"))]
        return [(_("cerca …"), _("trova file per contenuto")), (_("indicizza la cartella"), _("leggi i file")),
                (_("trova i duplicati"), _("libera spazio")), (_("ricordami di …"), _("agenda")),
                ("F3", _("impostazioni")), (_("aiuto"), _("tutti i comandi"))]

    def _render_tips(self) -> FormattedText:
        from prompt_toolkit.utils import get_cwidth

        width = self._win_width(self.tips_window, self._side_width() - 2) if hasattr(self, "tips_window") \
            else self._side_width() - 2
        tips = self._tips()
        out: list[tuple[str, str]] = []
        for i, (key, what) in enumerate(tips):
            end = "\n" if i < len(tips) - 1 else ""
            if get_cwidth(key) + 2 + get_cwidth(what) <= width:
                out += [("class:heading", key), ("class:muted", f"  {what}{end}")]
            else:  # frase lunga: la spiegazione va a capo, rientrata, invece di spezzarsi a metà parola
                out += [("class:heading", key + "\n"), ("class:muted", f"  {what}{end}")]
        return FormattedText(out)

    def _render_status(self) -> FormattedText:
        from prompt_toolkit.utils import get_cwidth

        cols = self._columns()
        n = len(self.jobs.active())
        style = "class:select" if self.select_mode else "class:status"
        pairs = self._status_hints()
        while True:  # su terminali stretti si mostrano meno scorciatoie, mai tagliate a metà
            hints: list[tuple[str, str]] = []
            for key, action in pairs:
                hints += [(style + " class:status.key" if not self.select_mode else style, f" {key} "),
                          (style, f"{action} ")]
            hint_w = sum(get_cwidth(t) for _st, t in hints)
            if hint_w <= cols - 12 or len(pairs) == 1:
                break
            pairs = pairs[:-1]
        left = (" " + (_("📋 Selezione testo") if self.select_mode else short_path(self.assistant.directory))
                + ("  " + _("⏳ {n} in corso", n=n) if n else "") + " ")
        room = cols - hint_w
        if get_cwidth(left) > room:  # prima si accorcia il percorso, le scorciatoie restano
            left = " " + short_path(self.assistant.directory, max(4, room - 3)) + " "
        pad = max(0, cols - hint_w - get_cwidth(left))
        return FormattedText([(style, left + " " * pad)] + hints)

    # ============================================================ selezione testo
    def toggle_select_mode(self) -> None:
        if not self.mouse:
            self.write(_("Il mouse è già lasciato al terminale (--no-mouse): seleziona pure il testo."), "info")
            return
        entering = not self.select_mode
        if entering:
            self.write(_("📋 Modalità selezione: lo schermo resta fermo, trascina per selezionare e ⌘C per copiare. "
                         "Ctrl+T o Esc per tornare."), "info")
            self.select_mode = True
        else:
            self.select_mode = False
            pending = getattr(self, "_frozen_changes", False)
            self._frozen_changes = False
            self.write(_("👆 Rotellina di nuovo attiva.") + (" " + _("Aggiornato con le novità arrivate nel frattempo.")
                                                            if pending else ""), "info")
        try:
            self.app.invalidate()  # un ridisegno per applicare il cambio (mouse, barra gialla)
        except Exception:
            pass

    def copy_last_reply(self) -> None:
        import subprocess

        if not self.last_reply:
            self.write(_("Non c'è ancora una risposta da copiare."), "info")
            return
        try:
            subprocess.run(["pbcopy"], input=self.last_reply.encode(), check=True, timeout=5)
            self.write(_("📋 Ultima risposta copiata negli appunti."), "info")
        except (OSError, subprocess.SubprocessError):
            self.write(_("Impossibile copiare negli appunti (pbcopy non disponibile)."), "err")

    def _on_jobs_changed(self) -> None:
        self._invalidate()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.4)  # abbastanza vivo, senza inondare i lettori di schermo
            if self.jobs.active() and not self.select_mode:
                self.tick += 1
                self._invalidate()
            elif self._bg_cache and time.monotonic() - self._bg_cache[0] > 5 and not self.select_mode:
                self._invalidate()  # box dell'indicizzazione automatica: si aggiorna da solo

    # ============================================================== conferme
    def _render_confirm(self) -> FormattedText:
        if not self.confirms:
            return FormattedText([])
        c = self.confirms[0]
        out: list[tuple[str, str]] = [("class:question", f"{c.question}\n")]
        for i, (_value, label) in enumerate(c.options):
            chosen = i == c.selected
            out.append(("class:sel" if chosen else "class:opt", f" {'❯' if chosen else ' '} {label} "))
            out.append(("", "   "))
        if len(self.confirms) > 1:
            out.append(("class:muted", _("(+{n} in attesa)", n=len(self.confirms) - 1)))
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
        self.idle_indexer.touch()
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
            self.write(_("🛑 Interrompo #{id}…", id=m.group(1)) if job and job.active else
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
        # messaggi intermedi di un'attività lunga (es. "ricerca pronta") direttamente in chat
        child.notify = lambda msg: self.write(msg, "info")
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
        self._bg_cache = None  # l'indicizzazione appena fatta in chat cambia l'avanzamento
        if job.result_data.get("open_results") and job.status == DONE:
            # i file vanno nel riquadro Risultati; in chat resta una riga di riepilogo
            # il box appare a destra senza rubare il cursore: si continua a scrivere
            self.write(job.result_data.get("summary") or text.splitlines()[0], "reply")
            self._invalidate()
        else:
            self.write_reply(text, style, job.result_data.get("command"))
        if text.strip() and job.status == DONE and job.result_data.get("command") != "unknown":
            self.last_reply = text  # Ctrl+Y copia risposte vere, non "Non ho capito"
        if job.confirmation:
            self._confirm_job(job)
        if job.result_data.get("open_settings"):
            self.open_settings()
        if job.result_data.get("command") == "exit":
            self._exit()

    # ================================================================ uscita
    def _ctrl_c(self) -> None:
        if self.input.text:
            self.input.text = ""
            return
        active = self.jobs.active()
        if active:
            j = active[0]  # la più recente
            self.jobs.cancel(j.id)
            self.write(_("🛑 Interrompo #{id} ({title})…", id=j.id, title=j.title), "warn")
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
        idle_work = asyncio.ensure_future(self.idle_indexer.run_forever())
        try:
            await self.app.run_async()
        finally:
            ticker.cancel()
            idle_work.cancel()
            self.idle_indexer.touch()  # ferma il file in corso
        return self.exit_code
