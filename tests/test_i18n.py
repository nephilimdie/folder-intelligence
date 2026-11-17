import ast
import asyncio
import json
import re
from pathlib import Path

import pytest

from folder_intelligence import i18n
from folder_intelligence.assistant import Assistant
from folder_intelligence.cli import split_question
from folder_intelligence.commands import registry
from folder_intelligence.i18n import _, from_accept_language, set_language
from folder_intelligence.i18n_extract import PACKAGE, dynamic_calls, messages
from folder_intelligence.intents import parse
from folder_intelligence.memory import create_memory

PLACEHOLDER = re.compile(r"\{(\w*)\}")


def catalog(code):
    return json.loads((PACKAGE / "locales" / f"{code}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", [c for c in i18n.SUPPORTED if c != i18n.SOURCE])
def test_every_message_is_translated_with_the_same_placeholders(code):
    cat = catalog(code)
    msgs = messages()
    missing = [m for m in msgs if not cat.get(m)]
    assert not missing, f"{len(missing)} testi senza traduzione {code}: {missing[:5]}"
    for m in msgs:
        assert sorted(PLACEHOLDER.findall(m)) == sorted(PLACEHOLDER.findall(cat[m])), m
        assert ("(sì/no)" in m) == ("(yes/no)" in cat[m]), m  # il marcatore delle domande sì/no


def test_no_stale_translations():
    stale = set(catalog("en")) - set(messages())
    assert not stale, f"traduzioni non più usate: {sorted(stale)[:5]}"


def test_underscore_is_never_reassigned_where_it_means_translate():
    """`_` come variabile ("for _ in", "a, _ = …", "def _(e)") nasconde la traduzione:
    errore subdolo che pyflakes non vede sempre."""
    bad = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "i18n.py":  # qui `_` è definita
            continue
        src = path.read_text(encoding="utf-8")
        if "import" not in src or not re.search(r"from \.+i18n import[^\n]*\b_\b", src):
            continue
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Name) and node.id == "_" and isinstance(node.ctx, ast.Store):
                bad.append(f"{path.name}:{node.lineno}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_":
                bad.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.arg) and node.arg == "_":
                bad.append(f"{path.name}:{node.lineno}")
    assert not bad, bad


def test_dynamic_translations_are_only_known_identifiers():
    # _(variabile) è ammesso solo per descrizioni/esempi/categorie già estratti come N_()
    assert dynamic_calls(), "attesi alcuni _(…) dinamici (categorie, descrizioni dei comandi)"


def test_language_detection_and_accept_language(monkeypatch):
    monkeypatch.setenv("FI_LANG", "en")
    assert i18n.detect() == "en"
    monkeypatch.setenv("FI_LANG", "fr")  # non supportata → inglese
    assert i18n.detect() == "en"
    assert from_accept_language("fr-FR,fr;q=0.9,it;q=0.8,en;q=0.5") == "it"
    assert from_accept_language("en-US,en;q=0.9") == "en"
    assert from_accept_language("de") is None and from_accept_language(None) is None


def test_translation_and_request_scope():
    set_language("en")
    assert _("Confermi?") == "Confirm?"
    assert _("Nessun file duplicato in {root}. ✅", root="/x") == "No duplicate files in /x. ✅"
    token = i18n.use_request_language("it")
    try:
        assert _("Confermi?") == "Confermi?"  # la richiesta API vince sulla lingua del processo
    finally:
        i18n.reset_request_language(token)
    assert _("Confermi?") == "Confirm?"


@pytest.mark.parametrize("code", i18n.SUPPORTED)
def test_examples_are_recognized_by_the_rules_in_every_language(code):
    """Gli esempi (mostrati in 'help' e usati dal router) funzionano anche senza AI, in ogni lingua."""
    set_language(code)
    wrong = []
    for cmd in registry().commands:
        for example in cmd.examples:
            got = parse(_(example)).type
            if got != cmd.name:
                wrong.append(f"{_(example)!r}: {got} invece di {cmd.name}")
    assert not wrong, wrong


def test_english_conversation(sample_dir):
    set_language("en")
    a = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        dup = await a.handle("find duplicates")
        assert "groups of duplicates" in dup and "foto copia.jpg" in dup
        plan = await a.handle("delete the duplicates")
        body, question = split_question(plan)
        assert "(yes/no)" in plan and question.startswith("Confirm?") and a.pending is not None
        done = await a.handle("yes")
        assert "moved to the Trash" in done
        assert "restored" in await a.handle("undo the last operation")
        assert "Write the way you'd talk" in await a.handle("help")
        assert "Here's what I can do" in await a.handle("help all")
        assert (await a.handle("what's in note.txt?")).count("Category:") == 1

    asyncio.run(flow())


def test_organize_uses_folder_names_of_the_language(sample_dir):
    set_language("en")
    a = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        await a.handle("organize the files")
        await a.handle("yes")

    asyncio.run(flow())
    assert (sample_dir / "Documents").is_dir() and not (sample_dir / "Documenti").exists()
    # riorganizzare in italiano non risposta i file già nelle cartelle inglesi
    set_language("it")
    b = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False)
    out = asyncio.run(b.handle("organizza i file"))
    assert "già organizzati" in out or "Documenti" not in out


def test_api_messages_follow_accept_language(tmp_path):
    from fastapi.testclient import TestClient

    from folder_intelligence.api import create_app

    client = TestClient(create_app(None, False, create_memory("inmemory"), default_dir=tmp_path, token="t"))
    h = {"Authorization": "Bearer t"}
    assert client.get("/status", headers={**h, "Accept-Language": "en-GB"}).json()["language"] == "en"
    assert client.get("/status", headers={**h, "Accept-Language": "it"}).json()["language"] == "it"
    r = client.get("/jobs/999", headers={**h, "Accept-Language": "en"})
    assert r.status_code == 404 and r.json()["detail"] == "Job not found"
    r = client.get("/jobs/999?lang=it", headers=h)
    assert r.json()["detail"] == "Job non trovato"
    cmds = client.get("/commands", headers={**h, "Accept-Language": "en"}).json()["items"]
    assert any(c["name"] == "duplicates" and c["description"].startswith("Finds duplicate") for c in cmds)


def test_cli_help_in_english(capsys):
    from folder_intelligence import cli

    with pytest.raises(SystemExit):
        cli._main(["--lang", "en", "--help"])
    out = capsys.readouterr().out
    assert "find duplicates and organize your folders" in out and "language of the interface" in out


def test_locale_files_are_packaged():
    assert (Path(i18n.__file__).parent / "locales" / "en.json").is_file()
