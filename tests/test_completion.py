
from folder_intelligence import intents
from folder_intelligence.completion import complete, complete_path, current_token, escape_path


def _tree(tmp_path):
    (tmp_path / "Downloads" / "Foto Vacanze").mkdir(parents=True)
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Desktop").mkdir()
    (tmp_path / ".nascosta").mkdir()
    (tmp_path / "file.txt").write_text("x")
    return tmp_path


def test_completes_only_folders_with_tilde(tmp_path):
    home = _tree(tmp_path)
    got = [c.text for c in complete_path("~/D", home, home)]
    assert got == ["~/Desktop/", "~/Documents/", "~/Downloads/"]
    assert complete_path("~/fi", home, home) == []  # i file non vengono proposti
    assert complete_path("~/", home, home)[0].text != "~/.nascosta/"
    assert [c.text for c in complete_path("~/.n", home, home)] == ["~/.nascosta/"]


def test_spaces_are_escaped_and_understood_by_parser(tmp_path):
    home = _tree(tmp_path)
    c = complete_path("~/Downloads/Fo", home, home)
    assert c[0].text == "~/Downloads/Foto\\ Vacanze/"
    assert current_token("analizza ~/Downloads/Foto\\ Va") == "~/Downloads/Foto\\ Va"
    line = f"analizza {escape_path(str(home / 'Downloads' / 'Foto Vacanze'))}"
    assert intents.parse(line).path == home / "Downloads" / "Foto Vacanze"


def test_absolute_and_relative(tmp_path):
    home = _tree(tmp_path)
    abs_c = complete(f"vai in {home}/Doc", home, home)
    assert [c.display for c in abs_c] == ["Documents/"]
    rel = complete("analizza Downloads/F", home / "x", home)
    assert rel == []  # cwd inesistente → nessuna proposta, nessun errore
    rel = complete("analizza Downloads/F", home, home)
    assert rel[0].text == "Downloads/Foto\\ Vacanze/"


def test_commands_at_line_start_and_folder_aliases(tmp_path):
    home = _tree(tmp_path)
    assert [c.text for c in complete("trovam", home, home)] == ["trovami i duplicati"]
    assert {c.display for c in complete("canc", home, home)} == {"cancella i duplicati"}
    after = complete("analizza la cartella Dow", home, home)
    assert after and after[0].text == "Downloads"
    assert intents.parse("analizza la cartella Downloads", home=home).path == home / "Downloads"
    start = complete("", home, home)
    assert len(start) > 10
    c = complete("trov", home, home)[0]
    assert c.start == -4
