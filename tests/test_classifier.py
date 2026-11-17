import asyncio
from pathlib import Path

from folder_intelligence.classifier import FileClassifier, RuleClassifier, _parse_json
from folder_intelligence.models import FileInfo


def test_rules_by_extension_and_name():
    r = RuleClassifier()
    assert r.classify(Path("x.jpg"))["category"] == "immagini"
    assert r.classify(Path("Screenshot 2024-01-01.png"))["category"] == "screenshot"
    assert r.classify(Path("fattura_enel.pdf"))["category"] == "fatture_ricevute"
    assert r.classify(Path("Passaporto.pdf"))["category"] == "carte_certificati"
    assert r.classify(Path("setup.dmg"))["category"] == "installer"
    assert r.classify(Path("README"))["category"] == "altro"


def test_parse_json_tolerates_noise():
    assert _parse_json('bla {"category": "documenti"} bla') == {"category": "documenti"}
    assert _parse_json("nonsense") == {}


class FakeOllama:
    model = "fake"

    def __init__(self, response):
        self.response = response

    async def is_available(self):
        return True

    async def generate_json(self, prompt, system=""):
        return self.response


def test_ai_overrides_rules_for_text(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("Fattura n. 12 del 2024, totale 100 euro")
    info = FileInfo.from_path(p)
    c = FileClassifier(FakeOllama({"category": "fatture_ricevute", "description": "fattura", "confidence": 0.95}))
    asyncio.run(c.classify(info))
    assert info.category == "fatture_ricevute" and info.source == "ollama"


def test_ai_invalid_category_falls_back_to_rules(tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("ciao")
    info = FileInfo.from_path(p)
    c = FileClassifier(FakeOllama({"category": "boh"}))
    asyncio.run(c.classify(info))
    assert info.category == "documenti" and info.source == "rules"
