import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from folder_intelligence import results, settings
from folder_intelligence.assistant import Assistant
from folder_intelligence.jobs import JobManager
from folder_intelligence.memory import create_memory

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="etichette del Finder solo su macOS")


def make_tree(tmp_path):
    root = tmp_path / "Documenti"
    (root / "Viaggi").mkdir(parents=True)
    (root / "Downloads").mkdir()
    files = {
        "Viaggi/ricevuta_hotel_roma.txt": "ricevuta hotel Roma soggiorno 189 euro, 14/03/2025",
        "Viaggi/ricevuta_hotel_milano.pdf": None,
        "Downloads/ricevuta_hotel_booking.txt": "ricevuta hotel booking conferma, 02/02/2024",
        "Viaggi/foto_ricevuta_hotel.png": None,
        "Viaggi/ricevuta_hotel_napoli.txt": "ricevuta hotel Napoli, 10/05/2025",
    }
    for rel, text in files.items():
        p = root / rel
        p.write_bytes(text.encode() if text else b"%PDF-1.4 ricevuta hotel milano")
    old = time.time() - 400 * 86400
    os.utime(root / "Downloads/ricevuta_hotel_booking.txt", (old, old))
    return root


def chat(root):
    return Assistant(root, memory=create_memory("inmemory"), use_ai=False)


def test_search_creates_a_result_set_and_refining_by_words(tmp_path):
    root = make_tree(tmp_path)
    a = chat(root)

    async def flow():
        await a.handle("indicizza la cartella")
        out = await a.handle("cerca ricevuta hotel")
        assert out.data["open_results"] and a.last_results is not None
        total = len(a.last_results.visible())
        assert total >= 4
        out = await a.handle("solo i pdf")
        assert [h.name for h in a.last_results.visible()] == ["ricevuta_hotel_milano.pdf"]
        await a.handle("mostrali tutti")
        assert len(a.last_results.visible()) == total
        await a.handle("togli quelli in Downloads")
        assert all("Downloads" not in h.path for h in a.last_results.visible())
        await a.handle("ordina per nome")
        names = [h.name for h in a.last_results.visible()]
        assert names == sorted(names, key=str.lower)
        assert "ricevuta hotel" in [h["query"] for h in results.history()]

    asyncio.run(flow())


def test_refine_without_a_search_explains(tmp_path):
    assert "Prima fai una ricerca" in asyncio.run(chat(tmp_path).handle("solo i pdf"))


def test_groups_put_most_relevant_first():
    hits = [results.Hit(path=f"/x/{d}/f{i}.txt", title=f"f{i}", score=1 - i / 10) for i, d in
            enumerate(["A", "B", "B", "C", "C", "C"])]
    rs = results.ResultSet("q", "/x", hits)
    groups = rs.groups()
    assert groups[0][0] == "Più pertinenti" and len(groups[0][1]) == 3
    assert groups[1][0] == "/x/C" and len(groups[1][1]) == 3


@mac_only
def test_finder_tag_is_added_and_removed_without_moving_files(tmp_path):
    f = tmp_path / "bolletta.pdf"
    f.write_bytes(b"x")
    res = results.to_finder([str(f)], "Bollette Test", mode="tag")
    assert res["tagged"] == 1 and f.exists()
    assert "Bollette Test" in [t.split("\n")[0] for t in results.read_tags(f)]
    assert results.remove_from_finder([str(f)], "Bollette Test") == 1
    assert results.read_tags(f) == []


def test_alias_folder_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(results.subprocess, "run", lambda *a, **k: None)  # niente Finder durante i test
    settings.update(results_destination="aliases", aliases_folder=str(tmp_path / "Risultati"))
    f1, f2 = tmp_path / "a" / "x.pdf", tmp_path / "b" / "x.pdf"
    for f in (f1, f2):
        f.parent.mkdir()
        f.write_text("x")
    res = results.to_finder([str(f1), str(f2)], "Ricevute/hotel")
    folder = Path(res["folder"])
    assert res["mode"] == "aliases" and folder.name == "Ricevute hotel" and res["added"] == 2
    links = sorted(folder.iterdir())
    assert [p.name for p in links] == ["x (2).pdf", "x.pdf"] and all(p.is_symlink() for p in links)
    assert f1.exists() and f2.exists()  # gli originali restano dove sono


def test_put_in_finder_from_chat_saves_the_search(tmp_path, monkeypatch):
    root = make_tree(tmp_path)
    calls = []
    monkeypatch.setattr(results, "to_finder", lambda paths, name, mode=None, open_finder=True: calls.append(
        (paths, name, mode)) or {"mode": "tag", "name": name, "added": len(paths), "tagged": len(paths), "failed": []})
    a = chat(root)

    async def flow():
        await a.handle("indicizza la cartella")
        await a.handle("cerca ricevuta hotel")
        await a.handle("solo i pdf")
        out = await a.handle("etichetta questi risultati come Ricevute")
        assert "etichettati «Ricevute»" in out and calls[0][1] == "Ricevute"
        assert calls[0][0] == [str(root / "Viaggi/ricevuta_hotel_milano.pdf")]  # solo i visibili (filtrati)
        saved = results.get_saved("Ricevute")
        assert saved.destination == "tag" and saved.filters["types"] == ["pdf"]
        out = await a.handle("ricerche salvate")
        assert "Ricevute" in out
        out = await a.handle("apri la ricerca Ricevute")
        assert out.data["open_results"] and [h.name for h in a.last_results.visible()] == ["ricevuta_hotel_milano.pdf"]

    asyncio.run(flow())


def test_result_boxes_stack_and_switch(tmp_path, monkeypatch):
    """Ogni ricerca aggiunge un box nella colonna di destra; i precedenti restano; Tab cambia box."""
    from folder_intelligence.tui import ChatTUI

    root = make_tree(tmp_path)
    a = chat(root)
    asyncio.run(a.handle("indicizza la cartella"))
    asyncio.run(a.handle("cerca ricevuta hotel"))
    asyncio.run(a.handle("cerca nota spese"))
    ui = ChatTUI(a, JobManager(), [])
    area = ui.results_area
    area.sync()
    assert [b.rs.query for b in area.boxes] == ["ricevuta hotel", "nota spese"]
    assert area.active is area.boxes[-1]  # l'ultima ricerca è quella attiva
    titles = [b.title() for b in area.boxes]
    assert titles[0].startswith("🔎1 «ricevuta hotel»") and titles[1].startswith("🔎2 «nota spese»")
    # il box non attivo è chiuso a poche righe, quello attivo mostra tutto
    assert area.boxes[0]._height().max <= 3
    area.focus(area.boxes[0])
    assert area.active is area.boxes[0] and a.last_results.query == "ricevuta hotel"
    asyncio.run(a.handle("solo i pdf"))  # i filtri a parole agiscono sul box selezionato
    assert area.boxes[0].rs.filters.types == ["pdf"] and area.boxes[1].rs.filters.types == []
    area.focus_next(1)
    assert area.active is area.boxes[1]
    opened = []
    monkeypatch.setattr("folder_intelligence.results_box.subprocess.run", lambda cmd, **k: opened.append(cmd))
    area.boxes[1].run(["open", "-R"], "x {name}")
    assert opened[0][:2] == ["open", "-R"] and opened[0][2] == area.boxes[1].selected_path()
    area.close(area.boxes[1])
    assert [b.rs.query for b in area.boxes] == ["ricevuta hotel"]
    text = "".join(t for _s, t in area.boxes[0].render())
    assert "ricevuta" in text.lower()


def test_result_boxes_react_to_mouse_clicks(tmp_path, monkeypatch):
    """Click su un box: lo seleziona e sceglie il file; doppio click: lo apre; rotellina: scorre."""
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    from folder_intelligence.tui import ChatTUI

    root = make_tree(tmp_path)
    a = chat(root)
    asyncio.run(a.handle("indicizza la cartella"))
    asyncio.run(a.handle("cerca ricevuta hotel"))
    asyncio.run(a.handle("cerca nota spese"))
    ui = ChatTUI(a, JobManager(), [])
    area = ui.results_area
    area.sync()
    first = area.boxes[0]

    def click(box, y, kind=MouseEventType.MOUSE_UP):
        box.render()  # come al disegno: righe → file
        return box.on_mouse(MouseEvent(Point(x=3, y=y), kind, MouseButton.LEFT, frozenset()))

    click(first, 1)  # box chiuso: riga 1 = secondo file
    assert area.active is first and first.selected == 1 and a.last_results is first.rs
    opened = []
    monkeypatch.setattr("folder_intelligence.results_box.subprocess.run", lambda cmd, **k: opened.append(cmd))
    first.render()
    line = first._line_hits.index(2)  # box aperto: le intestazioni dei gruppi non sono file
    click(first, line)
    assert first.selected == 2 and not opened
    click(first, line)  # doppio click
    assert opened and opened[0][0] == "open" and opened[0][1] == first.selected_path()
    click(first, 0, MouseEventType.SCROLL_DOWN)
    assert first.selected == 3


def test_empty_question_on_a_file_does_nothing(tmp_path):
    """'su «file»:' senza domanda (tasto ? poi Invio) non deve diventare un piano dell'AI con etichette."""
    from folder_intelligence.commands import registry
    from folder_intelligence.router import Router

    root = make_tree(tmp_path)
    a = chat(root)
    asyncio.run(a.handle("indicizza la cartella"))
    asyncio.run(a.handle("cerca ricevuta hotel"))
    out = asyncio.run(a.handle("su «ricevuta_hotel_roma.txt»:"))
    assert "Scrivi la domanda" in out and results.saved() == []
    class NoAI:  # l'AI non deve nemmeno essere interpellata
        model = "x"

        async def is_available(self):
            raise AssertionError("AI interpellata")

        async def generate_json(self, *a, **k):
            raise AssertionError("AI interpellata")

    plan = asyncio.run(Router(registry(), NoAI(), mode="ai").route("su «ricevuta_hotel_roma.txt»: quanto ho pagato?"))
    assert plan.source == "shortcut" and [s.type for s in plan.steps] == ["ask"]
