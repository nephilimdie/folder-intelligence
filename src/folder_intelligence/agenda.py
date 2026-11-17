"""Agenda: le cose da fare (promemoria), con una data facoltativa.

Si aggiungono dalla chat ("ricordami di pagare l'affitto venerdì", "remind me to call the bank
tomorrow"), si vedono nel pannello sotto il box Attività e con "agenda", si chiudono con
"fatto 2" e si tolgono con "elimina promemoria 2". Salvata cifrata in `agenda.enc`.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import crypto
from .audit import app_home
from .i18n import _, lang

PURPOSE = "agenda"
_lock = threading.RLock()
_cache: tuple[str, float, list["Item"]] | None = None   # (percorso, mtime, voci)

MONTHS_SHORT = {"it": ["", "gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"],
                "en": ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]}
WEEKDAYS_SHORT = {"it": ["lun", "mar", "mer", "gio", "ven", "sab", "dom"],
                  "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}


@dataclass
class Item:
    id: int
    text: str
    due: str | None = None          # "AAAA-MM-GG" oppure "AAAA-MM-GGTHH:MM" (con orario)
    done: bool = False
    created: str = ""
    done_at: str | None = None
    calendar_uid: str | None = None  # evento corrispondente nel Calendario di macOS

    @property
    def due_date(self) -> date | None:
        try:
            return date.fromisoformat(self.due[:10]) if self.due else None
        except ValueError:
            return None

    @property
    def due_at(self) -> datetime | None:
        """Data e ora, se la scadenza ha un orario."""
        if not self.due or "T" not in self.due:
            return None
        try:
            return datetime.fromisoformat(self.due)
        except ValueError:
            return None

    @property
    def when(self) -> date | datetime | None:
        return self.due_at or self.due_date

    def status(self, today: date | None = None, now: datetime | None = None) -> str:
        """'done' | 'overdue' | 'today' | 'soon' (entro 7 giorni) | 'later' | 'undated'."""
        today = today or date.today()
        if self.done:
            return "done"
        d = self.due_date
        if d is None:
            return "undated"
        at = self.due_at
        if at is not None and at < (now or datetime.now()) and d <= today:
            return "overdue"
        if d < today:
            return "overdue"
        if d == today:
            return "today"
        return "soon" if (d - today).days <= 7 else "later"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "status": self.status(), "due_label": format_due(self.due_at or self.due_date)}


def path() -> Path:
    return app_home() / "agenda.enc"


def load() -> list[Item]:
    global _cache
    p = path()
    with _lock:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return []
        if _cache and _cache[0] == str(p) and _cache[1] == mtime:
            return list(_cache[2])
        import json

        try:
            raw = json.loads(crypto.read_encrypted(p, PURPOSE))
        except Exception:
            return []
        items = [Item(**{k: v for k, v in r.items() if k in Item.__dataclass_fields__}) for r in raw]
        _cache = (str(p), mtime, items)
        return list(items)


def _save(items: list[Item]) -> None:
    global _cache
    import json

    with _lock:
        data = json.dumps([asdict(i) for i in items], ensure_ascii=False).encode()
        crypto.write_encrypted(path(), data, PURPOSE)
        _cache = (str(path()), path().stat().st_mtime, list(items))


def _iso(due: date | datetime | None) -> str | None:
    if due is None:
        return None
    if isinstance(due, datetime):
        return due.isoformat(timespec="minutes")
    return due.isoformat()


def add(text: str, due: date | datetime | None = None) -> Item:
    with _lock:
        items = load()
        item = Item(id=max((i.id for i in items), default=0) + 1, text=text.strip(),
                    due=_iso(due), created=datetime.now().isoformat(timespec="seconds"))
        items.append(item)
        _save(items)
        return item


def find(ref: int | str) -> Item | None:
    try:
        n = int(str(ref).lstrip("#"))
    except ValueError:
        return None
    return next((i for i in load() if i.id == n), None)


def set_done(ref: int | str, done: bool = True) -> Item | None:
    with _lock:
        items = load()
        for i in items:
            if str(i.id) == str(ref).lstrip("#"):
                i.done = done
                i.done_at = datetime.now().isoformat(timespec="seconds") if done else None
                _save(items)
                return i
    return None


def remove(ref: int | str) -> Item | None:
    with _lock:
        items = load()
        for i in items:
            if str(i.id) == str(ref).lstrip("#"):
                items.remove(i)
                _save(items)
                return i
    return None


def update(ref: int | str, text: str | None = None, due: date | datetime | None | str = "keep") -> Item | None:
    with _lock:
        items = load()
        for i in items:
            if str(i.id) == str(ref).lstrip("#"):
                if text:
                    i.text = text.strip()
                if due != "keep":
                    i.due = _iso(due) if isinstance(due, date) else None
                _save(items)
                return i
    return None


# --------------------------------------------------------------- Calendario di macOS
def calendar_add(item: Item) -> str:
    """Crea l'evento nel Calendario; restituisce una riga per l'utente ("" se disattivato)."""
    from . import calendar_mac

    if not calendar_mac.enabled() or item.when is None:
        return ""
    try:
        uid = calendar_mac.create_event(item.text, item.when)
    except calendar_mac.CalendarError as e:
        if str(e) == "permission":
            return _("❗ Evento non creato: concedi l'accesso a Calendario in Impostazioni di Sistema → "
                     "Privacy e sicurezza → Automazione (Terminale → Calendario).")
        return _("❗ Evento non creato nel Calendario: {error}", error=e)
    with _lock:
        items = load()
        for i in items:
            if i.id == item.id:
                i.calendar_uid = uid
        _save(items)
    return _("📆 Evento creato nel Calendario «{name}».", name=calendar_mac.calendar_name())


def calendar_done(item: Item) -> None:
    from . import calendar_mac

    if item.calendar_uid and calendar_mac.enabled():
        try:
            calendar_mac.rename_event(item.calendar_uid, ("✓ " if item.done else "") + item.text)
        except calendar_mac.CalendarError:
            pass


def calendar_remove(item: Item) -> None:
    from . import calendar_mac

    if item.calendar_uid and calendar_mac.enabled():
        try:
            calendar_mac.delete_event(item.calendar_uid)
        except calendar_mac.CalendarError:
            pass


_ORDER = {"overdue": 0, "today": 1, "soon": 2, "later": 3, "undated": 4, "done": 5}


def pending(today: date | None = None) -> list[Item]:
    """Cose da fare non ancora fatte: scadute, oggi, prossimi giorni, poi più avanti, poi senza data."""
    items = [i for i in load() if not i.done]
    return sorted(items, key=lambda i: (_ORDER[i.status(today)], i.due or "9999", i.id))


def recently_done(limit: int = 3) -> list[Item]:
    done = [i for i in load() if i.done]
    return sorted(done, key=lambda i: i.done_at or "", reverse=True)[:limit]


def format_due(when: date | datetime | None, today: date | None = None) -> str:
    """'oggi 18:30', 'domani', 'ven 20 nov', '3 gen 2027' (nella lingua corrente)."""
    if when is None:
        return ""
    clock = f" {when:%H:%M}" if isinstance(when, datetime) else ""
    return _format_day(when.date() if isinstance(when, datetime) else when, today) + clock


def _format_day(d: date, today: date | None = None) -> str:
    today = today or date.today()
    code = lang() if lang() in MONTHS_SHORT else "en"
    delta = (d - today).days
    if delta == 0:
        return _("oggi")
    if delta == 1:
        return _("domani")
    if delta == -1:
        return _("ieri")
    month = MONTHS_SHORT[code][d.month]
    label = f"{d.day} {month}" if code == "it" else f"{month} {d.day}"
    if 0 < delta < 7:
        label = f"{WEEKDAYS_SHORT[code][d.weekday()]} {label}"
    if d.year != today.year:
        label += f" {d.year}"
    return label
