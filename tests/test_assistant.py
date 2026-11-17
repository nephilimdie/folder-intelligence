import asyncio
import json

from folder_intelligence.assistant import Assistant
from folder_intelligence.memory import JsonFileStrategy, create_memory


def run(coro):
    return asyncio.run(coro)


def make(sample_dir):
    return Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False)


def test_full_conversation(sample_dir, tmp_path):
    a = make(sample_dir)

    async def flow():
        assert "9 file" in await a.handle("analizza la cartella")
        dup = await a.handle("trovami i duplicati")
        assert "foto copia.jpg" in dup
        plan = await a.handle("cancella i duplicati")
        assert "Confermi" in plan and a.pending is not None
        assert "annullata" in await a.handle("no")
        assert (sample_dir / "foto copia.jpg").exists()

        await a.handle("cancella i duplicati")
        done = await a.handle("sì")
        assert "2 file spostati nel Cestino" in done
        assert not (sample_dir / "foto copia.jpg").exists()

        assert "ripristinati 2 file" in await a.handle("annulla l'ultima operazione")
        assert (sample_dir / "foto copia.jpg").exists()

        assert "fattura_2024.pdf" in await a.handle("cerca fattura")
        assert "musica.mp3" in await a.handle("cerca mp3")
        assert "file più grandi" in await a.handle("mostrami i 3 file più grandi")

        out = tmp_path / "r.json"
        a.directory = sample_dir
        msg = await a.handle(f"esporta il report in json {out}")
        assert str(out) in msg
        data = json.loads(out.read_text())
        assert data["summary"]["duplicate_groups"] == 1

        await a.handle("riepilogo")  # un nuovo comando fa decadere eventuali domande sì/no
        assert "Non ho capito" in await a.handle("blablabla")

    run(flow())


def test_organize_flow(sample_dir):
    a = make(sample_dir)

    async def flow():
        plan = await a.handle("organizza i file")
        assert "copie duplicate escluse" in plan
        await a.handle("ok")
        assert (sample_dir / "Immagini" / "foto.jpg").exists()
        assert (sample_dir / "foto copia.jpg").exists()  # duplicato non toccato

    run(flow())


def test_memory_persistence(tmp_path):
    async def flow():
        m = JsonFileStrategy(tmp_path / "mem.json")
        await m.save("note", "sistemare le foto delle vacanze")
        m2 = JsonFileStrategy(tmp_path / "mem.json")
        hits = await m2.query("foto vacanze")
        assert hits and "vacanze" in hits[0]["text"]

    run(flow())


def test_remember_and_recall(sample_dir):
    a = make(sample_dir)

    async def flow():
        await a.handle("ricordami di sistemare le foto")
        assert "sistemare le foto" in await a.handle("cosa ricordi?")

    run(flow())
