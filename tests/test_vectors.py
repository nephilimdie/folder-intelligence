"""Vettori: profilo per modello, cambio di modello con ricalcolo, instradamento dei modelli di testo."""

import asyncio

from folder_intelligence.ai import AIEngine
from folder_intelligence.engines.base import EngineStrategy
from folder_intelligence.index import Index
from folder_intelligence.vectors import EmbeddingProfile, EmbeddingStore, Reembedder


def test_profiles_per_model():
    assert EmbeddingProfile.for_model("bge-m3:latest").query("ciao") == "ciao"
    q = EmbeddingProfile.for_model("qwen3-embedding:0.6b")
    assert q.query("bollette").endswith("Query: bollette") and q.family == "qwen3-embedding"
    assert EmbeddingProfile.for_model("sconosciuto").family == "bge-m3"


class FakeEmbedAI:
    embed_model = "qwen3-embedding:0.6b"

    async def can_embed(self):
        return True

    async def embed(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


def test_changing_model_clears_and_recomputes_vectors(tmp_path):
    index = Index(tmp_path / "i.enc")
    for i in range(3):
        index.upsert({"path": f"/d/f{i}.txt", "name": f"f{i}.txt", "text": f"testo {i}", "tags": [],
                      "metadata": {}}, [0.1, 0.2, 0.3])
    store = EmbeddingStore(index)
    assert store.use_model("bge-m3") is False and store.count_missing() == 0  # primo avvio: si registra
    assert store.use_model("bge-m3") is False
    assert store.use_model("qwen3-embedding:0.6b") is True and store.count_missing() == 3
    done = asyncio.run(Reembedder(store, FakeEmbedAI()).run())
    assert done == 3 and store.count_missing() == 0 and index.stats()["with_embedding"] == 3


class Engine(EngineStrategy):
    name = "fake"
    supports_text = True

    def __init__(self, loadable):
        super().__init__("qwen2.5:14b")
        self.loadable = loadable

    def can_run(self, model):
        return model in self.loadable

    async def generate_json(self, prompt, system="", schema=None, model=None):
        return {"by": "llamacpp", "model": model}


def test_models_the_engine_cannot_load_go_to_ollama(monkeypatch):
    sent = []

    class FakeOllama(Engine):
        async def generate_json(self, prompt, system="", schema=None, model=None):
            sent.append(model)
            return {"by": "ollama"}

    monkeypatch.setattr("folder_intelligence.ai.create_strategy", lambda name, **kw: FakeOllama(set()))
    ai = AIEngine(Engine({"qwen2.5:14b", "qwen2.5:7b"}))
    assert asyncio.run(ai.generate_json("x", model="qwen2.5:7b"))["by"] == "llamacpp"
    assert asyncio.run(ai.generate_json("x", model="qwen3.5:9b"))["by"] == "ollama" and sent == ["qwen3.5:9b"]
