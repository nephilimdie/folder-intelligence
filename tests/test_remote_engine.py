import asyncio
import socket
import threading
import time

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from folder_intelligence.ai import AIEngine, create_ai  # noqa: E402
from folder_intelligence.api import create_app  # noqa: E402
from folder_intelligence.engines.base import EMBED, TEXT, VISION, EngineStrategy  # noqa: E402
from folder_intelligence.engines.remote import RemoteEngine  # noqa: E402
from folder_intelligence.memory import create_memory  # noqa: E402

TOKEN = "segreto-di-test"


class ServerSide(EngineStrategy):
    """Il motore "vero" che gira nel server (qui finto)."""

    name, label = "fake", "Finto"
    supports_text = supports_vision = supports_embeddings = True

    def __init__(self):
        super().__init__("qwen-test", "vision-test", "embed-test")
        self.released = []

    async def available(self, capability=TEXT):
        return True

    async def generate_json(self, prompt, system="", schema=None, model=None):
        return {"prompt": prompt, "schema_keys": sorted((schema or {}).get("properties", {}))}

    async def chat(self, messages, model=None):
        return "risposta: " + messages[-1]["content"]

    async def describe_image(self, image, prompt, as_json=False):
        return f"{len(image)} byte"

    async def embed(self, texts):
        return [[0.5, 0.5] for _ in texts]

    def release(self, capability):
        self.released.append(capability)


@pytest.fixture
def server(monkeypatch):
    engine = ServerSide()
    app = create_app(AIEngine(engine, engine, engine), True, create_memory("inmemory"), token=TOKEN)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    monkeypatch.setenv("FI_SERVER_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("FI_API_TOKEN", TOKEN)
    monkeypatch.setenv("FI_USE_SERVER", "1")
    yield engine
    srv.should_exit = True


def run(c):
    return asyncio.run(c)


def test_chat_uses_server_models_instead_of_loading_its_own(server):
    assert RemoteEngine.reachable()
    ai = create_ai()  # tutto 'auto' + server acceso → modelli del server
    assert all(ai.strategy(c).name == "remote" for c in (TEXT, VISION, EMBED))
    assert run(ai.is_available()) and run(ai.can_see()) and run(ai.can_embed())
    out = run(ai.generate_json("ciao", schema={"properties": {"command": {}}}))
    assert out == {"prompt": "ciao", "schema_keys": ["command"]}
    assert run(ai.chat([{"role": "user", "content": "x"}])) == "risposta: x"
    assert run(ai.describe_image(b"12345", "descrivi")) == "5 byte"
    assert run(ai.embed(["a", "b"])) == [[0.5, 0.5], [0.5, 0.5]]
    assert ai.model == "qwen-test" and "server locale" in ai.summary()
    run(ai.release(TEXT))
    assert server.released == ["text"]


def test_explicit_engine_or_server_side_never_uses_remote(server):
    assert create_ai(engine="ollama").strategy(TEXT).name == "ollama"
    assert create_ai(allow_remote=False).strategy(TEXT).name != "remote"
    with pytest.raises(ValueError):
        create_ai().forbid_remote()


def test_wrong_token_or_disabled(server, monkeypatch):
    monkeypatch.setenv("FI_API_TOKEN", "sbagliato")
    assert not RemoteEngine.reachable()
    monkeypatch.setenv("FI_API_TOKEN", TOKEN)
    monkeypatch.setenv("FI_USE_SERVER", "0")
    assert not RemoteEngine.reachable()
