import asyncio
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from folder_intelligence.assistant import Assistant
from folder_intelligence.extractors import ExtractContext, load_extractors
from folder_intelligence.index import Index
from folder_intelligence.intelligence import DocumentIntelligence
from folder_intelligence.memory import create_memory


def run(c):
    return asyncio.run(c)


def make_docx(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   f'<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
        z.writestr("docProps/core.xml", "<cp:coreProperties><dc:title>Contratto</dc:title></cp:coreProperties>")


def make_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook><sheets><sheet name="Spese 2025" sheetId="1"/></sheets></workbook>')
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>Bolletta Enel</t></si><si><t>Affitto</t></si></sst>")


@pytest.fixture
def docs(tmp_path) -> Path:
    root = tmp_path / "Documenti"
    root.mkdir()
    (root / "note.txt").write_text("Verbale della riunione di condominio: rifacimento facciata, preventivo 12.000 euro")
    make_docx(root / "contratto.docx", "Contratto di locazione appartamento via Garibaldi, canone mensile 850 euro")
    make_xlsx(root / "spese.xlsx")
    with zipfile.ZipFile(root / "vacanze.zip", "w") as z:
        z.writestr("spiaggia_sardegna.jpg", b"x")
    (root / "strano.bin").write_bytes(bytes(range(256)))
    return root


def engine(**kw) -> DocumentIntelligence:
    return DocumentIntelligence(ai=None, index=Index(), use_vision=False, **kw)


def test_extractors_read_office_text_archives(docs):
    di = engine()
    assert "locazione" in run(di.extract(docs / "contratto.docx")).text
    x = run(di.extract(docs / "spese.xlsx"))
    assert "Bolletta Enel" in x.text and x.metadata["sheets"] == ["Spese 2025"]
    assert "spiaggia_sardegna.jpg" in run(di.extract(docs / "vacanze.zip")).text
    assert run(di.extract(docs / "strano.bin")).engine == "generic"


def test_index_and_search_by_content_without_ai(docs):
    di = engine()
    stats = run(di.analyze_many(sorted(docs.iterdir())))
    assert stats.analyzed == 5 and not stats.errors
    hits = run(di.search("locazione appartamento", docs))
    assert hits and hits[0].path.endswith("contratto.docx") and "testo" in hits[0].sources
    # accenti e maiuscole ignorati
    assert run(di.search("RIUNIONE condominio", docs))[0].path.endswith("note.txt")
    # incrementale: seconda passata non rianalizza
    again = run(di.analyze_many(sorted(docs.iterdir())))
    assert again.analyzed == 0 and again.skipped_fresh == 5
    (docs / "note.txt").write_text("contenuto cambiato: assicurazione auto")
    assert run(di.analyze_many(sorted(docs.iterdir()))).analyzed == 1
    assert run(di.search("assicurazione", docs))[0].path.endswith("note.txt")


def test_chat_index_describe_search_ask(docs):
    a = Assistant(docs, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        q = await a.handle("quanto costa l'affitto?")  # prima serve l'indice → domanda
        assert "Vuoi che li legga ora? (sì/no)" in q and "5 file" in q
        r = await a.handle("sì")  # indicizza e poi risponde alla domanda originale
        assert "Analizzati 5 file" in r and "Fonti" in r
        d = await a.handle("cosa contiene contratto.docx?")
        assert "locazione" in d and "office" in d
        s = await a.handle("cerca locazione")
        assert "contratto.docx" in s
        q = await a.handle("quanto costa l'affitto?")
        assert "Fonti" in q and "spese.xlsx" in q or "contratto.docx" in q

    run(flow())


def test_index_follows_moves_and_undo(docs):
    a = Assistant(docs, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        await a.handle("indicizza la cartella")
        await a.handle("organizza i file")
        await a.handle("sì")
        moved = docs / "Documenti" / "contratto.docx"
        assert moved.exists() and a.intelligence.index.get(moved) is not None
        assert a.intelligence.index.get(docs / "contratto.docx") is None
        await a.handle("annulla l'ultima operazione")
        assert a.intelligence.index.get(docs / "contratto.docx") is not None

    run(flow())


class FakeAI:
    model, vision_model, embed_model = "fake", "fake-vision", "fake-embed"

    async def is_available(self):
        return True

    async def can_see(self):
        return True

    async def can_embed(self):
        return True

    async def generate_json(self, prompt, system=""):
        if "condominio" in prompt:
            return {"category": "documenti", "title": "Verbale condominio", "description": "Verbale assemblea",
                    "tags": ["condominio", "facciata"], "date": "2025-03-01", "amount": "12000 EUR"}
        return {"category": "altro", "title": "x", "description": "x", "tags": []}

    async def embed(self, texts):
        # vettore giocattolo: dimensione 1 = "casa", 2 = "auto"
        return [[1.0 if any(w in t.lower() for w in ("condominio", "casa", "palazzo")) else 0.0,
                 1.0 if "auto" in t.lower() else 0.0, 0.1] for t in texts]

    async def chat(self, messages):
        return "Il preventivo è di 12.000 euro [1]."

    async def describe_image(self, image, prompt, as_json=False):
        return "Una ricevuta d'albergo"


def test_ai_understanding_semantic_search_and_ask(docs):
    di = DocumentIntelligence(ai=FakeAI(), index=Index(), use_vision=True)
    run(di.analyze_many([docs / "note.txt", docs / "contratto.docx"]))
    doc = di.index.get(docs / "note.txt")
    assert doc["title"] == "Verbale condominio" and "facciata" in doc["tags"]
    assert doc["metadata"]["amount"] == "12000 EUR" and doc["engine"].endswith("+ai")
    # "palazzo" non compare nel testo: lo trova solo la ricerca semantica
    hits = run(di.search("palazzo", docs))
    assert hits[0].path.endswith("note.txt") and "significato" in hits[0].sources
    answer, sources = run(di.ask("quanto costa la facciata?", docs))
    assert "12.000" in answer and sources


@pytest.mark.skipif(not (shutil.which("cupsfilter") and shutil.which("pdftoppm") and shutil.which("tesseract")),
                    reason="servono cupsfilter, poppler e tesseract")
def test_ocr_on_image_and_scanned_pdf(tmp_path):
    txt = tmp_path / "r.txt"
    txt.write_text("HOTEL ROMA CENTRO\nRicevuta numero 2231\nTotale EUR 189,00\n")
    pdf = tmp_path / "ricevuta.pdf"
    pdf.write_bytes(subprocess.run(["cupsfilter", str(txt)], capture_output=True).stdout)
    subprocess.run(["pdftoppm", "-r", "100", "-png", "-singlefile", str(pdf), str(tmp_path / "scan")], check=True)
    img = tmp_path / "scan.png"
    di = engine()
    ex = run(di.extract(img))
    assert "HOTEL ROMA" in ex.text.upper() and "ocr" in ex.engine
    # PDF composto solo da un'immagine → OCR automatico
    if shutil.which("sips"):
        scanned = tmp_path / "scansione.pdf"
        subprocess.run(["sips", "-s", "format", "pdf", str(img), "--out", str(scanned)], capture_output=True)
        if scanned.exists():
            ex = run(di.extract(scanned))
            assert "ROMA" in ex.text.upper() and "ocr" in ex.engine


def test_extractors_are_plugins():
    names = {e.name for e in load_extractors()}
    assert {"text", "pdf", "office", "textutil", "image", "media", "archive", "generic",
            "document-intelligence"} <= names


def test_docintel_disabled_without_config(monkeypatch):
    monkeypatch.delenv("FI_DOCINTEL_URL", raising=False)
    from folder_intelligence.extractors.docintel import DocumentIntelligenceExtractor

    assert run(DocumentIntelligenceExtractor().available(ExtractContext())) is False


def test_index_question_can_be_declined_and_overview(docs):
    a = Assistant(docs, memory=create_memory("inmemory"), use_ai=False)

    async def flow():
        q = await a.handle("cosa contiene la cartella, fammi un riassunto")
        assert "(sì/no)" in q
        assert "non leggo i file" in await a.handle("no")
        assert a.intelligence.index.stats(docs)["documents"] == 0
        await a.handle("riassumi il contenuto della cartella")
        r = await a.handle("si")
        assert "Analizzati 5 file" in r and "file letti su" in r and "Categorie" in r

    run(flow())
