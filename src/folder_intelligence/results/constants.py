"""Costanti dei risultati di ricerca: limiti, gruppi di tipi, parole per riconoscerli."""

from __future__ import annotations

from ..i18n import N_

MAX_HITS = 100
HISTORY = 15

TYPE_GROUPS = {
    "pdf": {".pdf"},
    "images": {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".tif", ".tiff", ".bmp"},
    "documents": {".pdf", ".doc", ".docx", ".odt", ".pages", ".rtf", ".txt", ".md"},
    "sheets": {".xls", ".xlsx", ".numbers", ".ods", ".csv"},
    "slides": {".ppt", ".pptx", ".key", ".odp"},
    "video": {".mp4", ".mov", ".m4v", ".mkv", ".avi"},
    "audio": {".mp3", ".m4a", ".wav", ".aac", ".flac", ".opus"},
    "email": {".eml", ".emlx", ".msg"},
}
TYPE_WORDS = {
    "pdf": ("pdf",), "images": ("immagin", "foto", "image", "photo", "picture", "screenshot"),
    "documents": ("document", "testi", "word", "text"), "sheets": ("fogli", "excel", "spreadsheet", "tabell"),
    "slides": ("presentazion", "slide", "keynote", "powerpoint"), "video": ("video", "filmat"),
    "audio": ("audio", "musica", "registrazion", "music"), "email": ("email", "mail", "messagg"),
}
SORT_LABELS = {"relevance": N_("pertinenza"), "date": N_("data"), "name": N_("nome"), "size": N_("dimensione")}
