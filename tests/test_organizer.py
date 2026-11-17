
from folder_intelligence import organizer
from folder_intelligence.classifier import RuleClassifier
from folder_intelligence.scanner import scan_directory


def _classified(root):
    res = scan_directory(root, duplicates=True)
    rc = RuleClassifier()
    for f in res.files:
        f.category = rc.classify(f.path)["category"]
    return res


def test_dedupe_moves_to_trash_and_undo_restores(sample_dir, tmp_path):
    res = _classified(sample_dir)
    plan = organizer.plan_dedupe(res.duplicates, res.root)
    assert len(plan) == 2
    result = organizer.apply_plan(plan)
    assert len(result.done) == 2 and not result.failed
    assert (sample_dir / "foto.jpg").exists()
    assert not (sample_dir / "foto copia.jpg").exists()
    assert len(list((tmp_path / "Trash").iterdir())) == 2

    undone = organizer.undo_last()
    assert len(undone.restored) == 2
    assert (sample_dir / "foto copia.jpg").exists()
    assert (sample_dir / "sub" / "foto.jpg").exists()
    assert organizer.undo_last() is None


def test_organize_handles_name_collisions_and_is_idempotent(sample_dir):
    res = _classified(sample_dir)
    plan = organizer.plan_organize(res.files, res.root)
    dsts = [m.dst for m in plan.moves]
    assert len(dsts) == len(set(dsts))
    organizer.apply_plan(plan)
    assert (sample_dir / "Immagini" / "foto.jpg").exists()
    assert (sample_dir / "Immagini" / "foto (1).jpg").exists()
    assert (sample_dir / "Documenti" / "Fatture e ricevute" / "fattura_2024.pdf").exists()

    again = organizer.plan_organize(_classified(sample_dir).files, sample_dir)
    assert len(again) == 0

    organizer.undo_last()
    assert (sample_dir / "note.txt").exists()
    assert not (sample_dir / "Immagini").exists()  # cartelle vuote rimosse


def test_unique_destination(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert organizer.unique_destination(tmp_path / "a.txt").name == "a (1).txt"
    assert organizer.unique_destination(tmp_path / "b.txt").name == "b.txt"
