"""La pila dei box dei risultati nella colonna di destra, e quale box è attivo."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.layout import HSplit, Window

from .results_box import ResultBox

if TYPE_CHECKING:
    from .tui import ChatTUI

MAX_BOXES = 5


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
