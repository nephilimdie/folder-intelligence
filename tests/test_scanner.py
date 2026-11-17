from folder_intelligence.scanner import scan_directory


def test_finds_duplicates_and_keeps_original(sample_dir):
    res = scan_directory(sample_dir, duplicates=True)
    names = {f.name for f in res.files}
    assert ".hidden" not in names
    assert len(res.duplicates) == 1
    g = res.duplicates[0]
    assert len(g.files) == 3
    assert g.keep.name == "foto.jpg"  # "foto copia.jpg" non è mai l'originale
    assert "foto copia.jpg" in {f.name for f in g.extras}
    assert g.wasted_bytes == 1600


def test_same_size_different_content_not_duplicates(sample_dir):
    res = scan_directory(sample_dir, duplicates=True)
    dup_names = {f.name for g in res.duplicates for f in g.files}
    assert "same_size_a.bin" not in dup_names


def test_large_files_differing_after_partial_block(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"X" * 200_000 + b"1")
    b.write_bytes(b"X" * 200_000 + b"2")
    assert scan_directory(tmp_path, duplicates=True).duplicates == []


def test_missing_directory_raises(tmp_path):
    import pytest

    with pytest.raises(NotADirectoryError):
        scan_directory(tmp_path / "nope")
