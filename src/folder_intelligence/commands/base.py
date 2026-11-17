"""Classe base dei comandi (skill) della chat.

Per aggiungere un comando crea un file in questa cartella (o in
~/.folder_intelligence/commands/) con una sottoclasse di `Command`:

    from .base import Command, Request

    class SpazioLiberoCommand(Command):
        name = "free_space"
        description = "Mostra lo spazio libero sul disco"
        examples = ["quanto spazio libero ho?"]
        keywords = ("spazio libero", "disco pieno")

        async def run(self, ctx, intent):
            res = await shell.run(["df", "-h", str(ctx.directory)])   # comando di sistema
            return res.stdout

Viene caricato automaticamente all'avvio. Dentro `run` hai a disposizione `ctx`
(la sessione: cartella corrente, scansioni, motore AI, indice, memoria, audit…),
il modulo `shell` per i comandi di sistema e `ctx.ai` per il modello locale.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..intents import Intent

if TYPE_CHECKING:
    from ..assistant import Assistant


class CommandResult(str):
    """Risposta di un comando: è una stringa (testo per la chat) con in più `.data`,
    un dizionario JSON-serializzabile usato dalle API e dall'app Mac."""

    data: dict[str, Any]

    def __new__(cls, text: str, data: dict[str, Any] | None = None) -> "CommandResult":
        obj = super().__new__(cls, text)
        obj.data = data or {}
        return obj


@dataclass
class Request:
    """La frase dell'utente già pre-elaborata, passata a `Command.match`."""

    text: str                      # originale
    norm: str                      # minuscolo, senza accenti
    words: set[str] = field(default_factory=set)
    paths: list[Path] = field(default_factory=list)

    def has(self, *needles: str) -> bool:
        return any(n in self.norm for n in needles)

    def search(self, pattern: str) -> re.Match | None:
        return re.search(pattern, self.norm)


class Command(ABC):
    """Un comando della chat.

    Attributi:
      name         identificativo univoco (usato anche dal router AI e dalle API)
      description  cosa fa (mostrato in 'aiuto' e dato all'AI per capire le frasi libere)
      examples     frasi di esempio (aiuto + autocompletamento)
      keywords     se una di queste sottostringhe è nella frase → il comando corrisponde
      patterns     espressioni regolari alternative alle keywords
      priority     ordine di prova: più basso = prima (risolve i conflitti tra comandi)
      destructive  True se modifica file (richiede conferma e passa dall'audit)
      hidden       non mostrato in 'aiuto'
    """

    name: str = ""
    description: str = ""
    examples: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    priority: int = 50
    destructive: bool = False
    hidden: bool = False
    params: tuple[str, ...] = ()   # argomenti accettati (documentati in GET /commands)

    def match(self, req: Request) -> dict[str, Any] | None:
        """Restituisce gli argomenti se la frase è per questo comando, altrimenti None.

        Le chiavi speciali `path` e `target` diventano `intent.path`/`intent.target`
        (di default: primo e secondo percorso trovati nella frase).
        """
        if self.keywords and req.has(*self.keywords):
            return {}
        for p in self.patterns:
            if req.search(p):
                return {}
        return None

    @abstractmethod
    async def run(self, ctx: "Assistant", intent: Intent) -> str:
        """Esegue il comando: restituisce testo o `CommandResult(testo, dati)`."""

    # helper comuni -----------------------------------------------------------
    @staticmethod
    def rel(path: Path | str, root: Path) -> str:
        try:
            return str(Path(path).relative_to(root))
        except ValueError:
            return str(path)
