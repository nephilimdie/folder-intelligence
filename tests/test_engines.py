import asyncio
import json
import os

import pytest

from folder_intelligence import ai as ai_mod
from folder_intelligence.ai import AIEngine, create_ai
from folder_intelligence.engines import ENGINES, EngineStrategy, EngineUnsupported, create_strategy, parse_json
from folder_intelligence.engines.base import EMBED, TEXT, VISION
from folder_intelligence.engines.llamacpp import LlamaCppEngine, resolve_gguf


class Fake(EngineStrategy):
    name = "fake"
    label = "Finto"
    supports_text = supports_embeddings = True

    def __init__(self, *a, ok=True, **kw):
        super().__init__(*a, **kw)
        self.ok = ok
        self.calls = []

    async def available(self, capability=TEXT):
        return self.ok and self.supports(capability)

    async def generate_json(self, prompt, system="", schema=None, model=None):
        self.calls.append(("json", model))
        return {"from": self.name}

    async def embed(self, texts):
        self.calls.append(("embed", len(texts)))
        return [[1.0, 0.0] for _ in texts]


class FakeVision(Fake):
    name = "vision"
    supports_text = supports_embeddings = False
    supports_vision = True

    async def describe_image(self, image, prompt, as_json=False, model=None):
        self.calls.append(("image", prompt))
        return "una ricevuta"


def run(c):
    return asyncio.run(c)


def test_facade_routes_each_capability_to_its_strategy():
    text, vision = Fake("qwen2.5:14b", embed_model="bge-m3"), FakeVision(vision_model="qwen3.5:9b")
    ai = AIEngine(text, vision, text)
    assert run(ai.generate_json("x", model="qwen2.5:7b")) == {"from": "fake"}
    assert run(ai.describe_image(b"img", "descrivi")) == "una ricevuta"
    assert run(ai.embed(["a", "b"])) == [[1.0, 0.0], [1.0, 0.0]]
    assert text.calls == [("json", "qwen2.5:7b"), ("embed", 2)] and vision.calls == [("image", "descrivi")]
    assert run(ai.is_available()) and run(ai.can_see()) and run(ai.can_embed())
    assert (ai.model, ai.vision_model, ai.embed_model) == ("qwen2.5:14b", "qwen3.5:9b", "bge-m3")
    assert "qwen3.5:9b (Finto)" in ai.summary()


def test_missing_capability_is_reported_not_crashing():
    ai = AIEngine(Fake("m", ok=False), None, None)
    assert not run(ai.is_available()) and not run(ai.can_see())
    with pytest.raises(EngineUnsupported):
        run(ai.describe_image(b"x", "p"))
    with pytest.raises(EngineUnsupported):
        run(Fake().describe_image(b"x", "p"))  # default della classe base


def test_registry_and_factory():
    assert {"ollama", "llamacpp", "mlx"} <= set(ENGINES)
    s = create_strategy("ollama", model="m", base_url="http://x:1")
    assert s.base_url == "http://x:1"
    assert create_strategy("mlx", model="m", base_url="ignorato").name == "mlx"
    with pytest.raises(ValueError):
        create_strategy("nessuno")


def test_create_ai_explicit_engines_share_instances():
    ai = create_ai(engine="ollama", vision_engine="auto", embed_engine="auto")
    assert ai.strategy(TEXT) is ai.strategy(VISION) is ai.strategy(EMBED)
    assert ai.strategy(TEXT).name == "ollama"
    mixed = create_ai(engine="llamacpp", vision_engine="ollama")
    assert mixed.strategy(TEXT).name == "llamacpp" and mixed.strategy(VISION).name == "ollama"
    assert mixed.strategy(EMBED) is mixed.strategy(TEXT)  # embedding seguono --engine


def test_auto_prefers_llamacpp_only_when_model_file_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "vuoto"))
    ai = create_ai(engine="auto")
    assert ai.strategy(TEXT).name == "ollama"  # nessun GGUF → Ollama
    fake_ollama(tmp_path / "models", "qwen2.5:14b", b"GGUF" + b"\0" * 32)
    fake_ollama(tmp_path / "models", "bge-m3", b"GGUF" + b"\0" * 32)
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(LlamaCppEngine, "installed", staticmethod(lambda: True))
    ai = create_ai(engine="auto", model="qwen2.5:14b", embed_model="bge-m3")
    assert ai.strategy(TEXT).name == "llamacpp" and ai.strategy(EMBED).name == "llamacpp"
    assert ai.strategy(VISION).name == "ollama"


def fake_ollama(root, name, blob_bytes, extra_layers=0):
    repo, _, tag = name.partition(":")
    digest = "sha256:" + format(abs(hash((name, blob_bytes))), "x").ljust(64, "0")
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    (root / "blobs" / digest.replace(":", "-")).write_bytes(blob_bytes)
    layers = [{"mediaType": "application/vnd.ollama.image.model", "digest": digest}] * (1 + extra_layers)
    manifest = root / "manifests" / "registry.ollama.ai" / "library" / repo / (tag or "latest")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"layers": layers}))


def test_resolve_gguf(monkeypatch, tmp_path):
    root = tmp_path / "models"
    monkeypatch.setenv("OLLAMA_MODELS", str(root))
    fake_ollama(root, "qwen2.5:14b", b"GGUF" + b"\0" * 32)
    fake_ollama(root, "nongguf:1b", b"XXXX")
    fake_ollama(root, "atensori:9b", b"GGUF", extra_layers=3)
    assert resolve_gguf("qwen2.5:14b").read_bytes()[:4] == b"GGUF"
    assert resolve_gguf("nongguf:1b") is None
    assert resolve_gguf("atensori:9b") is None
    assert resolve_gguf("assente") is None
    direct = tmp_path / "mio.gguf"
    direct.write_bytes(b"GGUF")
    assert resolve_gguf(str(direct)) == direct


def test_mlx_is_declared_but_not_available():
    s = create_strategy("mlx", model="x")
    assert not run(s.available(TEXT)) and "non è ancora implementato" in s.why_unavailable()


def test_parse_json():
    assert parse_json('bla {"a": 1} bla') == {"a": 1}
    assert parse_json("[1, 2]") == {} and parse_json("niente") == {}


@pytest.mark.skipif(not os.environ.get("FI_LIVE_LLM"), reason="imposta FI_LIVE_LLM=1 per provare i modelli veri")
def test_live_llamacpp_schema_and_embeddings():
    e = LlamaCppEngine(ai_mod.DEFAULT_OLLAMA_MODEL, embed_model="bge-m3")
    schema = {"type": "object", "properties": {"command": {"type": "string", "enum": ["search", "similar"]}},
              "required": ["command"]}
    out = run(e.generate_json("trova foto simili", system="Scegli il comando. JSON.", schema=schema))
    assert out["command"] in ("search", "similar")
    vec = run(e.embed(["ciao"]))[0]
    assert len(vec) == 1024 and abs(sum(x * x for x in vec) - 1) < 1e-3
    e.close()


def test_indexing_never_keeps_text_and_vision_models_loaded_together(tmp_path, monkeypatch):
    from folder_intelligence.extractors.base import Extraction
    from folder_intelligence.index import Index
    from folder_intelligence.intelligence import DocumentIntelligence

    log = []

    class Text(Fake):
        def release(self, capability):
            log.append(f"release-{capability}")

        async def generate_json(self, prompt, system="", schema=None, model=None):
            log.append("text")
            return {"category": "documenti", "title": "t", "description": "d", "tags": []}

    class Vision(FakeVision):
        async def available(self, capability=TEXT):
            return capability == VISION

        def release(self, capability):
            log.append(f"release-{capability}")

        async def describe_image(self, image, prompt, as_json=False, model=None):
            log.append("vision")
            return "foto"

    monkeypatch.setenv("FI_LOW_MEMORY", "1")
    monkeypatch.setattr("folder_intelligence.intelligence.PHASE_BATCH", 3)
    files = []
    for i in range(5):
        f = tmp_path / f"img{i}.png"
        f.write_bytes(b"x" * (i + 1))
        files.append(f)
    text = Text("t", embed_model="e")
    ai = AIEngine(text, Vision(vision_model="v"), text)
    di = DocumentIntelligence(ai, index=Index(), use_vision=True)

    async def fake_extract(path, vision=None):
        return Extraction(description=await ai.describe_image(b"", "p"), engine="vision")

    monkeypatch.setattr(di, "extract", fake_extract)
    stats = run(di.analyze_many(files))
    assert stats.analyzed == 5 and not stats.errors
    # blocco 1 (3 file) e blocco 2 (2 file): mai "text" tra "vision" e il rilascio del visivo
    assert log == (["release-text"] + ["vision"] * 3 + ["release-vision"] + ["text"] * 3
                   + ["release-text"] + ["vision"] * 2 + ["release-vision"] + ["text"] * 2)

    log.clear()
    monkeypatch.setenv("FI_LOW_MEMORY", "0")  # tanta memoria: nessun cambio di modello
    run(di.analyze_many(files, force=True))
    assert "release-text" not in log and "release-vision" not in log
