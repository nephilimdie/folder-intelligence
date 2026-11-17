"""Classe base degli estrattori di contenuto."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..ai import AIEngine


@dataclass
class Extraction:
    text: str = ""                 # testo letto (anche via OCR)
    description: str = ""          # descrizione visiva/semantica (modello visivo, servizio esterno)
    metadata: dict[str, Any] = field(default_factory=dict)
    engine: str = ""               # quale estrattore l'ha prodotta

    @property
    def empty(self) -> bool:
        return not (self.text.strip() or self.description.strip() or self.metadata)


@dataclass
class ExtractContext:
    ai: "AIEngine | None" = None
    use_vision: bool = True        # usa il modello visivo per immagini/video/PDF scansionati
    tmpdir: Path = Path("/tmp")    # cartella temporanea per conversioni
    max_chars: int = 20_000        # testo massimo conservato per file
    ocr_lang: str = "ita+eng"

    async def vision_ok(self) -> bool:
        return bool(self.use_vision and self.ai and await self.ai.can_see())


class Extractor(ABC):
    """Un estrattore sa leggere uno o più tipi di file.

    - `name`: identificativo univoco
    - `priority`: più basso = provato prima (es. servizio esterno 10, locali 50, generico 99)
    - `extensions`: estensioni gestite (oppure sovrascrivi `supports`)
    Se `extract` restituisce None o un risultato vuoto si passa all'estrattore successivo.
    """

    name: str = ""
    priority: int = 50
    extensions: frozenset[str] = frozenset()

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions

    async def available(self, ctx: ExtractContext) -> bool:
        return True

    @abstractmethod
    async def extract(self, path: Path, ctx: ExtractContext) -> Extraction | None: ...


def vision_prompt() -> str:
    from ..i18n import language_name

    return (f"Describe in {language_name()}, in 2-4 sentences, what this image shows: what kind of image "
            "it is (photo, screenshot, document, receipt, ticket, diagram…), subjects, places, objects "
            "and the most important readable text (dates, amounts, company names). "
            "Do not identify people by name.")
