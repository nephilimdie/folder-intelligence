"""Pagina "Impostazioni" della chat a pannelli (finestra sopra la conversazione).

↑/↓ sceglie · Invio/spazio cambia il valore · a aggiunge una cartella · d la toglie · Esc chiude.
Ogni modifica si salva subito (settings.enc, cifrato).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame, TextArea

from . import completion, settings
from .i18n import N_, _, set_language

if TYPE_CHECKING:
    from .tui import ChatTUI

LOAD_LABELS = {"low": N_("solo se il Mac non sta facendo altro"), "medium": N_("anche se il Mac è un po' occupato"),
               "any": N_("sempre (non controllare il carico)")}
LANG_LABELS = {"auto": N_("come macOS"), "it": "italiano", "en": "English"}
MCP_LABELS = {"sealed": N_("solo inventario (sealed)"), "summary": N_("riassunti ripuliti (summary)"),
              "off": N_("niente (off)")}


def _cycle(values: tuple, current: Any) -> Any:
    values = tuple(values)
    return values[(values.index(current) + 1) % len(values)] if current in values else values[0]


class SettingsPage:
    def __init__(self, tui: "ChatTUI") -> None:
        self.tui = tui
        self.selected = 0
        self.message = ""
        self.adding = False

        kb = KeyBindings()

        @kb.add("up")
        def _up(e) -> None:
            self.selected = max(0, self.selected - 1)

        @kb.add("down")
        def _down(e) -> None:
            self.selected = min(len(self.rows()) - 1, self.selected + 1)

        @kb.add("enter")
        @kb.add("space")
        def _activate(e) -> None:
            self.activate()

        @kb.add("a")
        def _add(e) -> None:
            self.start_adding()

        @kb.add("d")
        @kb.add("delete")
        @kb.add("backspace")
        def _remove(e) -> None:
            row = self.rows()[self.selected]
            if row["kind"] == "folder":
                self.remove_folder(row["value"])

        @kb.add("escape", eager=True)
        @kb.add("q")
        def _close(e) -> None:
            self.tui.close_settings()

        self.control = FormattedTextControl(self.render, focusable=True, key_bindings=kb, show_cursor=False)
        self.path_input = TextArea(
            height=1, prompt=_("Cartella da sorvegliare: "), multiline=False,
            completer=completion.prompt_toolkit_completer(lambda: self.tui.assistant.directory),
            complete_while_typing=True, accept_handler=self._accept_folder)
        adding = Condition(lambda: self.adding)
        self.container = Frame(HSplit([Window(self.control, wrap_lines=True),
                                       ConditionalContainer(self.path_input, filter=adding)]),
                               title=lambda: _("🔧 Impostazioni"))

    # ------------------------------------------------------------------ righe
    def rows(self) -> list[dict[str, Any]]:
        s = settings.load()
        rows: list[dict[str, Any]] = [
            {"kind": "section", "label": _("Indicizzazione automatica")},
            {"kind": "toggle", "key": "background_enabled", "label": _("Indicizza in background"),
             "value": _("● attiva") if s.background_enabled else _("○ spenta"), "act": self.toggle_background},
        ]
        rows += [{"kind": "folder", "label": "  📁 " + _short(f), "value": f} for f in s.watched_folders]
        rows.append({"kind": "action", "label": "  " + _("+ aggiungi una cartella… (a)"), "act": self.start_adding})
        rows += [
            {"kind": "choice", "label": _("Quando l'hai lasciato inattivo da"),
             "value": _("{n} minuti", n=s.idle_minutes),
             "act": lambda: self.set(idle_minutes=_cycle(settings.IDLE_CHOICES, s.idle_minutes))},
            {"kind": "choice", "label": _("Solo con l'alimentazione collegata"),
             "value": _("sì") if s.require_ac_power else _("no"),
             "act": lambda: self.set(require_ac_power=not s.require_ac_power)},
            {"kind": "choice", "label": _("Carico del Mac"), "value": _(LOAD_LABELS[s.max_system_load]),
             "act": lambda: self.set(max_system_load=_cycle(settings.LOAD_CHOICES, s.max_system_load))},
            {"kind": "section", "label": _("AI e integrazioni")},
            {"kind": "choice", "label": _("Modello per i riassunti"), "value": s.index_model,
             "act": lambda: self.set_model(_cycle(settings.INDEX_MODEL_CHOICES, s.index_model))},
            {"kind": "choice", "label": _("Eventi nel Calendario"),
             "value": _("no") if s.calendar.lower() == "off" else s.calendar,
             "act": lambda: self.set(calendar="off" if s.calendar.lower() != "off" else "folderIntelligence")},
            {"kind": "choice", "label": _("Lingua"), "value": _(LANG_LABELS[s.language]),
             "act": lambda: self.set_language(_cycle(settings.LANG_CHOICES, s.language))},
            {"kind": "choice", "label": _("Memoria MCP condivisa"), "value": _(MCP_LABELS[s.mcp_mode]),
             "act": lambda: self.set_mcp(_cycle(settings.MCP_CHOICES, s.mcp_mode))},
        ]
        return rows

    def _selectable(self) -> list[int]:
        return [i for i, r in enumerate(self.rows()) if r["kind"] != "section"]

    def render(self) -> FormattedText:
        rows = self.rows()
        sel = self._selectable()
        if self.selected not in sel:
            self.selected = min(sel, key=lambda i: abs(i - self.selected))
        width = max(30, self.tui._win_width(self.tui.output, 70) - 8)
        label_w = min(38, max(len(r["label"]) for r in rows if r["kind"] != "section"))
        out: list[tuple[str, str]] = []
        for i, r in enumerate(rows):
            if r["kind"] == "section":
                out.append(("class:heading", ("\n" if i else "") + r["label"] + "\n"))
                continue
            chosen = i == self.selected
            mark = "❯ " if chosen else "  "
            value = r.get("value", "") if r["kind"] != "folder" else ""
            line = f"{mark}{r['label']:<{label_w}}  {value}"[:width]
            out.append(("class:sel" if chosen else "", line + "\n"))
        out.append(("class:muted", "\n" + _("↑/↓ scegli · Invio cambia · a aggiungi cartella · d togli · Esc chiudi")))
        if self.message:
            out.append(("class:warn", "\n" + self.message))
        return FormattedText(out)

    # ------------------------------------------------------------------ azioni
    def activate(self) -> None:
        row = self.rows()[self.selected]
        act: Callable | None = row.get("act")
        if act:
            act()
        elif row["kind"] == "folder":
            self.message = _("Premi d per non sorvegliare più questa cartella.")

    def set(self, **changes: Any) -> None:
        settings.update(**changes)
        self.message = _("Salvato.")

    def set_model(self, model: str) -> None:
        self.set(index_model=model)
        self.tui.assistant.intelligence.index_model = model

    def set_language(self, code: str) -> None:
        self.set(language=code)
        set_language(None if code == "auto" else code)
        self.message = _("Salvato. Alcuni testi cambiano al prossimo avvio.")

    def set_mcp(self, mode: str) -> None:
        self.set(mcp_mode=mode)
        self.tui.assistant.mcp.mode = mode

    def toggle_background(self) -> None:
        from . import background

        s = settings.load()
        try:
            if s.background_enabled:
                background.uninstall()
                settings.update(background_enabled=False)
                self.message = _("Servizio in background spento.")
            else:
                background.install()
                settings.update(background_enabled=True)
                self.message = (_("Servizio attivo: lavora quando il Mac è inattivo.") if s.watched_folders
                                else _("Servizio attivo. Aggiungi le cartelle da indicizzare (a); intanto "
                                       "completa quelle già indicizzate."))
        except Exception as e:  # noqa: BLE001
            self.message = _("❗ Non riuscito: {error}", error=e)

    def start_adding(self) -> None:
        self.adding = True
        self.path_input.text = ""
        self.tui.app.layout.focus(self.path_input)

    def cancel_adding(self) -> None:
        self.adding = False
        self.tui.app.layout.focus(self.control)

    def _accept_folder(self, buff) -> bool:
        from .completion import unescape_path

        raw = unescape_path(buff.text.strip())
        self.adding = False
        self.tui.app.layout.focus(self.control)
        if not raw:
            return False
        try:
            s = settings.add_folder(raw)
        except NotADirectoryError:
            self.message = _("❗ La cartella {path} non esiste.", path=raw)
            return False
        self.message = (_("Aggiunta. Verrà indicizzata in background quando il Mac è inattivo.")
                        if s.background_enabled else
                        _("Aggiunta. Attiva 'Indicizza in background' perché venga indicizzata da sola."))
        return False

    def remove_folder(self, folder: str) -> None:
        settings.remove_folder(folder)
        self.message = _("Cartella tolta (quanto già indicizzato resta cercabile).")


def _short(path: str) -> str:
    from .tui import short_path

    return short_path(path, 48)
