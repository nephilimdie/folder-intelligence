import asyncio
from datetime import date

from folder_intelligence.assistant import Assistant
from folder_intelligence.dates import parse_due, query_dates
from folder_intelligence.memory import create_memory


def make_docs(tmp_path):
    docs = tmp_path / "Documents"
    (docs / "banca").mkdir(parents=True)
    (docs / "banca" / "01-2024.txt").write_text(
        "Estratto conto corrente. Periodo dal 01/01/2024 al 31/03/2024. Saldo al 31/03/2024: 1.200 euro.")
    (docs / "banca" / "02-2024.txt").write_text(
        "Estratto conto corrente. Periodo dal 01/04/2024 al 30/06/2024. Saldo al 30/06/2024: 900 euro.")
    (docs / "contratto.txt").write_text("Contratto di locazione dell'appartamento, canone mensile 700 euro.")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "setup.dmg").write_bytes(b"x" * 10)
    return docs, downloads


def test_index_a_folder_then_search_without_saying_where(tmp_path):
    """Il caso segnalato: 'indicizza Documents' e poi 'cerca …' partiva da Downloads (non indicizzata)
    e chiedeva di leggere i file invece di cercare in Documents."""
    docs, downloads = make_docs(tmp_path)
    a = Assistant(downloads, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        await a.handle(f"indicizza la cartella {docs}")
        assert a.directory == docs.resolve()  # la cartella indicata diventa quella di lavoro
        out = await a.handle("cerca estratto conto")
        assert "01-2024.txt" in out and "(sì/no)" not in out

    asyncio.run(flow())


def test_search_falls_back_to_other_indexed_folders(tmp_path):
    docs, downloads = make_docs(tmp_path)
    a = Assistant(downloads, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        await a.handle(f"indicizza la cartella {docs}")
        await a.handle(f"vai in {downloads}")
        out = await a.handle("cerca contratto di locazione")
        assert "altre cartelle indicizzate" in out and "contratto.txt" in out and "(sì/no)" not in out
        assert out.data["elsewhere"] is True

    asyncio.run(flow())


def test_search_by_date_keeps_only_documents_of_that_date(tmp_path):
    docs, downloads = make_docs(tmp_path)
    a = Assistant(docs, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        await a.handle("indicizza la cartella")
        out = await a.handle("cerca estratto conto del 31/03/2024")
        assert "01-2024.txt" in out and "02-2024.txt" not in out
        out = await a.handle("cerca estratto conto giugno 2024")
        assert "02-2024.txt" in out and "01-2024.txt" not in out

    asyncio.run(flow())


def test_index_reads_all_files_by_default(tmp_path):
    docs, _downloads = make_docs(tmp_path)
    for i in range(30):
        (docs / f"nota_{i}.txt").write_text(f"appunto numero {i}")
    a = Assistant(docs, memory=create_memory("inmemory"), use_ai=False, index_limit=None)
    out = asyncio.run(a.handle("indicizza la cartella"))
    assert out.data["analyzed"] == 33 and out.data["remaining"] == 0


def test_query_dates_and_due_dates():
    assert [(s.kind, s.start.isoformat()) for s in query_dates("estratto 31/03/2024")] == [("day", "2024-03-31")]
    assert [(s.kind, s.start.isoformat()) for s in query_dates("bank statement March 2024")] == [("month", "2024-03-01")]
    assert query_dates("contratto 2019") == []
    today = date(2026, 11, 17)  # martedì
    assert parse_due("pagare l'affitto domani", today) == (date(2026, 11, 18), "pagare l'affitto")
    assert parse_due("chiamare il notaio venerdì", today) == (date(2026, 11, 20), "chiamare il notaio")
    assert parse_due("call the bank on Nov 20", today) == (date(2026, 11, 20), "call the bank")
    assert parse_due("dentista il 3 gennaio", today) == (date(2027, 1, 3), "dentista")
    assert parse_due("sistemare le foto", today) == (None, "sistemare le foto")
