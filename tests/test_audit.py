import json

from folder_intelligence import audit, organizer
from folder_intelligence.assistant import Assistant
from folder_intelligence.memory import create_memory
from folder_intelligence.scanner import scan_directory


def _dedupe(root, source="test"):
    res = scan_directory(root, duplicates=True)
    return organizer.apply_plan(organizer.plan_dedupe(res.duplicates, res.root), source)


def test_every_operation_is_audited_with_fingerprints(sample_dir):
    r = _dedupe(sample_dir)
    lines = audit.audit_path().read_text().splitlines()
    assert len(lines) == 1
    ev = audit._decode(lines[0])
    assert ev["event"] == "operation" and ev["id"] == r.operation_id and ev["source"] == "test"
    m = ev["moves"][0]
    assert {"src", "dst", "size", "mtime", "sha256"} <= m.keys()
    assert len(m["sha256"]) == 64


def test_undo_specific_operation_not_only_last(sample_dir):
    first = _dedupe(sample_dir)
    (sample_dir / "nuovo.txt").write_text("x")
    res = scan_directory(sample_dir, duplicates=True)
    rc = organizer.plan_organize(
        [f for f in res.files if f.name == "nuovo.txt"], res.root)
    for mv in rc.moves:
        mv.reason = "documenti"
    second = organizer.apply_plan(rc, "test")

    # annullo la PRIMA operazione lasciando intatta la seconda
    u = organizer.undo_operation(first.operation_id)
    assert len(u.restored) == 2
    assert (sample_dir / "foto copia.jpg").exists()
    ops = {o.id: o for o in audit.operations()}
    assert ops[first.operation_id].status == "annullata"
    assert ops[second.operation_id].status == "eseguita"
    # undo_last ora prende la seconda (l'unica ancora annullabile)
    assert organizer.undo_last().operation_id == second.operation_id
    assert organizer.undo_last() is None


def test_audit_is_append_only_and_records_undo(sample_dir):
    r = _dedupe(sample_dir)
    before = audit.audit_path().read_text()
    organizer.undo_last()
    after = audit.audit_path().read_text()
    assert after.startswith(before)  # mai riscritto
    last = audit._decode(after.splitlines()[-1])
    assert last["event"] == "undo" and last["target"] == r.operation_id


def test_partial_undo_then_complete(sample_dir, tmp_path):
    r = _dedupe(sample_dir)
    op = audit.find_operation(r.operation_id)
    # simulo un file sparito dal Cestino
    gone = op.moves[0]["dst"]
    hidden = tmp_path / "parked"
    import shutil
    shutil.move(gone, hidden)
    u = organizer.undo_operation(1)
    assert len(u.restored) == 1 and len(u.failed) == 1
    assert audit.find_operation(1).status == "annullata in parte"
    shutil.move(hidden, gone)
    u2 = organizer.undo_operation(1)
    assert len(u2.restored) == 1
    assert audit.find_operation(1).status == "annullata"


def test_corrupted_line_is_skipped(sample_dir):
    _dedupe(sample_dir)
    with open(audit.audit_path(), "a") as f:
        f.write('{"event": "operat')  # scrittura interrotta
    assert len(audit.operations()) == 1


def test_chat_history_and_undo_by_index(sample_dir):
    import asyncio

    a = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        await a.handle("cancella i duplicati")
        assert "operazione" in await a.handle("si")
        h = await a.handle("mostra lo storico")
        assert "[1]" in h and "rimuovi duplicati" in h and "eseguita" in h
        assert "ripristinati 2" in await a.handle("annulla operazione 1")
        assert "annullata" in await a.handle("mostra lo storico")
        assert "già stata annullata" in await a.handle("annulla operazione 1")
        assert "Nessuna operazione" in await a.handle("annulla operazione 9")

    asyncio.run(flow())


def test_audit_lines_are_encrypted_and_tamper_evident(sample_dir):
    _dedupe(sample_dir)
    raw = audit.audit_path().read_text()
    assert raw.startswith("enc:v1:") and "foto" not in raw and "Downloads" not in raw
    assert oct(audit.audit_path().stat().st_mode)[-3:] == "600"
    # una riga manomessa viene scartata (GCM), le altre restano leggibili
    line = raw.splitlines()[0]
    tampered = line[:-6] + ("A" if line[-6] != "A" else "B") + line[-5:]
    audit.audit_path().write_text(tampered + "\n")
    assert audit.operations() == []


def test_legacy_plaintext_audit_is_migrated(sample_dir):
    audit.audit_path().parent.mkdir(parents=True, exist_ok=True)
    audit.audit_path().write_text(json.dumps({"event": "operation", "id": "old1", "ts": "2026-01-01T00:00:00",
                                              "kind": "dedupe", "root": "/x", "moves": [{"src": "/x/a", "dst": "/t/a"}],
                                              "failed": []}) + "\n")
    assert audit.migrate_plaintext() == 1
    assert audit.audit_path().read_text().startswith("enc:v1:")
    assert audit.find_operation("old1") is not None
