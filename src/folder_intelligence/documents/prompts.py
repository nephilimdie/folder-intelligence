"""Istruzioni per il modello visivo: foto (descrizione) e documenti (descrizione + campi)."""

from __future__ import annotations

from .document_fields import DocumentFields


def _language() -> str:
    from ..i18n import language_name

    return language_name()


def photo_prompt() -> str:
    return (f"Describe in {_language()}, in 2-4 sentences, what this image shows: what kind of image it is "
            "(photo, screenshot, diagram…), subjects, places, objects and any readable text. "
            "Do not identify people by name.")


def document_prompt(ocr_text: str) -> str:
    hint = f"\nText already read by OCR (may contain errors):\n{ocr_text[:1500]}\n" if ocr_text.strip() else ""
    return (f"This image is a document (receipt, invoice, bill, bank statement, ticket, form…).{hint}\n"
            "Answer ONLY with JSON: "
            f'{{"description": "<1-2 sentences in {_language()}: what document it is, from whom, about what>", '
            + DocumentFields.schema_hint() + "}")
