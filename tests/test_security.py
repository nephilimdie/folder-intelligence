import asyncio
import json
import socket
import threading
from pathlib import Path

import pytest

from folder_intelligence import crypto, history
from folder_intelligence.index import Index
from folder_intelligence.mcp_memory import McpMemory
from folder_intelligence.memory import JsonFileStrategy
from folder_intelligence.privacy import redact


def test_encrypt_roundtrip_and_purpose_binding():
    blob = crypto.encrypt_bytes(b"segreto", "index")
    assert b"segreto" not in blob
    assert crypto.decrypt_bytes(blob, "index") == b"segreto"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_bytes(blob, "memory")  # scopo diverso → rifiutato
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_bytes(tampered, "index")


def test_index_is_encrypted_on_disk_and_reloads(tmp_path):
    ix = Index()
    ix.upsert({"path": "/x/contratto.pdf", "name": "contratto.pdf", "text": "canone di locazione segreto",
               "title": "Contratto", "tags": ["casa"], "metadata": {}})
    ix.flush(force=True)
    raw = ix.path.read_bytes()
    assert ix.path.name == "index.db.enc" and b"locazione" not in raw and b"SQLite" not in raw
    assert oct(ix.path.stat().st_mode)[-3:] == "600"
    again = Index()
    assert again.get("/x/contratto.pdf")["title"] == "Contratto"
    assert again.fts("locazione")[0][0] == "/x/contratto.pdf"


def test_plaintext_index_is_migrated_and_removed(tmp_path):
    import sqlite3

    from folder_intelligence.audit import app_home
    from folder_intelligence.index import SCHEMA

    legacy = app_home() / "index.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(legacy)
    c.execute("PRAGMA journal_mode=WAL")  # come la versione precedente dell'app
    c.executescript(SCHEMA)
    c.execute("INSERT INTO docs(path, name, text) VALUES ('/a/vecchio.txt', 'vecchio.txt', 'dato in chiaro')")
    c.commit()
    c.close()
    ix = Index()
    assert not legacy.exists() and ix.path.exists()
    assert ix.get("/a/vecchio.txt")["text"] == "dato in chiaro"
    ix.upsert({"path": "/a/nuovo.txt", "name": "nuovo.txt", "text": "scrivibile"})  # prima falliva
    ix.flush(force=True)
    again = Index()  # ricarica dal file cifrato nato da un DB in WAL
    again.upsert({"path": "/a/altro.txt", "name": "altro.txt", "text": "ok"})
    assert again.get("/a/nuovo.txt")["text"] == "scrivibile"


def test_memory_and_history_encrypted(tmp_path):
    from folder_intelligence.audit import app_home

    m = JsonFileStrategy()
    asyncio.run(m.save("note", "il PIN della cassaforte è nel cassetto"))
    assert b"cassaforte" not in m.path.read_bytes()
    assert asyncio.run(JsonFileStrategy().query("cassaforte"))
    legacy = app_home() / "history"
    legacy.write_text("\n# 2026\n+analizza ~/Documents/Privato\n")
    history.append("cerca referto medico")
    assert history.load() == ["analizza ~/Documents/Privato", "cerca referto medico"]
    assert not legacy.exists() and b"referto" not in history.history_path().read_bytes()


def test_redaction():
    t = redact("Mario: mario.rossi@mail.it, tel +39 333 1234567, IBAN IT60X0542811101000000123456, "
               "CF RSSMRA80A01H501U, targa AB123CD, https://banca.it/x?id=9")
    for leaked in ("mario.rossi@mail.it", "1234567", "IT60X054", "RSSMRA80A01H501U", "AB123CD", "banca.it"):
        assert leaked not in t


class FakeMemoryServer:
    def __init__(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        self.received = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.received.append(body)
                self.send_response(202)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": f"mem{len(outer.received)}", "state": "pending"}).encode())

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.httpd = HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


DOCS = [
    {"path": "/Users/x/Documents/Lavoro/lettera_rossi.pdf", "name": "lettera_rossi.pdf", "category": "documenti",
     "title": "Lettera a Mario Rossi", "description": "Comunicazione riservata, IBAN IT60X0542811101000000123456",
     "tags": ["lavoro", "riservato"], "metadata": {"doc_type": "lettera", "date": "2025-02-01"}},
    {"path": "/Users/x/Documents/ricevuta.png", "name": "ricevuta.png", "category": "fatture_ricevute",
     "title": "Ricevuta hotel", "description": "Hotel, 189 euro", "tags": ["hotel"],
     "metadata": {"doc_type": "ricevuta", "date": "2025-03-14"}},
]


def test_mcp_sealed_sends_only_inventory():
    srv = FakeMemoryServer()
    mcp = McpMemory("sealed", url=srv.url)
    res = asyncio.run(mcp.sync_folder(Path("/Users/x/Documents"), DOCS))
    assert res["written"] == 1 and len(srv.received) == 1
    text = srv.received[0]["text"]
    assert "lettera 1" in text and "ricevuta 1" in text and "2025" in text
    for leaked in ("Rossi", "IT60", "riservat", "lettera_rossi", "Lavoro", "hotel", "189"):
        assert leaked not in text
    assert srv.received[0]["namespace"] == "files:folder-intelligence"
    # seconda analisi → sostituisce la precedente (supersedes)
    asyncio.run(McpMemory("sealed", url=srv.url).sync_folder(Path("/Users/x/Documents"), DOCS))
    assert srv.received[1]["supersedes"] == "mem1"


def test_mcp_summary_is_redacted_and_off_sends_nothing():
    srv = FakeMemoryServer()
    asyncio.run(McpMemory("summary", url=srv.url).sync_folder(Path("/Users/x/Documents"), DOCS))
    assert len(srv.received) == 3
    joined = " ".join(r["text"] for r in srv.received)
    assert "IT60X0542811101000000123456" not in joined and "Ricevuta hotel" in joined
    srv2 = FakeMemoryServer()
    asyncio.run(McpMemory("off", url=srv2.url).sync_folder(Path("/x"), DOCS))
    assert srv2.received == []


def test_mcp_unreachable_is_reported_not_fatal():
    res = asyncio.run(McpMemory("sealed", url="http://127.0.0.1:9").sync_folder(Path("/x"), DOCS))
    assert res["written"] == 0 and "non raggiungibile" in res["error"]
