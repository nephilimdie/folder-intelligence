"""Righe "Modelli e lettura" della pagina impostazioni (F3): un valore per riga, Invio lo cambia."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import settings
from .i18n import N_, _

if TYPE_CHECKING:
    from .settings_page import SettingsPage

OCR_LABELS = {"auto": N_("automatico (Apple Vision)"), "apple": N_("Apple Vision"), "tesseract": N_("Tesseract"),
              "model": N_("modello locale ({model})")}
RESTART = N_("Salvato. Vale dalla prossima apertura della chat.")


def _next(values: tuple, current: Any) -> Any:
    return values[(values.index(current) + 1) % len(values)] if current in values else values[0]


def model_rows(page: "SettingsPage") -> list[dict[str, Any]]:
    s = settings.load()

    def change(**values: Any) -> None:
        settings.update(**values)
        page.message = _(RESTART)

    def change_embed() -> None:
        change(embed_model=_next(settings.EMBED_MODEL_CHOICES, s.embed_model))
        page.message = _("Salvato. Alla prossima apertura i vettori della ricerca si ricalcolano un po' alla "
                         "volta (la ricerca per parole funziona sempre).")

    return [
        {"kind": "section", "label": _("Modelli e lettura")},
        {"kind": "choice", "label": _("Modello della chat"), "value": s.chat_model,
         "act": lambda: change(chat_model=_next(settings.CHAT_MODEL_CHOICES, s.chat_model))},
        {"kind": "choice", "label": _("Modello per i riassunti"), "value": s.index_model,
         "act": lambda: page.set_model(_next(settings.INDEX_MODEL_CHOICES, s.index_model))},
        {"kind": "choice", "label": _("Lettura del testo (OCR)"),
         "value": _(OCR_LABELS[s.ocr_engine], model=s.ocr_model),
         "act": lambda: change(ocr_engine=_next(settings.OCR_ENGINE_CHOICES, s.ocr_engine))},
        {"kind": "choice", "label": _("Modello per le foto"), "value": s.vision_model,
         "act": lambda: change(vision_model=_next(settings.VISION_MODEL_CHOICES, s.vision_model))},
        {"kind": "choice", "label": _("Modello per i documenti"), "value": s.document_model,
         "act": lambda: change(document_model=_next(settings.DOCUMENT_MODEL_CHOICES, s.document_model))},
        {"kind": "choice", "label": _("Ricerca per significato"), "value": s.embed_model, "act": change_embed},
    ]
