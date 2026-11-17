"""Eventi nel Calendario di macOS per le voci dell'agenda (AppleScript via `osascript`).

- calendario dedicato (FI_CALENDAR, default "folderIntelligence"; "off" per disattivare),
  creato al primo uso;
- voce con orario → evento di 30 minuti con avviso 15 minuti prima; solo data → tutto il giorno;
- "fatto" → l'evento diventa "✓ …"; "elimina promemoria" → l'evento viene cancellato.

Testo e date arrivano allo script come argomenti (mai interpolati nel codice AppleScript).
La prima volta macOS chiede il permesso di controllare Calendario (Privacy e sicurezza →
Automazione): se manca, la voce resta in agenda e viene spiegato come abilitarlo.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

TIMEOUT = 30

_CREATE = r'''
on run argv
    set calName to item 1 of argv
    set theTitle to item 2 of argv
    set y to (item 3 of argv) as integer
    set m to (item 4 of argv) as integer
    set d to (item 5 of argv) as integer
    set h to (item 6 of argv) as integer
    set mi to (item 7 of argv) as integer
    set allDay to (item 8 of argv) is "1"
    tell application "Calendar"
        if not (exists calendar calName) then make new calendar with properties {name:calName}
        set startD to current date
        set day of startD to 1
        set year of startD to y
        set month of startD to m
        set day of startD to d
        set hours of startD to h
        set minutes of startD to mi
        set seconds of startD to 0
        if allDay then
            set e to make new event at end of events of calendar calName with properties {summary:theTitle, start date:startD, end date:startD + 1 * days, allday event:true}
        else
            set e to make new event at end of events of calendar calName with properties {summary:theTitle, start date:startD, end date:startD + 30 * minutes}
            tell e to make new display alarm at end of display alarms with properties {trigger interval:-15}
        end if
        return uid of e
    end tell
end run
'''

_RENAME = r'''
on run argv
    tell application "Calendar"
        set evs to (every event of calendar (item 1 of argv) whose uid is (item 2 of argv))
        repeat with e in evs
            set summary of e to (item 3 of argv)
        end repeat
    end tell
end run
'''

_DELETE = r'''
on run argv
    tell application "Calendar"
        delete (every event of calendar (item 1 of argv) whose uid is (item 2 of argv))
    end tell
end run
'''


class CalendarError(RuntimeError):
    pass


def calendar_name() -> str:
    from . import settings

    return settings.calendar_name()


def enabled() -> bool:
    return sys.platform == "darwin" and calendar_name().lower() not in ("off", "0", "no", "false")


def _osascript(script: str, *args: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run(["osascript", path, *args], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise CalendarError(str(e)) from e
    finally:
        Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        err = r.stderr.strip()
        if "-1743" in err or "not allowed" in err.lower() or "non è autorizzat" in err.lower():
            raise CalendarError("permission")
        raise CalendarError(err[-200:] or f"osascript exit {r.returncode}")
    return r.stdout.strip()


def create_event(title: str, when: date | datetime) -> str:
    """Crea l'evento e ne restituisce l'uid."""
    all_day = not isinstance(when, datetime)
    h, mi = (0, 0) if all_day else (when.hour, when.minute)
    return _osascript(_CREATE, calendar_name(), title, str(when.year), str(when.month), str(when.day),
                      str(h), str(mi), "1" if all_day else "0")


def rename_event(uid: str, title: str) -> None:
    _osascript(_RENAME, calendar_name(), uid, title)


def delete_event(uid: str) -> None:
    _osascript(_DELETE, calendar_name(), uid)
