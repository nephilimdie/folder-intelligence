import asyncio
from pathlib import Path

from folder_intelligence.index import Index, query_terms
from folder_intelligence.intelligence import DocumentIntelligence, required_terms

ROOT = Path("/docs")


def make_index():
    ix = Index()
    docs = {
        "busta_paga.pdf": "cedolino della retribuzione del mese con trattenute e ricevuta di pagamento",
        "hotel.pdf": "ricevuta albergo Roma, camera doppia, tassa di soggiorno",
        "icona.png": "il testo non è leggibile a causa della risoluzione",
        "verbale.txt": "verbale assemblea condominio per il rifacimento della facciata",
    }
    for name, text in docs.items():
        ix.upsert({"path": str(ROOT / name), "name": name, "text": text})
    return ix


def names(rows):
    return [Path(r[0]).name for r in rows]


def test_elided_articles_are_stopwords():
    assert query_terms("ricevuta dell'albergo") == ["ricevuta", "albergo"]
    assert query_terms("ricevuta dell albergo") == ["ricevuta", "albergo"]  # scritto senza apostrofo


def test_required_terms():
    assert [required_terms(n) for n in (1, 2, 3, 4, 6)] == [1, 2, 2, 2, 3]


def test_fts_precise_requires_most_words():
    ix = make_index()
    loose = names(ix.fts("ricevuta dell albergo", ROOT, min_match=1))
    assert "busta_paga.pdf" in loose and "icona.png" not in loose  # "dell" non trova più "della"
    assert names(ix.fts("ricevuta dell albergo", ROOT, min_match=2)) == ["hotel.pdf"]
    assert ix.fts("ricevuta gatto", ROOT, min_match=2) == []


class FlatAI:
    """Embedding uguali per tutto: simula documenti "vagamente simili" (cos = 0.45)."""

    model = embed_model = "x"

    async def is_available(self):
        return False

    async def can_embed(self):
        return True

    async def embed(self, texts):
        return [[0.45, (1 - 0.45 ** 2) ** 0.5] for _ in texts]


def test_precise_search_returns_nothing_when_nothing_is_relevant(monkeypatch):
    ix = make_index()
    for p in list(ix.under(ROOT)):
        doc = ix.get(p)
        ix.upsert(doc, embedding=[1.0, 0.0])
    di = DocumentIntelligence(FlatAI(), index=ix)

    async def no_spotlight(*a, **k):
        return []

    monkeypatch.setattr("folder_intelligence.shell.mdfind", no_spotlight)
    assert asyncio.run(di.search("gatto", ROOT)) == []
    loose = asyncio.run(di.search("gatto", ROOT, precise=False))
    assert loose and all("significato" in h.sources for h in loose)  # le domande restano larghe
    hits = asyncio.run(di.search("ricevuta albergo", ROOT))
    assert [Path(h.path).name for h in hits] == ["hotel.pdf"]
