import asyncio
from datetime import date, timedelta

from folder_intelligence import agenda
from folder_intelligence.assistant import Assistant
from folder_intelligence.i18n import set_language
from folder_intelligence.memory import create_memory


def test_items_are_ordered_and_stored_encrypted():
    today = date.today()
    agenda.add("senza data")
    agenda.add("più avanti", today + timedelta(days=30))
    agenda.add("scaduto", today - timedelta(days=2))
    agenda.add("oggi", today)
    assert [i.text for i in agenda.pending()] == ["scaduto", "oggi", "più avanti", "senza data"]
    assert [i.status() for i in agenda.pending()] == ["overdue", "today", "later", "undated"]
    raw = agenda.path().read_bytes()
    assert b"scaduto" not in raw and oct(agenda.path().stat().st_mode)[-3:] == "600"
    item = agenda.set_done(3)
    assert item.done and [i.text for i in agenda.recently_done()] == ["scaduto"]
    assert agenda.remove(1).text == "senza data" and agenda.find(1) is None


def test_format_due_in_both_languages():
    today = date(2026, 11, 17)  # martedì
    assert agenda.format_due(today, today) == "oggi"
    assert agenda.format_due(date(2026, 11, 20), today) == "ven 20 nov"
    assert agenda.format_due(date(2027, 1, 3), today) == "3 gen 2027"
    set_language("en")
    assert agenda.format_due(today + timedelta(days=1), today) == "tomorrow"
    assert agenda.format_due(date(2026, 11, 20), today) == "Fri Nov 20"


def test_chat_flow(tmp_path):
    a = Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        out = await a.handle("ricordami di pagare l'affitto domani")
        assert "Aggiunto all'agenda (#1): pagare l'affitto — domani" in out
        note = await a.handle("ricordati che le bollette vanno in Documents/Casa")
        assert "me lo ricordo" in note and len(agenda.pending()) == 1  # un'informazione, non una cosa da fare
        await a.handle("aggiungi all'agenda sistemare le foto")
        listing = await a.handle("cosa devo fare?")
        assert "pagare l'affitto" in listing and "sistemare le foto" in listing
        assert "Fatto: pagare l'affitto" in await a.handle("fatto 1")
        assert "Tolto dall'agenda" in await a.handle("elimina promemoria 2")
        assert agenda.pending() == []

    asyncio.run(flow())


def test_english_chat_flow(tmp_path):
    set_language("en")
    a = Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        out = await a.handle("remind me to call the bank tomorrow")
        assert "Added to the agenda (#1): call the bank — tomorrow" in out
        assert "Done: call the bank" in await a.handle("done 1")

    asyncio.run(flow())


def test_panel_rendering(tmp_path):
    from folder_intelligence.jobs import JobManager
    from folder_intelligence.tui import ChatTUI

    agenda.add("chiamare il notaio", date.today())
    agenda.add("rinnovare la patente " * 5, date.today() + timedelta(days=3))
    agenda.add("scaduto", date.today() - timedelta(days=1))
    agenda.set_done(agenda.add("fatto ieri").id)
    a = Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False)
    ui = ChatTUI(a, JobManager(), [], mouse=False)
    text = "".join(t for _style, t in ui._render_agenda())
    lines = text.strip().splitlines()
    assert lines[0].startswith("⚠#3") and "ieri" in lines[0]
    assert lines[1].startswith("●#1") and "oggi" in lines[1]
    assert lines[2].endswith("…")  # testo lungo troncato alla larghezza del pannello
    assert lines[-1].startswith("✓#4")


def test_api(tmp_path):
    from fastapi.testclient import TestClient

    from folder_intelligence.api import create_app

    c = TestClient(create_app(None, False, create_memory("inmemory"), default_dir=tmp_path, token="t"))
    h = {"Authorization": "Bearer t", "Accept-Language": "en"}
    item = c.post("/agenda", json={"text": "pay the rent tomorrow"}, headers=h).json()
    assert item["text"] == "pay the rent" and item["due_label"] == "tomorrow" and item["status"] == "soon"
    c.post("/agenda", json={"text": "renew passport", "due": "2030-01-15"}, headers=h)
    assert [i["text"] for i in c.get("/agenda", headers=h).json()["items"]] == ["pay the rent", "renew passport"]
    assert c.patch(f"/agenda/{item['id']}", json={"done": True}, headers=h).json()["done"] is True
    assert c.patch("/agenda/99", json={"done": True}, headers=h).status_code == 404
    assert c.post("/agenda", json={"text": "x", "due": "domani"}, headers=h).status_code == 422
    assert c.delete(f"/agenda/{item['id']}", headers=h).status_code == 204
