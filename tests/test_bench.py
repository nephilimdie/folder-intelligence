"""Banco di prova: file di casi, punteggio, report; filtro per importo nei risultati."""

import json

import pytest

from folder_intelligence import results
from folder_intelligence.bench import BenchCase, BenchRepository, BenchResult, score
from folder_intelligence.bench.bench_report import report


def test_template_and_cases(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.pdf").write_bytes(b"x")
    (tmp_path / "note.txt").write_text("x")
    repo = BenchRepository(tmp_path)
    assert repo.write_template() == 2
    with pytest.raises(FileExistsError):
        repo.write_template()  # non sovrascrive le risposte già scritte
    data = json.loads(repo.path.read_text())
    data["cases"][0].update(kind="receipt", amount=6.68, words=["latte"])
    repo.path.write_text(json.dumps(data))
    cases = repo.cases()
    assert [c.file for c in cases] == ["a.jpg", "sub/b.pdf"] and cases[0].labelled and not cases[1].labelled


def test_score_and_report():
    case = BenchCase("s.png", kind="receipt", date="2025-03-14", amount=6.68, issuer="Esselunga", words=("latte",))
    doc = {"text": "LATTE INTERO 1,29", "metadata": {"fields": {"kind": "receipt", "date": "2025-03-14",
                                                                "amount": "6,68", "issuer": "Esselunga S.p.A."}}}
    checks = score(case, doc)
    assert checks == {"kind": True, "date": True, "amount": True, "issuer": True, "«latte»": True}
    wrong = score(case, {"text": "", "metadata": {"fields": {"amount": 66.8}}})
    assert not any(wrong.values())
    table = report([BenchResult("apple-ocr", "s.png", checks, 3.2), BenchResult("prima", "s.png", wrong, 5.0)],
                   ["glm-ocr: glm-ocr"])
    assert "apple-ocr" in table and "100%" in table and "glm-ocr" in table


def test_amount_filters_by_words():
    hits = [results.Hit(path=f"/d/b{i}.pdf", title=f"b{i}", score=1, amount=a) for i, a in
            enumerate([45.0, 87.4, 120.0, None])]
    rs = results.ResultSet("bollette", "/d", hits)
    assert rs.refine("sopra 100 €") == ["amount"] and [h.amount for h in rs.visible()] == [120.0]
    rs.refine("mostrali tutti")
    rs.refine("tra 50 e 100 euro")
    assert [h.amount for h in rs.visible()] == [87.4] and "50 €–100 €" in rs.filters.describe()
    rs.refine("mostrali tutti")
    rs.refine("sotto 1.000")
    assert len(rs.visible()) == 3  # senza importo noto non passa un filtro per importo
