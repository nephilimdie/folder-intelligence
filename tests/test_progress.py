import asyncio

from folder_intelligence.assistant import Assistant
from folder_intelligence.cli import ProgressDisplay
from folder_intelligence.memory import create_memory
from folder_intelligence.scanner import scan_directory


def test_scan_reports_phases(sample_dir):
    events = []
    scan_directory(sample_dir, duplicates=True, progress=lambda *a: events.append(a))
    phases = [e[0] for e in events]
    assert phases[0] == "scan" and "hash" in phases
    last_scan = [e for e in events if e[0] == "scan"][-1]
    assert last_scan[1] == last_scan[2] == 9  # file trovati = totale
    last_hash = [e for e in events if e[0] == "hash"][-1]
    assert last_hash[1] == last_hash[2]


def test_assistant_forwards_progress(sample_dir):
    events = []
    a = Assistant(sample_dir, memory=create_memory("inmemory"), use_ai=False,
                  progress=lambda *e: events.append(e))
    asyncio.run(a.handle("trovami i duplicati"))
    assert any(e[0] == "hash" for e in events)


def test_progress_display_without_terminal_does_not_crash(sample_dir):
    with ProgressDisplay() as p:
        scan_directory(sample_dir, duplicates=True, progress=p)
