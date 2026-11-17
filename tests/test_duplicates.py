import os
import subprocess
import sys

import pytest

from folder_intelligence import organizer
from folder_intelligence.intents import parse
from folder_intelligence.scanner import scan_directory


@pytest.fixture
def tree(tmp_path):
    big = os.urandom(200_000)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "report.pdf").write_bytes(big)
    (tmp_path / "b" / "report copia.pdf").write_bytes(big)
    (tmp_path / "a" / "quasi.bin").write_bytes(big[:-1] + b"X")  # stessi primi 64KB
    (tmp_path / "a" / "foto.jpg").write_bytes(b"J" * 1000)
    (tmp_path / "b" / "foto 2.jpg").write_bytes(b"J" * 1000)
    os.utime(tmp_path / "b" / "foto 2.jpg", (0, 0))  # la copia è più "vecchia"
    os.link(tmp_path / "a" / "report.pdf", tmp_path / "b" / "hardlink.pdf")
    return tmp_path


def groups_by_name(scan):
    return {g.keep.name: g for g in scan.duplicates}


def test_same_prefix_different_tail_is_not_duplicate(tree):
    scan = scan_directory(tree, duplicates=True)
    assert all("quasi.bin" not in [f.name for f in g.files] for g in scan.duplicates)


def test_finder_style_copy_is_not_kept_even_if_older(tree):
    g = groups_by_name(scan_directory(tree, duplicates=True))["foto.jpg"]
    assert [f.name for f in g.extras] == ["foto 2.jpg"]


def test_hardlink_does_not_count_as_wasted_space(tree):
    g = groups_by_name(scan_directory(tree, duplicates=True))["report.pdf"]
    assert {f.name for f in g.extras} == {"hardlink.pdf", "report copia.pdf"}
    assert g.wasted_bytes == 200_000  # solo la copia vera occupa spazio
    link = next(f for f in g.extras if f.name == "hardlink.pdf")
    assert g.shares_storage(link)


@pytest.mark.skipif(sys.platform != "darwin", reason="cloni APFS solo su macOS")
def test_apfs_clone_does_not_count_as_wasted_space(tmp_path):
    (tmp_path / "x.bin").write_bytes(os.urandom(100_000))
    if subprocess.run(["cp", "-c", tmp_path / "x.bin", tmp_path / "y.bin"]).returncode:
        pytest.skip("file system senza cloni")
    g = scan_directory(tmp_path, duplicates=True).duplicates[0]
    assert g.wasted_bytes == 0


def test_dedupe_rechecks_files_before_moving(tree):
    scan = scan_directory(tree, duplicates=True)
    plan = organizer.plan_dedupe(scan.duplicates, scan.root)
    (tree / "b" / "report copia.pdf").write_bytes(b"modificato dopo la scansione" * 10_000)
    (tree / "a" / "foto.jpg").unlink()  # l'originale sparisce: la copia deve restare
    res = organizer.apply_plan(plan, "test")
    assert (tree / "b" / "report copia.pdf").exists()
    assert (tree / "b" / "foto 2.jpg").exists()
    assert not (tree / "b" / "hardlink.pdf").exists()  # questa era ancora identica
    assert len(res.failed) == 2


@pytest.mark.parametrize("text,expected", [
    ("cancella tutto", "dedupe"),
    ("eliminali", "dedupe"),
    ("cancella i duplicati", "dedupe"),
    ("cancella tutti i file pdf", None),
    ("elimina quelli vecchi", None),
])
def test_delete_phrases_only_mean_dedupe_when_unambiguous(text, expected):
    got = parse(text).type
    assert (got == "dedupe") == (expected == "dedupe"), got

