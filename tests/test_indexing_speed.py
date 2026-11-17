import asyncio
from pathlib import Path

from folder_intelligence import background, locks
from folder_intelligence.index import Index
from folder_intelligence.intelligence import DocumentIntelligence, is_content
from folder_intelligence.scanner import scan_directory


def test_non_documents_are_not_read(tmp_path):
    assert not is_content(Path("/x/4A1865C8-3F9E-4968-98DD-F7F7969EF4ED"))
    assert not is_content(Path("/x/scheda.abcdp")) and not is_content(Path("/x/app.lockfile"))
    assert is_content(Path("/x/estratto 03-2024.pdf")) and is_content(Path("/x/README"))
    # i backup della Rubrica sono pacchetti: lo scanner non ci entra
    book = tmp_path / "Contatti.abbu"
    (book / "Sources").mkdir(parents=True)
    (book / "Sources" / "00544534-55BD-45A1-88CE-9202E4FD9C0B").write_text("x")
    (tmp_path / "lettera.txt").write_text("ciao")
    assert [f.name for f in scan_directory(tmp_path).files] == ["lettera.txt"]


class SlowAI:
    """Finto motore: conta i riassunti (la parte lenta) senza farli davvero."""

    model = "fake"
    embed_model = "fake-embed"

    def __init__(self):
        self.summaries = 0

    async def is_available(self):
        return True

    async def can_see(self):
        return False

    async def can_embed(self):
        return True

    async def generate_json(self, prompt, system="", schema=None, model=None):
        self.summaries += 1
        return {"category": "documenti", "title": "Titolo AI", "description": "capito", "tags": ["x"]}

    async def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]


def make_docs(tmp_path, n=5):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    for i in range(n):
        (docs / f"doc{i}.txt").write_text(f"estratto conto numero {i} saldo al 31/03/2024")
    (docs / "rubrica.abcdp").write_bytes(b"bplist")
    return sorted(docs.iterdir())


def test_fast_pass_makes_everything_searchable_without_ai_summaries(tmp_path):
    ai = SlowAI()
    di = DocumentIntelligence(ai, index=Index(tmp_path / "ix.enc"))
    paths = make_docs(tmp_path)
    stats = asyncio.run(di.analyze_many(paths, deep=False))
    assert stats.analyzed == 5 and stats.skipped_noncontent == 1 and ai.summaries == 0
    docs = tmp_path / "docs"
    assert asyncio.run(di.search("estratto conto", docs))  # già cercabile
    assert di.index.get(docs / "doc0.txt")["metadata"]["date"] == "2024-03-31"  # data trovata nel testo
    assert len(di.pending_enrichment(docs)) == 5
    ai.summaries = 0  # la ricerca qui sopra ha usato l'AI per espandere la query
    deep = asyncio.run(di.enrich(docs))
    assert deep.analyzed == 5 and ai.summaries == 5 and di.pending_enrichment(docs) == []
    assert di.index.get(docs / "doc0.txt")["title"] == "Titolo AI"


def test_index_lock_is_exclusive_between_writers():
    first, second = locks.IndexLock(), locks.IndexLock()
    assert first.acquire(blocking=False)
    assert not second.acquire(blocking=False)  # il servizio non entra mentre la chat è aperta
    first.release()
    assert second.acquire(blocking=False)
    second.release()


def test_background_service_completes_files_only_when_allowed(tmp_path, monkeypatch):
    ai = SlowAI()
    di = DocumentIntelligence(ai, index=Index())
    asyncio.run(di.analyze_many(make_docs(tmp_path), deep=False))
    di.index.flush(force=True)

    monkeypatch.setattr(background, "can_work", lambda: (False, "user-active"))
    asyncio.run(background.run_service(once=True, ai=ai))
    assert ai.summaries == 0  # stai usando il Mac: non fa niente

    monkeypatch.setattr(background, "can_work", lambda: (True, ""))
    asyncio.run(background.run_service(once=True, ai=ai))
    assert ai.summaries == 5
    assert Index().needs_enrichment() == []  # salvato su disco per la chat
    assert background.take_report() == 5 and background.take_report() == 0


def test_launch_agent_runs_with_background_priority():
    p = background.plist_content()
    assert p["ProgramArguments"][:2] == ["/usr/sbin/taskpolicy", "-b"]
    assert p["ProgramArguments"][-1] == "--background" and p["ProcessType"] == "Background"
    assert p["LowPriorityIO"] is True and "FI_MASTER_KEY" not in p["EnvironmentVariables"]


def test_idle_time_parsing(monkeypatch):
    import subprocess

    out = '  |   "HIDIdleTime" = 425000000000\n'
    monkeypatch.setattr(background.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))
    assert background.idle_seconds() == 425.0
