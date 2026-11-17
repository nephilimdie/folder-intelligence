from pathlib import Path

from prompt_toolkit.utils import get_cwidth

from folder_intelligence.assistant import Assistant
from folder_intelligence.jobs import JobManager
from folder_intelligence.memory import create_memory
from folder_intelligence.tui import ChatTUI, short_path, wrap_fragments


def make_ui(tmp_path):
    return ChatTUI(Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False), JobManager(), [])


class Key:
    def __init__(self, data):
        self.data = data


def test_letter_selects_but_never_confirms(tmp_path):
    """P0 della critica: una lettera non deve mai spostare file nel Cestino."""
    ui = make_ui(tmp_path)
    answers = []
    ui.ask("Confermi?", [(True, "Sì, sposta nel Cestino"), (False, "No, annulla")], 1, answers.append)
    letter = next(b for b in ui.confirm_control.key_bindings.bindings if b.keys == ("<any>",))
    letter.handler(Key("s"))
    assert answers == [] and ui.confirms[0].selected == 0  # selezionato, non confermato
    enter = next(b for b in ui.confirm_control.key_bindings.bindings if b.keys == ("c-m",))
    enter.handler(Key("\r"))
    assert answers == [True]


def test_input_is_hidden_while_confirming(tmp_path):
    ui = make_ui(tmp_path)
    target = ui.input.__pt_container__()
    containers = [c for c in ui.app.layout.walk() if c.__class__.__name__ == "ConditionalContainer"]
    input_box = next(c for c in containers if c.content is target)
    assert input_box.filter()
    ui.ask("Confermi?", [(True, "Sì"), (False, "No")], 1, lambda v: None)
    assert not input_box.filter()


def test_word_wrap_never_splits_words():
    line = [("", "Trovati 2 gruppi di duplicati in /Users/x/Documents (3 copie, 53 B recuperabili)")]
    rows = ["".join(t for _s, t in r) for r in wrap_fragments(line, 30)]
    assert all(get_cwidth(r) <= 30 for r in rows)
    words = " ".join(r.strip() for r in rows).split()
    assert words == line[0][1].split()  # nessuna parola spezzata o persa


def test_short_path_and_status_fit(tmp_path, monkeypatch):
    assert short_path(Path.home() / "Documents" / "Lavoro") == "~/Documents/Lavoro"
    assert short_path("/a/very/long/path/to/some/deep/folder/Progetti", 20) == "…/Progetti"
    ui = make_ui(tmp_path)
    for cols in (60, 90, 130, 200):
        monkeypatch.setattr(ui, "_columns", lambda c=cols: c)
        text = "".join(t for _s, t in ui._render_status())
        assert get_cwidth(text) <= cols, cols
        assert "Ctrl+T" in text  # le scorciatoie restano sempre visibili


def test_status_hints_follow_context(tmp_path):
    ui = make_ui(tmp_path)
    assert ui._status_hints()[0][0] == "Ctrl+T"
    ui.ask("Confermi?", [(True, "Sì"), (False, "No")], 1, lambda v: None)
    assert [k for k, _a in ui._status_hints()] == ["←/→", "Invio", "Esc"]


def test_history_suggestion_can_be_accepted(tmp_path):
    """Bug segnalato: il suggerimento grigio (testo già scritto) non si poteva accettare."""
    import asyncio

    from prompt_toolkit.auto_suggest import Suggestion

    async def flow():
        ui = make_ui(tmp_path)
        buf = ui.input.buffer
        buf.text = "trova"
        buf.cursor_position = len(buf.text)
        buf.suggestion = Suggestion("mi i duplicati")
        keys = {b.keys for b in ui.app.key_bindings.bindings}
        assert ("right",) in keys and ("c-e",) in keys  # tasti standard di accettazione caricati
        tab = next(b for b in ui.app.key_bindings.bindings
                   if b.keys == ("c-i",) and b.handler.__name__ == "_key_accept")
        tab.handler(Key("\t"))
        assert buf.text == "trovami i duplicati"

    asyncio.run(flow())


def test_selection_mode_freezes_the_screen(tmp_path, monkeypatch):
    """Terminale di macOS annulla la selezione a ogni ridisegno: in modalità selezione niente ridisegni."""
    ui = make_ui(tmp_path)
    redraws = []
    monkeypatch.setattr(ui.app, "invalidate", lambda: redraws.append(1))
    ui.toggle_select_mode()
    redraws.clear()
    for _ in range(20):  # avanzamento di un'attività, nuovi messaggi…
        ui.jobs.touch()
        ui.write("aggiornamento")
    assert redraws == []
    ui.toggle_select_mode()
    assert redraws and not ui.select_mode
    assert "novità arrivate nel frattempo" in "".join(t for line in list(ui.lines)[-1:] for _s, t in line)


def test_useful_commands_box_follows_context(tmp_path):
    """In basso a destra: comandi utili che cambiano con ciò che si sta facendo."""
    import asyncio

    from folder_intelligence.assistant import Assistant
    from folder_intelligence.jobs import JobManager
    from folder_intelligence.memory import create_memory
    from folder_intelligence.tui import ChatTUI

    (tmp_path / "ricevuta_hotel.txt").write_text("ricevuta hotel Roma")
    a = Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False)
    ui = ChatTUI(a, JobManager(), [])
    text = lambda: "".join(t for _s, t in ui._render_tips())  # noqa: E731
    assert "cerca …" in text() and "impostazioni" in text()
    asyncio.run(a.handle("indicizza la cartella"))
    asyncio.run(a.handle("cerca ricevuta hotel"))
    assert "vai ai risultati" in text() and "solo i pdf" in text()
    ui.settings_open = True
    assert "aggiungi una cartella" in text()


def test_side_column_has_fixed_width(tmp_path, monkeypatch):
    """La colonna di destra non si stringe quando la chat ha righe lunghe."""
    from folder_intelligence.assistant import Assistant
    from folder_intelligence.jobs import JobManager
    from folder_intelligence.memory import create_memory
    from folder_intelligence.tui import ChatTUI

    ui = ChatTUI(Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False), JobManager(), [])
    for cols, want in ((120, 42), (200, 50), (300, 56)):
        monkeypatch.setattr(ui, "_columns", lambda c=cols: c)
        assert ui._side_width() == want
