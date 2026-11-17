"""Box dei risultati nella colonna di destra (sotto Attività e Agenda), uno per ricerca.

- ogni ricerca aggiunge un box; i precedenti restano (al massimo MAX_BOXES, poi esce il più vecchio);
- il box attivo è espanso (elenco + anteprima), gli altri mostrano titolo e prime righe;
- F4 entra nei box dalla chat · Tab/⇧Tab box successivo/precedente · Esc torna alla chat · x chiude;
- nel box: ↑/↓ scegli · Invio apri · spazio Quick Look · f Finder · c copia · ? chiedi · e nel Finder ·
  s salva · / filtra scrivendo.
"""

from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.widgets import Frame

from .i18n import _

if TYPE_CHECKING:
    from .results import Hit, ResultSet
    from .results_area import ResultsArea

DOUBLE_CLICK = 0.45  # secondi: secondo click sulla stessa riga = apri il file
COLLAPSED_ROWS = 3   # righe dei box non attivi


class ResultBox:
    def __init__(self, area: "ResultsArea", rs: "ResultSet") -> None:
        self.area = area
        self.rs = rs
        self.selected = 0
        self.message = ""
        kb = KeyBindings()

        @kb.add("up")
        def _up(e) -> None:
            self.move(-1)

        @kb.add("down")
        def _down(e) -> None:
            self.move(1)

        @kb.add("pageup")
        def _pgup(e) -> None:
            self.move(-8)

        @kb.add("pagedown")
        def _pgdn(e) -> None:
            self.move(8)

        @kb.add("enter")
        def _open(e) -> None:
            self.run(["open"], _("Apro {name}…"))

        @kb.add("space")
        def _quicklook(e) -> None:
            hit = self.current()
            if hit:  # Quick Look in una finestra a parte, senza bloccare la chat
                subprocess.Popen(["qlmanage", "-p", hit.path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.message = _("Anteprima di {name}.", name=hit.name)

        @kb.add("f")
        def _finder(e) -> None:
            self.run(["open", "-R"], _("Mostro {name} nel Finder."))

        @kb.add("c")
        def _copy(e) -> None:
            hit = self.current()
            if hit:
                subprocess.run(["pbcopy"], input=hit.path.encode(), check=False)
                self.message = _("📋 Percorso copiato.")

        @kb.add("?")
        def _ask(e) -> None:
            hit = self.current()
            if hit:
                self.area.tui.focus_input_with(f"su «{hit.name}»: ")

        @kb.add("e")
        def _export(e) -> None:
            self.area.activate(self)
            self.area.tui.run_text(_("metti i risultati nel Finder"))

        @kb.add("s")
        def _save(e) -> None:
            self.area.activate(self)
            self.area.tui.focus_input_with(_("salva questa ricerca come "))

        @kb.add("/")
        def _filter(e) -> None:
            self.area.activate(self)
            self.area.tui.focus_input_with("")
            self.message = _("Scrivi un filtro: 'solo i pdf', 'del 2024'…")

        @kb.add("tab")
        def _next(e) -> None:
            self.area.focus_next(1)

        @kb.add("s-tab")
        def _prev(e) -> None:
            self.area.focus_next(-1)

        @kb.add("escape", eager=True)
        def _back(e) -> None:
            self.area.tui.focus_chat()

        @kb.add("x")
        @kb.add("delete")
        def _close(e) -> None:
            self.area.close(self)

        self._line_hits: list[int | None] = []   # riga disegnata → indice del file (None: intestazione)
        self._last_click: tuple[float, int] = (0.0, -1)
        self.control = FormattedTextControl(self.render, focusable=True, key_bindings=kb, show_cursor=False)
        self.control.mouse_handler = self.on_mouse  # click per scegliere, doppio click per aprire
        self.window = Window(self.control, wrap_lines=False, height=lambda: self._height())
        self.frame = Frame(self.window, title=self.title,
                           style="class:frame.box")

    def on_mouse(self, ev):
        """Click: seleziona il box e il file sotto il puntatore; doppio click: apre; rotellina: scorre."""
        if ev.event_type == MouseEventType.SCROLL_UP:
            self.move(-1)
            return None
        if ev.event_type == MouseEventType.SCROLL_DOWN:
            self.move(1)
            return None
        if ev.event_type != MouseEventType.MOUSE_UP:
            return NotImplemented
        was_active = self.is_active
        self.area.focus(self)
        y = ev.position.y
        idx = self._line_hits[y] if 0 <= y < len(self._line_hits) else None
        if idx is not None:
            now = time.monotonic()
            last_t, last_i = self._last_click
            self.selected = idx
            self.message = ""
            if was_active and last_i == idx and now - last_t < DOUBLE_CLICK:
                self.run(["open"], _("Apro {name}…"))
                self._last_click = (0.0, -1)
            else:
                self._last_click = (now, idx)
        self.area.tui._invalidate()
        return None

    # ------------------------------------------------------------------ stato
    @property
    def is_active(self) -> bool:
        return self.area.active is self

    @property
    def has_focus(self) -> bool:
        try:
            return self.area.tui.app.layout.has_focus(self.control)
        except Exception:
            return False

    def hits(self) -> list["Hit"]:
        return self.rs.visible()

    def current(self) -> "Hit | None":
        hits = self.hits()
        if not hits:
            return None
        self.selected = max(0, min(self.selected, len(hits) - 1))
        return hits[self.selected]

    def move(self, delta: int) -> None:
        n = len(self.hits())
        if n:
            self.selected = max(0, min(n - 1, self.selected + delta))
        self.message = ""

    def run(self, cmd: list[str], msg: str) -> None:
        hit = self.current()
        if hit:
            subprocess.run([*cmd, hit.path], capture_output=True)
            self.message = msg.format(name=hit.name)

    def selected_path(self) -> str | None:
        hit = self.current()
        return hit.path if hit else None

    def title(self) -> str:
        n = self.area.number(self)
        desc = self.rs.filters.describe()
        mark = "▶ " if self.has_focus else ""
        return f"{mark}🔎{n} «{self.rs.query}» · {len(self.hits())}" + (f" · {desc}" if desc else "")

    # ------------------------------------------------------------------ disegno
    def _width(self) -> int:
        info = self.window.render_info
        return max(20, (info.window_width if info else 38))

    def _rows(self) -> list[tuple[int | None, list[tuple[str, str]]]]:
        from .tui import short_path

        width = self._width()
        rows: list[tuple[int | None, list[tuple[str, str]]]] = []
        i = 0
        for header, group in self.rs.groups():
            if header and self.is_active:
                label = header if header == _("Più pertinenti") else short_path(header, width - 6)
                rows.append((None, [("class:heading", f"{label} ({len(group)})")]))
            for h in group:
                chosen = i == self.selected and self.is_active
                when = h.when.strftime("%d/%m/%y")
                title = h.name if h.title in (h.name, h.name.rsplit(".", 1)[0]) else h.title
                room = width - len(when) - 2
                text = title if len(title) <= room else title[: max(6, room - 1)] + "…"
                rows.append((i, [("class:sel" if chosen else "", f"{'❯' if chosen else ' '}{text:<{room}} "),
                                 ("class:sel" if chosen else "class:muted", when)]))
                i += 1
        return rows

    def _height(self) -> D:
        rows = len(self._rows()) if self.rs.visible() else 1
        if not self.is_active:
            return D.exact(max(1, min(rows, COLLAPSED_ROWS)))
        try:
            screen = self.area.tui.app.output.get_size().rows
        except Exception:
            screen = 30
        want = min(rows, max(2, screen - 16)) + (2 if self.has_focus else 1)  # + anteprima e tasti
        return D(min=min(want, 5), preferred=want, max=want)

    def render(self) -> FormattedText:
        from .tui import short_path

        hits = self.hits()
        self._line_hits = []
        if not hits:
            return FormattedText([("class:muted", _("Nessun file con questi filtri ('mostrali tutti')."))])
        rows = self._rows()
        if not self.is_active:
            out: list[tuple[str, str]] = []
            for idx, r in rows[:COLLAPSED_ROWS]:
                out += r + [("", "\n")]
                self._line_hits.append(idx)
            return FormattedText(out)
        # righe disponibili: quelle volute dal box (non quelle del disegno precedente, che poteva
        # essere ancora chiuso), con un tetto sull'altezza dello schermo
        try:
            screen = self.area.tui.app.output.get_size().rows
        except Exception:
            screen = 30
        avail = max(2, min(len(rows), screen - 16))
        sel_row = next((k for k, (idx, _r) in enumerate(rows) if idx == self.selected), 0)
        start = max(0, min(sel_row - avail // 2, len(rows) - avail))
        out = []
        for idx, r in rows[start:start + avail]:
            out += r + [("", "\n")]
            self._line_hits.append(idx)
        width = self._width()
        hit = self.current()
        if self.has_focus and hit:
            preview = (hit.snippet or hit.description or short_path(hit.path, width)).replace("\n", " ").strip()
            out.append(("class:example", preview[: width - 1] + "\n"))
        elif not self.has_focus:
            out.append(("class:muted", _("F4 o click per selezionare")))
        if self.message:
            out.append(("class:warn", "\n" + self.message[: width * 2]))
        return FormattedText(out)
