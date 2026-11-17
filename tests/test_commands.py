import asyncio

import pytest

from folder_intelligence.assistant import Assistant
from folder_intelligence.commands import Command, registry
from folder_intelligence.intents import parse
from folder_intelligence.memory import create_memory


def test_all_commands_loaded_with_unique_names():
    reg = registry(reload=True)
    names = [c.name for c in reg.commands]
    assert len(names) == len(set(names))
    for expected in ("scan", "duplicates", "dedupe", "organize", "index", "search", "ask", "describe",
                     "history", "undo", "help", "confirm", "cancel"):
        assert expected in names
    for c in reg.commands:
        assert isinstance(c, Command) and c.description


@pytest.mark.parametrize("text,kind", [
    ("indicizza la cartella", "index"),
    ("analizza il contenuto dei file", "index"),
    ("cosa contiene fattura_2024.pdf?", "describe"),
    ("descrivi IMG_2041.HEIC", "describe"),
    ("cerca la ricevuta dell'hotel di Roma", "search"),
    ("quanto ho pagato l'hotel?", "ask"),
    ("quando scade il contratto?", "ask"),
    ("che file ci sono?", "classify"),
    ("quanto spazio occupa?", "summary"),
])
def test_new_phrases(text, kind):
    assert parse(text).type == kind


def test_describe_extracts_filename():
    assert parse("cosa contiene fattura_2024.pdf?").args["file"] == "fattura_2024.pdf"


def test_user_plugin_is_loaded(tmp_path, sample_dir, monkeypatch):

    plugin_dir = tmp_path / "fi_home" / "commands"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "spazio_libero.py").write_text('''
from folder_intelligence import shell
from folder_intelligence.commands.base import Command


class FreeSpaceCommand(Command):
    name = "free_space"
    description = "Mostra lo spazio libero sul disco"
    examples = ("quanto spazio libero ho?",)
    keywords = ("spazio libero",)
    priority = 5

    async def run(self, ctx, intent):
        res = await shell.run(["df", "-k", str(ctx.directory)])
        return "DISCO:" + res.stdout.splitlines()[-1]
''')
    reg = registry(reload=True)
    try:
        assert reg.get("free_space") is not None
        a = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False, commands=reg)
        reply = asyncio.run(a.handle("quanto spazio libero ho?"))
        assert reply.startswith("DISCO:")
        assert "spazio libero" in reg.help_text()
    finally:
        for f in plugin_dir.glob("*.py"):
            f.unlink()
        registry(reload=True)


def test_broken_plugin_does_not_break_app(tmp_path):
    plugin_dir = tmp_path / "fi_home" / "commands"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "rotto.py").write_text("this is not python(")
    try:
        assert registry(reload=True).get("scan") is not None
    finally:
        (plugin_dir / "rotto.py").unlink()
        registry(reload=True)


def test_shell_never_uses_a_shell():
    from folder_intelligence import shell

    res = asyncio.run(shell.run(["echo", "a; rm -rf /tmp/nonexistent && echo pwned"]))
    assert res.stdout.strip() == "a; rm -rf /tmp/nonexistent && echo pwned"
    assert asyncio.run(shell.run(["comando-che-non-esiste"])).code == 127
