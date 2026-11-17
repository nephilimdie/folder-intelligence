import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from folder_intelligence.cli import split_question
from folder_intelligence.selector import select

OPTS = [(True, "Sì, procedi"), (False, "No, annulla")]


def choose(keys: str, default: int = 0):
    async def go():
        with create_pipe_input() as inp:
            inp.send_text(keys)
            return await select("Confermi?", OPTS, default, input=inp, output=DummyOutput())

    return asyncio.run(go())


@pytest.mark.parametrize("keys,default,expected", [
    ("\r", 0, True),                 # Invio sul default
    ("\r", 1, False),
    ("\x1b[C\r", 0, False),          # freccia destra + Invio
    ("\x1b[C\x1b[D\r", 0, True),     # destra, sinistra, Invio
    ("\x1b[B\r", 0, False),          # freccia giù
    ("s", 1, True),                  # scorciatoia 's'
    ("n", 0, False),                 # scorciatoia 'n'
    ("\x1b", 0, None),               # Esc = annulla
    ("\x03", 0, None),               # Ctrl+C = annulla
])
def test_selector_keys(keys, default, expected):
    assert choose(keys, default) is expected


def test_split_question():
    body, q = split_question("Sposterò 2 copie:\n  a\n\nConfermi? (sì/no) — potrai annullare.")
    assert body == "Sposterò 2 copie:\n  a" and q == "Confermi? — potrai annullare."
    body, q = split_question("Per riassumere devo leggere.\n\nVuoi che li legga ora? (sì/no)")
    assert q == "Vuoi che li legga ora?" and "(sì/no)" not in body
