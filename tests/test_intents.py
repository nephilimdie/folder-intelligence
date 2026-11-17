from pathlib import Path

import pytest

from folder_intelligence.intents import extract_paths, parse


@pytest.mark.parametrize("text,kind", [
    ("trovami i duplicati nella cartella Downloads", "duplicates"),
    ("ci sono doppioni?", "duplicates"),
    ("cancella i duplicati", "dedupe"),
    ("cancella tutto", "dedupe"),
    ("organizza i file", "organize"),
    ("classifica i file", "classify"),
    ("che file ci sono?", "classify"),
    ("mostrami i 5 file più grandi", "largest"),
    ("cerca fattura", "search"),
    ("esporta il report in csv", "export"),
    ("annulla l'ultima operazione", "undo"),
    ("ricordami di sistemare le foto", "remember"),
    ("cosa ricordi?", "recall"),
    ("sì", "confirm"),
    ("no", "cancel"),
    ("esci", "exit"),
    ("aiuto", "help"),
    ("analizza ~/Desktop", "scan"),
    ("vai in /tmp", "change_dir"),
    ("blablabla", "unknown"),
])
def test_parse(text, kind):
    assert parse(text).type == kind


def test_largest_count_and_export_format():
    assert parse("mostrami i 5 file più grandi").args["n"] == 5
    assert parse("esporta in json").args["format"] == "json"


def test_remember_extracts_note():
    assert parse("ricordami di sistemare le foto").args["text"] == "sistemare le foto"


def test_organize_target():
    i = parse("organizza in ~/Archivio")
    assert i.target == Path("~/Archivio").expanduser() and i.path is None


def test_extract_paths(tmp_path):
    (tmp_path / "Downloads").mkdir()
    assert extract_paths("duplicati nella cartella Downloads", home=tmp_path) == [tmp_path / "Downloads"]
    assert extract_paths('analizza "/a b/c"') == [Path("/a b/c")]
    assert extract_paths("esporta json/csv") == []
