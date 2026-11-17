import asyncio

from folder_intelligence import background, settings
from folder_intelligence.assistant import Assistant
from folder_intelligence.index import Index
from folder_intelligence.jobs import JobManager
from folder_intelligence.memory import create_memory


def test_settings_are_encrypted_validated_and_persisted(tmp_path):
    folder = tmp_path / "Documenti"
    folder.mkdir()
    settings.update(idle_minutes=7, language="xx", max_system_load="boh")
    settings.add_folder(folder)
    settings.add_folder(folder)  # niente doppioni
    s = settings.load()
    assert s.idle_minutes == 5 and s.language == "auto" and s.max_system_load == "low"
    assert s.watched_folders == [str(folder.resolve())]
    assert b"Documenti" not in settings.path().read_bytes()  # percorsi personali cifrati
    settings.remove_folder(folder)
    assert settings.load().watched_folders == []


def test_environment_overrides_settings(monkeypatch):
    settings.update(index_model="qwen2.5:14b", calendar="Lavoro", idle_minutes=10)
    monkeypatch.delenv("FI_CALENDAR", raising=False)
    assert settings.calendar_name() == "Lavoro" and settings.idle_minutes() == 10
    monkeypatch.setenv("FI_INDEX_MODEL", "qwen2.5:7b")
    assert settings.index_model() == "qwen2.5:7b"


def test_system_busy_blocks_background_work(monkeypatch):
    monkeypatch.setattr(background, "app_wants_index", lambda: False)
    monkeypatch.setattr(background, "on_ac_power", lambda: True)
    monkeypatch.setattr(background, "idle_seconds", lambda: 3600)
    monkeypatch.setattr(settings.os, "getloadavg", lambda: (50.0, 1, 1))
    assert background.can_work() == (False, "busy")  # il Mac sta lavorando ad altro
    settings.update(max_system_load="any")
    assert background.can_work() == (True, "")
    settings.update(require_ac_power=True)
    monkeypatch.setattr(background, "on_ac_power", lambda: False)
    assert background.can_work() == (False, "battery")
    settings.update(require_ac_power=False)
    assert background.can_work() == (True, "")


class FakeAI:
    model = "fake"

    def __init__(self):
        self.summaries = 0

    async def is_available(self):
        return True

    async def can_see(self):
        return False

    async def can_embed(self):
        return False

    async def generate_json(self, prompt, system="", schema=None, model=None):
        self.summaries += 1
        return {"category": "documenti", "title": "capito", "description": "d", "tags": []}


def test_background_service_indexes_watched_folders_on_its_own(tmp_path, monkeypatch):
    folder = tmp_path / "Archivio"
    folder.mkdir()
    (folder / "lettera.txt").write_text("lettera di dimissioni")
    settings.add_folder(folder)
    monkeypatch.setattr(background, "can_work", lambda: (True, ""))
    ai = FakeAI()
    asyncio.run(background.run_service(once=True, ai=ai))
    doc = Index().get(folder / "lettera.txt")
    assert doc is not None and doc["title"] == "capito"  # letto e compreso senza aprire la chat
    # un file aggiunto dal Finder viene raccolto al giro successivo; uno cancellato esce dall'indice
    (folder / "nuovo.txt").write_text("contratto nuovo")
    (folder / "lettera.txt").unlink()
    asyncio.run(background.run_service(once=True, ai=ai))
    assert Index().get(folder / "nuovo.txt") is not None and Index().get(folder / "lettera.txt") is None


def test_settings_command_adds_folder(tmp_path):
    folder = tmp_path / "Foto"
    folder.mkdir()
    a = Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False)
    out = asyncio.run(a.handle(f"indicizza {folder} in automatico"))
    assert out.data["open_settings"] and str(folder.resolve()) in settings.load().watched_folders
    assert "verrà indicizzata in automatico" in out


def test_settings_page_interactions(tmp_path, monkeypatch):
    from folder_intelligence.tui import ChatTUI

    installed = []
    monkeypatch.setattr(background, "install", lambda: installed.append("on") or "plist")
    monkeypatch.setattr(background, "uninstall", lambda: installed.append("off") or True)
    ui = ChatTUI(Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False), JobManager(), [])
    page = ui.settings_page
    text = "".join(t for _s, t in page.render())
    assert "Indicizza in background" in text and "○ spenta" in text
    toggle = next(i for i, r in enumerate(page.rows()) if r.get("label") == "Indicizza in background")
    page.selected = toggle
    page.activate()
    assert installed == ["on"] and settings.load().background_enabled
    idle = next(i for i, r in enumerate(page.rows()) if "inattivo" in r.get("label", ""))
    page.selected = idle
    page.activate()
    assert settings.load().idle_minutes == 10  # 5 → 10
    folder = tmp_path / "Lavoro"
    folder.mkdir()

    class Buf:
        text = str(folder)

    page._accept_folder(Buf())
    assert str(folder.resolve()) in settings.load().watched_folders
    page.remove_folder(str(folder.resolve()))
    assert settings.load().watched_folders == []


def test_settings_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from folder_intelligence.api import create_app

    monkeypatch.setattr(background, "install", lambda: "plist")
    c = TestClient(create_app(None, False, create_memory("inmemory"), default_dir=tmp_path, token="t"))
    h = {"Authorization": "Bearer t"}
    assert c.put("/settings", json={"idle_minutes": 15, "max_system_load": "medium"}, headers=h).json()["idle_minutes"] == 15
    assert c.post("/settings/folders", json={"path": str(tmp_path)}, headers=h).json()["watched_folders"] == [str(tmp_path.resolve())]
    assert c.post("/settings/folders", json={"path": "/non/esiste"}, headers=h).status_code == 404
    r = c.post("/settings/background", json={"enabled": True}, headers=h).json()
    assert r["background_enabled"] is True and "background" in r
    assert c.delete("/settings/folders", params={"path": str(tmp_path)}, headers=h).json()["watched_folders"] == []


def test_native_folder_picker(monkeypatch):
    """'+ aggiungi una cartella' apre la finestra di macOS che parte dalla home."""
    import subprocess
    from pathlib import Path

    from folder_intelligence import settings_page

    seen = {}

    def fake(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="/Users/x/Documents/\n", stderr="")

    monkeypatch.setattr(settings_page.sys if hasattr(settings_page, "sys") else __import__("sys"), "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", fake)
    assert settings_page.choose_folder("Scegli") == "/Users/x/Documents"
    assert "choose folder" in seen["args"][2] and seen["args"][-1] == str(Path.home())  # parte dalla home
    monkeypatch.setattr(subprocess, "run", lambda a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="execution error: User canceled. (-128)"))
    assert settings_page.choose_folder("Scegli") is None  # annullata: niente da fare
    monkeypatch.setattr(subprocess, "run", lambda a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="boom"))
    assert settings_page.choose_folder("Scegli") is settings_page._UNAVAILABLE  # ripiego: campo di testo


def test_background_box_shows_progress_and_events(tmp_path, monkeypatch):
    """Box "Indicizzazione automatica": stato del servizio, avanzamento per cartella, ultimi eventi."""
    from folder_intelligence.tui import ChatTUI

    ui = ChatTUI(Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False), JobManager(), [])
    text = lambda: (setattr(ui, "_bg_cache", None), "".join(t for _s, t in ui._render_background()))[1]  # noqa: E731
    assert "Nessuna cartella sorvegliata" in text()
    folder = tmp_path / "Archivio"
    folder.mkdir()
    for i in range(4):
        (folder / f"lettera{i}.txt").write_text(f"lettera numero {i}")
    settings.add_folder(folder)
    assert "spenta" in text() and "da leggere" in text()
    asyncio.run(ui.assistant.handle(f"indicizza {folder}"))  # passo veloce: cercabili, non ancora compresi
    total, understood = ui.assistant.intelligence.index.progress(folder.resolve())
    assert total == 4
    assert f"{understood}/4" in text()
    settings.update(background_enabled=True)
    assert "riparte tra" in text()  # chat appena usata: aspetta che resti ferma
    ui.idle_indexer.state = {"phase": "enrich", "done": 3, "total": 8, "file": "a.pdf"}
    background._event("enriched", 3)
    shown = text()
    assert "al lavoro: comprensione 3/8" in shown and "a.pdf" in shown and "+3 file compresi dall'AI" in shown


def test_open_but_idle_chat_indexes_watched_folders(tmp_path, monkeypatch):
    """Chat aperta ma ferma: legge e comprende le cartelle sorvegliate; si ferma appena scrivi."""
    from folder_intelligence.idle_indexer import IdleIndexer
    from folder_intelligence.intelligence import DocumentIntelligence

    folder = tmp_path / "Archivio"
    folder.mkdir()
    for i in range(3):
        (folder / f"lettera{i}.txt").write_text(f"lettera di dimissioni numero {i}")
    settings.add_folder(folder)
    ai = FakeAI()
    a = Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False,
                  intelligence=DocumentIntelligence(ai, index=Index()))
    busy = {"v": False}
    worker = IdleIndexer(a, busy=lambda: busy["v"], idle_seconds=0)
    monkeypatch.setattr(settings, "system_is_free", lambda *a: True)
    monkeypatch.setattr(background, "on_ac_power", lambda: True)
    assert asyncio.run(worker.step()) == 0 and worker.state["reason"] == "off"  # spenta nelle impostazioni
    settings.update(background_enabled=True)
    busy["v"] = True
    assert asyncio.run(worker.step()) == 0 and worker.state["reason"] == "chat-active"
    busy["v"] = False
    assert asyncio.run(worker.step()) == 3  # letti col passo veloce e compresi dall'AI
    doc = a.intelligence.index.get(folder.resolve() / "lettera0.txt")
    assert doc["title"] == "capito" and a.intelligence.index.progress(folder.resolve()) == (3, 3)
    assert asyncio.run(worker.step()) == 0 and worker.state["phase"] == "done"
    worker.idle_seconds = 60
    (folder / "nuova.txt").write_text("lettera nuova")
    worker._last_scan = 0
    worker.touch()  # hai scritto: niente lavoro finché la chat non resta ferma
    assert asyncio.run(worker.step()) == 0 and a.intelligence.index.get(folder.resolve() / "nuova.txt") is None
    worker.idle_seconds = 0
    assert asyncio.run(worker.step()) == 1  # ferma di nuovo: il file nuovo viene letto e compreso


def test_idle_work_stops_when_you_type(tmp_path, monkeypatch):
    from folder_intelligence.idle_indexer import IdleIndexer
    from folder_intelligence.intelligence import DocumentIntelligence

    class SlowAI(FakeAI):
        async def generate_json(self, *a, **k):
            await asyncio.sleep(0.3)
            return await super().generate_json(*a, **k)

    folder = tmp_path / "Archivio"
    folder.mkdir()
    for i in range(8):
        (folder / f"nota{i}.txt").write_text(f"nota {i}")
    settings.add_folder(folder)
    settings.update(background_enabled=True)
    monkeypatch.setattr(settings, "system_is_free", lambda *a: True)
    monkeypatch.setattr(background, "on_ac_power", lambda: True)
    a = Assistant(tmp_path, memory=create_memory("inmemory"), use_ai=False,
                  intelligence=DocumentIntelligence(SlowAI(), index=Index()))
    worker = IdleIndexer(a, busy=lambda: False, idle_seconds=0)

    async def flow():
        task = asyncio.ensure_future(worker.step())
        await asyncio.sleep(0.5)
        worker.touch()  # un tasto premuto nella chat
        return await asyncio.wait_for(task, 5)

    done = asyncio.run(flow())
    assert done < 8 and len(a.intelligence.pending_enrichment()) == 8 - done
