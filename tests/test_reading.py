"""OCR (strategie e catena), tipo di immagine, campi dei documenti, tabelle dei PDF."""

import asyncio
from pathlib import Path

from folder_intelligence.documents import DocumentFields, DocumentKind, ImageKind, VisualReader
from folder_intelligence.documents.amount import parse_amount
from folder_intelligence.ocr import OcrChain, OcrEngineKind, OcrResult, OcrStrategy, build_ocr
from folder_intelligence.pdf_text import keep_columns, looks_tabular, read_pdf_text
from folder_intelligence.pdf_text.pdf_text_strategy import PdfTextStrategy


class FakeOcr(OcrStrategy):
    def __init__(self, kind, text, ok=True):
        self.kind, self.text, self.ok, self.calls = kind, text, ok, 0

    async def available(self):
        return self.ok

    async def read(self, image):
        self.calls += 1
        return OcrResult(self.text, self.kind)


def test_chain_uses_first_available_engine_that_finds_text():
    missing = FakeOcr(OcrEngineKind.APPLE, "testo", ok=False)
    empty = FakeOcr(OcrEngineKind.TESSERACT, "  ")
    model = FakeOcr(OcrEngineKind.MODEL, "SCONTRINO ESSELUNGA totale 6,68")
    result = asyncio.run(OcrChain([missing, empty, model]).read(Path("x.png")))
    assert result.engine is OcrEngineKind.MODEL and missing.calls == 0 and empty.calls == 1


def test_build_ocr_orders_engines_by_setting():
    kinds = lambda k: [s.kind for s in build_ocr(k).strategies]  # noqa: E731
    assert kinds(OcrEngineKind.AUTO) == [OcrEngineKind.APPLE, OcrEngineKind.TESSERACT]
    assert kinds(OcrEngineKind.TESSERACT)[0] is OcrEngineKind.TESSERACT
    assert kinds(OcrEngineKind.MODEL)[0] is OcrEngineKind.MODEL
    assert OcrEngineKind.parse("boh") is OcrEngineKind.AUTO


def test_amounts_written_in_any_way():
    assert parse_amount("1.234,56 €") == (1234.56, "EUR")
    assert parse_amount("EUR 12.50") == (12.5, "EUR")
    assert parse_amount("$1,234.00") == (1234.0, "USD")
    assert parse_amount("1.850") == (1850.0, None)
    assert parse_amount(87.4) == (87.4, None)
    assert parse_amount("null") == (None, None)


def test_document_fields_from_dirty_model_output():
    f = DocumentFields.from_dict({"kind": "Bank Statement", "date": "2024-09-30", "amount": "2.413,77 €",
                                  "issuer": "Intesa Sanpaolo", "number": "null"})
    assert f.kind is DocumentKind.BANK_STATEMENT and f.amount == 2413.77 and f.currency == "EUR"
    assert f.number is None and f.search_terms() == ["intesa sanpaolo"]
    assert DocumentFields.from_dict({"date": "14/03/2025"}).date is None  # solo ISO
    merged = DocumentFields(kind=DocumentKind.RECEIPT).merged_with(DocumentFields(amount=6.68, issuer="Esselunga"))
    assert merged.kind is DocumentKind.RECEIPT and merged.amount == 6.68 and merged.issuer == "Esselunga"
    assert DocumentFields.from_dict(merged.to_dict()) == merged


def test_image_kind_from_ocr_text():
    assert ImageKind.from_ocr("SUPERMERCATO ESSELUNGA Via Roma 12 Scontrino 0458 LATTE PANE TOTALE 6,68") \
        is ImageKind.DOCUMENT
    assert ImageKind.from_ocr("STOP") is ImageKind.PHOTO and ImageKind.from_ocr("") is ImageKind.PHOTO


class FakeVisionAI:
    vision_model = "qwen3.5:9b"

    def __init__(self, installed):
        self.installed, self.calls = installed, []

    async def can_see(self):
        return True

    async def has_model(self, model):
        return model in self.installed

    async def describe_image(self, image, prompt, as_json=False, model=None):
        self.calls.append((model, as_json))
        if as_json:
            return {"description": "Scontrino Esselunga", "fields": {"kind": "receipt", "amount": 6.68}}
        return "Un gatto sul divano"


def test_visual_reader_routes_documents_and_photos():
    doc_ocr = FakeOcr(OcrEngineKind.APPLE, "SUPERMERCATO ESSELUNGA Via Roma 12 Scontrino LATTE PANE TOTALE 6,68")
    ai = FakeVisionAI({"qwen3-vl:8b"})
    reading = asyncio.run(VisualReader(doc_ocr, ai, "qwen3-vl:8b").read(Path("s.png")))
    assert reading.kind is ImageKind.DOCUMENT and reading.fields.amount == 6.68
    assert ai.calls == [("qwen3-vl:8b", True)] and "vision:qwen3-vl:8b" in reading.engine
    assert reading.metadata()["fields"]["kind"] == "receipt"
    photo = asyncio.run(VisualReader(FakeOcr(OcrEngineKind.APPLE, ""), ai, "qwen3-vl:8b").read(Path("p.jpg")))
    assert photo.kind is ImageKind.PHOTO and photo.description == "Un gatto sul divano" and ai.calls[-1] == (None, False)
    # modello per documenti non installato: si usa quello visivo generale
    ai2 = FakeVisionAI(set())
    asyncio.run(VisualReader(doc_ocr, ai2, "qwen3-vl:8b").read(Path("s.png")))
    assert ai2.calls == [(None, True)]
    # passo veloce: solo OCR, nessun modello
    ai3 = FakeVisionAI(set())
    fast = asyncio.run(VisualReader(doc_ocr, ai3, "qwen3-vl:8b").read(Path("s.png"), describe=False))
    assert fast.text and not ai3.calls


STATEMENT = """BANCA INTESA SANPAOLO
Data          Descrizione              Dare        Avere
01/09/2024    Bonifico stipendio                   1.850,00
05/09/2024    Affitto                  650,00
12/09/2024    Supermercato             84,20
"""


def test_table_columns_are_kept():
    assert looks_tabular(STATEMENT) and not looks_tabular("una lettera\ncon due righe")
    text = keep_columns(STATEMENT)
    assert "05/09/2024 | Affitto | 650,00" in text and "BANCA INTESA SANPAOLO" in text


class FakePdf(PdfTextStrategy):
    def __init__(self, name, text, ok=True):
        self.name, self.text, self.ok, self.calls = name, text, ok, 0

    async def available(self):
        return self.ok

    async def read(self, path, max_pages):
        self.calls += 1
        return self.text


def test_docling_only_for_tables_in_the_deep_pass():
    fast, docling = FakePdf("pdftotext", STATEMENT), FakePdf("docling", "| Data | Descrizione |\n" * 20)
    assert asyncio.run(read_pdf_text(Path("e.pdf"), False, fast, docling))[1] == "pdftotext" and not docling.calls
    assert asyncio.run(read_pdf_text(Path("e.pdf"), True, fast, docling))[1] == "docling"
    letter = FakePdf("pdftotext", "Gentile cliente,\nla informiamo che…")
    assert asyncio.run(read_pdf_text(Path("l.pdf"), True, letter, docling))[1] == "pdftotext"
    missing = FakePdf("docling", "", ok=False)
    assert asyncio.run(read_pdf_text(Path("e.pdf"), True, fast, missing))[1] == "pdftotext"


def test_generic_issuer_is_not_a_name():
    assert DocumentFields.from_dict({"issuer": "shop"}).issuer is None
    assert DocumentFields.from_dict({"issuer": "Banca"}).issuer is None
    assert DocumentFields.from_dict({"issuer": "Banca Intesa Sanpaolo"}).issuer == "Banca Intesa Sanpaolo"
