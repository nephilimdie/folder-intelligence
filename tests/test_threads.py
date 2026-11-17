import asyncio
import threading
import time

import pytest

from folder_intelligence import scanner
from folder_intelligence.assistant import Assistant
from folder_intelligence.memory import create_memory
from folder_intelligence.models import FileInfo
from folder_intelligence.threads import Timeout, call_with_timeout, parallel_map, run_blocking


def test_parallel_map_skips_stuck_items():
    block = threading.Event()

    def work(x):
        if x == "bloccato":
            block.wait(30)  # simula una read() iCloud che non torna
        return x.upper()

    start = time.monotonic()
    out = {item: (res, err) for item, res, err in
           parallel_map(work, ["a", "bloccato", "b"], lambda _: 0.5, workers=2)}
    assert time.monotonic() - start < 3
    assert out["a"] == ("A", "") and out["b"] == ("B", "")
    assert out["bloccato"][0] is None and "timeout" in out["bloccato"][1]
    block.set()


def test_call_with_timeout():
    assert call_with_timeout(lambda: 42, 1) == 42
    with pytest.raises(Timeout):
        call_with_timeout(lambda: time.sleep(5), 0.2)


def test_run_blocking_timeout():
    async def go():
        with pytest.raises(Timeout):
            await run_blocking(time.sleep, 5, timeout=0.2)
        assert await run_blocking(sum, [1, 2]) == 3

    asyncio.run(go())


def test_largest_never_reads_file_contents(sample_dir, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("non doveva leggere i file")

    monkeypatch.setattr(scanner, "hash_file", boom)
    a = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False)
    reply = asyncio.run(a.handle("mostrami i 3 file più grandi"))
    assert "file più grandi" in reply


def test_cloud_only_files_are_not_read(tmp_path, monkeypatch):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "b.bin").write_bytes(b"x" * 100)
    files = [FileInfo.from_path(tmp_path / "a.bin"), FileInfo.from_path(tmp_path / "b.bin")]
    files[1].cloud_only = True
    assert scanner.find_duplicates(files) == []


def test_stuck_file_does_not_block_duplicates(tmp_path, monkeypatch):
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.bin").write_bytes(b"same" * 10)
    real = scanner.hash_file
    gate = threading.Event()

    def slow(path, limit=None):
        if path.name == "c.bin":
            gate.wait(30)
        return real(path, limit)

    monkeypatch.setattr(scanner, "hash_file", slow)
    monkeypatch.setattr(scanner, "_timeout", lambda f, limit: 0.5)
    errors: list[str] = []
    files = [FileInfo.from_path(tmp_path / f"{n}.bin") for n in "abc"]
    groups = scanner.find_duplicates(files, errors=errors)
    gate.set()
    assert len(groups) == 1 and {f.name for f in groups[0].files} == {"a.bin", "b.bin"}
    assert any("timeout" in e for e in errors)
