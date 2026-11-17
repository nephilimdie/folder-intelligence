"""Box dei risultati nella colonna di destra (sotto Attività e Agenda), uno per ricerca.

- ogni ricerca aggiunge un box; i precedenti restano (al massimo MAX_BOXES, poi esce il più vecchio);
- il box attivo è espanso (elenco + anteprima), gli altri mostrano titolo e prime righe;
- F4 entra nei box dalla chat · Tab/⇧Tab box successivo/precedente · Esc torna alla chat · x chiude;
- nel box: ↑/↓ scegli · Invio apri · spazio Quick Look · f Finder · c copia · ? chiedi · e nel Finder ·
  s salva · / filtra scrivendo.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.widgets import Frame

from .i18n import _

if TYPE_CHECKING:
    from .results import Hit, ResultSet
    from .tui import ChatTUI

MAX_BOXES = 5
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

        self.control = FormattedTextControl(self.render, focusable=True, key_bindings=kb, show_cursor=False)
        self.window = Window(self.control, wrap_lines=False, height=lambda: self._height())
        self.frame = Frame(self.window, title=self.title,
                           style="class:frame.box")

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
        want = min(rows, max(2, screen - 16)) + (3 if self.has_focus else 1)  # + anteprima e tasti
        return D(min=min(want, 5), preferred=want, max=want)

    def render(self) -> FormattedText:
        from .tui import short_path

        hits = self.hits()
        if not hits:
            return FormattedText([("class:muted", _("Nessun file con questi filtri ('mostrali tutti')."))])
        rows = self._rows()
        if not self.is_active:
            out: list[tuple[str, str]] = []
            for _i, r in rows[:COLLAPSED_ROWS]:
                out += r + [("", "\n")]
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
        for _i, r in rows[start:start + avail]:
            out += r + [("", "\n")]
        width = self._width()
        hit = self.current()
        if self.has_focus and hit:
            preview = (hit.snippet or hit.description or short_path(hit.path, width)).replace("\n", " ").strip()
            out.append(("class:example", preview[: width - 1] + "\n"))
            out.append(("class:muted", _("spazio anteprima · f Finder · ? chiedi · e nel Finder · x chiudi")))
        elif not self.has_focus:
            out.append(("class:muted", _("F4 per selezionare")))
        if self.message:
            out.append(("class:warn", "\n" + self.message[: width * 2]))
        return FormattedText(out)


class ResultsArea:
    """La pila dei box dei risultati e il box attivo (su cui agiscono i filtri a parole)."""

    def __init__(self, tui: "ChatTUI") -> None:
        self.tui = tui
        self.boxes: list[ResultBox] = []
        self.active: ResultBox | None = None

    def sync(self) -> None:
        """Allinea i box ai risultati della sessione (nuove ricerche, ricerche riaperte, chiusure)."""
        sets = self.tui.assistant.shared.setdefault("result_sets", [])
        known = {id(b.rs): b for b in self.boxes}
        self.boxes = [known.get(id(rs)) or ResultBox(self, rs) for rs in sets][-MAX_BOXES:]
        active_rs = self.tui.assistant.last_results
        self.active = next((b for b in self.boxes if b.rs is active_rs), self.boxes[-1] if self.boxes else None)

    def number(self, box: ResultBox) -> int:
        sets = self.tui.assistant.shared.get("result_sets", [])
        return sets.index(box.rs) + 1 if box.rs in sets else 0

    def activate(self, box: ResultBox) -> None:
        self.active = box
        self.tui.assistant.last_results = box.rs  # i filtri a parole agiscono su questo box

    def container(self):
        self.sync()
        return HSplit([b.frame for b in self.boxes]) if self.boxes else Window(height=0)

    def focus(self, box: ResultBox | None = None) -> bool:
        self.sync()
        box = box or self.active or (self.boxes[-1] if self.boxes else None)
        if box is None:
            return False
        self.activate(box)
        self.tui.app.layout.focus(box.control)
        self.tui._invalidate()
        return True

    def focus_next(self, step: int) -> None:
        self.sync()
        if not self.boxes:
            return
        cur = self.boxes.index(self.active) if self.active in self.boxes else len(self.boxes) - 1
        self.focus(self.boxes[(cur + step) % len(self.boxes)])

    def close(self, box: ResultBox) -> None:
        sets = self.tui.assistant.shared.get("result_sets", [])
        if box.rs in sets:
            sets.remove(box.rs)
        if self.tui.assistant.last_results is box.rs:
            self.tui.assistant.shared["last_results"] = sets[-1] if sets else None
        self.sync()
        if self.boxes:
            self.focus(self.active)
        else:
            self.tui.focus_chat()

    def focused_box(self) -> ResultBox | None:
        return next((b for b in self.boxes if b.has_focus), None)
