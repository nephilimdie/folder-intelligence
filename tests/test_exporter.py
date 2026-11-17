import csv
import io

from folder_intelligence import exporter
from folder_intelligence.scanner import scan_directory


def test_all_formats(sample_dir, tmp_path):
    res = scan_directory(sample_dir, duplicates=True)
    for fmt in exporter.FORMATS:
        p = exporter.export(res, fmt, tmp_path / f"r.{fmt}")
        assert p.exists() and p.stat().st_size > 0
    rows = list(csv.DictReader(io.StringIO(exporter.to_csv(res))))
    assert sum(1 for r in rows if r["duplicate_of"]) == 2
    assert "Spazio recuperabile" in exporter.to_markdown(res)
