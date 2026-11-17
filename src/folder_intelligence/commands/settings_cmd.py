from .. import settings
from ..i18n import N_, _
from ..intents import Intent
from .base import Command, CommandResult, Request


class SettingsCommand(Command):
    name = "settings"
    group = "sistema"
    needs_folder = False
    description = N_("Apre le impostazioni: cartelle da indicizzare in background, quando farlo, modello, calendario, lingua")
    examples = (N_("impostazioni"), N_("indicizza Documents in automatico"))
    keywords = ("impostazioni", "configurazione", "preferenze", "settings", "preferences", "configure",
                "in automatico", "automaticamente", "automatically")
    priority = 14

    def match(self, req: Request):
        if not req.has(*self.keywords):
            return None
        if req.paths:
            return {}
        # "indicizza Documents in automatico": il nome di una cartella nota basta anche da solo
        from pathlib import Path

        from ..intents import HOME_ALIASES

        for word in req.words:
            real = HOME_ALIASES.get(word)
            if real and (Path.home() / real).is_dir():
                return {"path": Path.home() / real}
        return {}

    async def run(self, ctx, intent: Intent) -> str:
        s = settings.load()
        added = None
        # "indicizza Documents in automatico": aggiunge la cartella alle sorvegliate
        if intent.path is not None and intent.path.expanduser().is_dir():
            s = settings.add_folder(intent.path)
            added = str(intent.path.expanduser().resolve())
        lines = []
        if added:
            lines.append(_("📁 {path} verrà indicizzata in automatico in background.", path=added))
            if not s.background_enabled:
                lines.append(_("Attiva 'Indicizza in background' nelle impostazioni (oppure: "
                               "folder-intelligence --install-background)."))
        lines.append(_("🔧 Impostazioni: cartelle sorvegliate {n}, background {state}, inattivo da {idle} minuti.",
                       n=len(s.watched_folders), state=_("attivo") if s.background_enabled else _("spento"),
                       idle=s.idle_minutes))
        return CommandResult("\n".join(lines), {"open_settings": True, "settings": s.to_dict(), "added": added})
