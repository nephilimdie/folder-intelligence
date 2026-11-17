import time

import pytest
from fastapi.testclient import TestClient

from folder_intelligence.api import create_app
from folder_intelligence.memory import create_memory

TOKEN = "test-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(sample_dir):
    app = create_app(use_ai=False, memory=create_memory("inmemory"),
                     default_dir=sample_dir, token=TOKEN)
    with TestClient(app) as c:
        yield c


def _wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/scans/{job_id}", headers=H).json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("scansione non terminata")


def test_auth_required(client):
    assert client.get("/health").status_code == 200
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"Authorization": "Bearer sbagliato"}).status_code == 401
    assert client.get("/status", headers=H).status_code == 200


def test_scan_job_with_progress_and_summary(client, sample_dir):
    r = client.post("/scans", json={"path": str(sample_dir), "duplicates": True}, headers=H)
    assert r.status_code == 202
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done"
    assert job["progress"]["phase"] in ("scan", "hash", "hash_full")
    assert job["summary"]["duplicate_groups"] == 1
    assert client.post("/scans", json={"path": "/non/esiste"}, headers=H).status_code == 404


def test_browse_files_duplicates_folders(client, sample_dir):
    files = client.get("/files", params={"path": str(sample_dir), "sort": "name", "desc": False},
                       headers=H).json()
    assert files["total"] == 9 and files["items"][0]["name"] == "empty.txt"
    q = client.get("/files", params={"path": str(sample_dir), "q": "fattura"}, headers=H).json()
    assert q["total"] == 1
    d = client.get("/duplicates", params={"path": str(sample_dir)}, headers=H).json()
    assert d["total"] == 1 and len(d["items"][0]["duplicates"]) == 2
    f = client.get("/folders", params={"path": str(sample_dir)}, headers=H).json()
    assert [i["name"] for i in f["items"]] == ["sub"]
    c = client.get("/complete", params={"text": "analizza su", "cwd": str(sample_dir)}, headers=H).json()
    assert c["items"][0]["text"] == "sub/"


def test_plan_apply_audit_undo(client, sample_dir):
    plan = client.post("/plans", json={"kind": "dedupe", "path": str(sample_dir)}, headers=H).json()
    assert plan["count"] == 2
    res = client.post(f"/plans/{plan['id']}/apply", headers=H).json()
    assert res["moved"] == 2 and res["operation_id"]
    assert not (sample_dir / "foto copia.jpg").exists()
    assert client.post(f"/plans/{plan['id']}/apply", headers=H).status_code == 404  # mai due volte

    ops = client.get("/operations", headers=H).json()
    assert ops["items"][0]["status"] == "done" and ops["items"][0]["source"] == "api"
    detail = client.get(f"/operations/{res['operation_id']}", headers=H).json()
    assert len(detail["moves"]) == 2

    u = client.post(f"/operations/{res['operation_id']}/undo", headers=H).json()
    assert u["restored"] == 2
    assert (sample_dir / "foto copia.jpg").exists()
    assert client.post(f"/operations/{res['operation_id']}/undo", headers=H).status_code == 409


def test_chat_session_with_confirmation(client, sample_dir):
    r = client.post("/chat", json={"message": "cancella i duplicati"}, headers=H).json()
    assert r["pending"]["kind"] == "dedupe"
    r2 = client.post("/chat", json={"message": "sì", "session_id": r["session_id"]}, headers=H).json()
    assert "Cestino" in r2["reply"] and r2["pending"] is None
    assert not (sample_dir / "foto copia.jpg").exists()
    client.post("/undo", headers=H)
    assert (sample_dir / "foto copia.jpg").exists()


def test_memory_and_export(client, sample_dir):
    client.post("/memory", json={"text": "riordinare le fatture"}, headers=H)
    assert client.get("/memory", params={"q": "fatture"}, headers=H).json()["items"]
    r = client.get("/export", params={"path": str(sample_dir), "format": "csv"}, headers=H)
    assert r.status_code == 200 and r.text.startswith("path,name")


def test_commands_index_search_describe(client, sample_dir):
    cmds = client.get("/commands", headers=H).json()["items"]
    assert {"search", "index", "ask", "describe"} <= {c["name"] for c in cmds}
    r = client.post("/scans", json={"path": str(sample_dir), "index": True}, headers=H)
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done" and job["index"]["analyzed"] == 9
    res = client.get("/search", params={"q": "riunione", "path": str(sample_dir)}, headers=H).json()
    assert res["items"][0]["path"].endswith("note.txt")
    d = client.get("/describe", params={"path": str(sample_dir / "note.txt")}, headers=H).json()
    assert "riunione" in d["text"]
    assert client.get("/index/stats", params={"path": str(sample_dir)}, headers=H).json()["documents"] == 9


def test_scan_can_be_cancelled(client, sample_dir):
    r = client.post("/scans", json={"path": str(sample_dir), "index": True}, headers=H).json()
    client.delete(f"/scans/{r['id']}", headers=H)
    job = _wait(client, r["id"])
    assert job["status"] in ("cancelled", "done")


# ---------------------------------------------------------------- comandi e job
ARGS = {
    "largest": {"n": 3}, "search": {"query": "riunione"}, "ask": {"query": "di cosa parla la riunione?"},
    "remember": {"text": "nota di prova"}, "recall": {"query": "prova"}, "export": {"format": "json"},
    "describe": {"file": "note.txt"}, "undo": {}, "index": {"limit": 50},
}


def _run(client, name, sample_dir, **body):
    body.setdefault("path", str(sample_dir))
    body.setdefault("args", ARGS.get(name, {}))
    body.setdefault("wait", 10)
    return client.post(f"/commands/{name}", json=body, headers=H)


def test_every_chat_command_has_an_api(client, sample_dir):
    cmds = client.get("/commands", headers=H).json()["items"]
    names = {c["name"] for c in cmds}
    assert {"scan", "summary", "largest", "duplicates", "dedupe", "organize", "classify", "index", "search",
            "ask", "describe", "overview", "history", "undo", "export", "remember", "recall",
            "change_dir"} <= names
    assert not {"confirm", "cancel", "unknown", "exit"} & names
    for c in cmds:
        assert c["endpoint"] == f"/commands/{c['name']}" and "path" in c["params"]
    for name in sorted(names):
        r = _run(client, name, sample_dir)
        assert r.status_code == 200, (name, r.text)
        job = r.json()
        assert job["status"] == "done", (name, job)
        assert job["result"]["text"] and job["result"]["data"]["command"] == name
        # rispondi "no" alle conferme lasciate aperte da questo giro di prova
        if job["needs_confirmation"]:
            client.post(f"/jobs/{job['id']}/confirm", json={"confirm": False}, headers=H)


def test_structured_data(client, sample_dir):
    largest = _run(client, "largest", sample_dir).json()["result"]["data"]
    assert len(largest["items"]) == 3 and {"path", "size_bytes"} <= largest["items"][0].keys()
    dups = _run(client, "duplicates", sample_dir).json()["result"]["data"]
    assert len(dups["groups"]) == 1 and len(dups["groups"][0]["duplicates"]) == 2
    summary = _run(client, "summary", sample_dir).json()["result"]["data"]
    assert summary["files"] == 9 and summary["categories"]


def test_destructive_command_requires_confirmation(client, sample_dir):
    job = _run(client, "dedupe", sample_dir).json()
    conf = job["needs_confirmation"]
    assert conf["kind"] == "dedupe" and conf["destructive"] and conf["default"] == "no"
    assert job["result"]["data"]["plan"]["count"] == 2
    assert (sample_dir / "foto copia.jpg").exists()  # niente è stato toccato
    done = client.post(f"/jobs/{job['id']}/confirm", json={"confirm": True}, headers=H).json()
    assert done["status"] == "done" and done["result"]["data"]["operation_id"]
    assert not (sample_dir / "foto copia.jpg").exists()
    assert client.post(f"/jobs/{job['id']}/confirm", json={"confirm": True}, headers=H).status_code == 409
    undo = _run(client, "undo", sample_dir).json()
    assert undo["result"]["data"]["restored"] and (sample_dir / "foto copia.jpg").exists()


def test_index_question_confirmation_via_api(client, sample_dir):
    job = _run(client, "overview", sample_dir).json()
    assert job["needs_confirmation"]["kind"] == "index" and job["needs_confirmation"]["default"] == "yes"
    done = client.post(f"/jobs/{job['id']}/confirm", json={"confirm": True, "wait": 20}, headers=H).json()
    assert done["status"] == "done" and "file letti" in done["result"]["text"]


def test_background_job_progress_and_cancel(client, sample_dir, monkeypatch):
    import asyncio as aio

    from folder_intelligence.intelligence import DocumentIntelligence

    real = DocumentIntelligence.analyze

    async def slow(self, path, force=False):
        await aio.sleep(0.3)
        return await real(self, path, force)

    monkeypatch.setattr(DocumentIntelligence, "analyze", slow)
    r = _run(client, "index", sample_dir, wait=0.2)
    assert r.status_code == 202 and r.json()["status"] == "running"
    jid = r.json()["id"]
    assert any(j["id"] == jid for j in client.get("/jobs", params={"active": True}, headers=H).json()["items"])
    for _ in range(50):
        p = client.get(f"/jobs/{jid}", headers=H).json()["progress"]
        if p["phase"] == "analyze" and p["total"]:
            break
        time.sleep(0.05)
    assert p["phase"] == "analyze" and p["total"] == 9
    client.delete(f"/jobs/{jid}", headers=H)
    for _ in range(100):
        job = client.get(f"/jobs/{jid}", headers=H).json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "cancelled"


def test_unknown_command_and_events_stream(client, sample_dir):
    assert client.post("/commands/nope", json={}, headers=H).status_code == 404
    assert client.post("/commands/confirm", json={}, headers=H).status_code == 404
    _run(client, "largest", sample_dir)
    with client.stream("GET", "/jobs/events", headers=H) as s:
        for line in s.iter_lines():
            if line.startswith("data: "):
                import json

                payload = json.loads(line[6:])
                assert payload["jobs"] and payload["jobs"][0]["command"] == "largest"
                break
