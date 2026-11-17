import struct
import subprocess
import sys

import pytest

from folder_intelligence import similarity as sim
from folder_intelligence.index import Index
from folder_intelligence.intents import parse
from folder_intelligence.models import FileInfo
from folder_intelligence.scanner import scan_directory


def bmp(width, height, pixels):
    """BMP 24 bit top-down da una lista di righe di valori di grigio."""
    row_size = ((24 * width + 31) // 32) * 4
    body = b""
    for row in pixels:
        r = b"".join(bytes([v, v, v]) for v in row)
        body += r + b"\0" * (row_size - len(r))
    header = b"BM" + struct.pack("<IHHI", 54 + len(body), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 24, 0, len(body), 0, 0, 0, 0)
    return header + info + body


def test_dhash_from_bmp_and_flat_images():
    gradient = [[x * 25 for x in range(9)] for _ in range(8)]
    h = sim.dhash_from_gray(sim.parse_bmp_gray(bmp(9, 8, gradient)))
    assert h == 0  # sempre crescente → nessun bit "sinistra > destra"
    reverse = [[(8 - x) * 25 for x in range(9)] for _ in range(8)]
    assert sim.dhash_from_gray(sim.parse_bmp_gray(bmp(9, 8, reverse))) == (1 << 64) - 1
    assert sim.dhash_from_gray(sim.parse_bmp_gray(bmp(9, 8, [[100] * 9] * 8))) is None


def fi(path, size=1000, mtime=0.0):
    return FileInfo(path=path, size_bytes=size, modified=mtime)


def test_image_groups_and_keep_choice(tmp_path):
    a, b, c, d = (fi(tmp_path / n, s) for n, s in
                  (("foto.jpg", 5000), ("foto (1).jpg", 5000), ("piccola.jpg", 900), ("altro.jpg", 4000)))
    hashes = {str(a.path): 0b1010, str(b.path): 0b1010, str(c.path): 0b1011, str(d.path): (1 << 64) - 1}
    groups = sim._image_groups([a, b, c, d], hashes, sim.NORMAL, None)
    assert len(groups) == 1
    g = groups[0]
    assert g.keep is a  # stessa dimensione di "(1)" ma nome pulito
    assert {f.name for f in g.files} == {"foto.jpg", "foto (1).jpg", "piccola.jpg"}


@pytest.mark.skipif(sys.platform != "darwin", reason="sips solo su macOS")
def test_real_resized_image_is_similar_and_cached(tmp_path, monkeypatch):
    src = tmp_path / "orig.png"
    w, h = 64, 48
    pixels = [[(x * 4 + y * 2) % 256 for x in range(w)] for y in range(h)]
    (tmp_path / "orig.bmp").write_bytes(bmp(w, h, pixels))
    subprocess.run(["sips", "-s", "format", "png", tmp_path / "orig.bmp", "--out", src], capture_output=True, check=True)
    subprocess.run(["sips", "-z", "24", "32", src, "--out", tmp_path / "small.png"], capture_output=True, check=True)
    subprocess.run(["sips", "-s", "format", "jpeg", src, "--out", tmp_path / "conv.jpg"], capture_output=True, check=True)
    (tmp_path / "orig.bmp").unlink()
    ix = Index(tmp_path / "ix.enc")
    scan = scan_directory(tmp_path)
    res = sim.find_similar(scan.files, ix, "images")
    assert res.images_checked == 3 and len(res.groups) == 1 and len(res.groups[0].files) == 3
    calls = []
    monkeypatch.setattr(sim, "image_dhash", lambda *a: calls.append(a))
    again = sim.find_similar(scan.files, ix, "images")
    assert calls == [] and len(again.groups) == 1  # impronte dalla cache cifrata


TEXT = ("Il presente contratto di locazione ad uso abitativo viene stipulato tra le parti per "
        "l'immobile situato in via Roma, con canone mensile concordato e deposito cauzionale pari a "
        "tre mensilità, durata di quattro anni rinnovabile tacitamente salvo disdetta inviata con "
        "raccomandata almeno sei mesi prima della scadenza naturale del contratto stesso. ") * 3


def test_documents_by_text_and_by_name(tmp_path):
    docs = {
        "contratto.pdf": TEXT, "contratto.docx": TEXT,                      # stesso testo, formati diversi
        "relazione.pdf": "tutt'altro argomento " * 80,
        "estratto_1.pdf": "movimenti gennaio conto " * 50,               # numerazione, non versioni
        "estratto_2.pdf": "spese febbraio carta credito " * 50,
    }
    files = [fi(tmp_path / n) for n in docs] + [fi(tmp_path / "preventivo.pdf"), fi(tmp_path / "preventivo v2.pdf")]
    groups = sim._document_groups(files, {str(tmp_path / n): t for n, t in docs.items()}, None)
    names = sorted(sorted(f.name for f in g.files) for g in groups)
    assert names == [["contratto.docx", "contratto.pdf"], ["preventivo v2.pdf", "preventivo.pdf"]]


def test_version_key():
    assert sim.version_key(fi(sim.Path("/x/Contratto_affitto_v2 FINALE.docx")))[0] == "contratto affitto"
    assert sim.version_key(fi(sim.Path("/x/contratto.docx"))) is None
    assert sim.version_key(fi(sim.Path("/x/04-2024_6.pdf"))) is None
    assert sim.version_key(fi(sim.Path("/x/foto v2.jpg"))) is None  # non è un documento


@pytest.mark.parametrize("text,kind", [
    ("trova foto simili", "images"),
    ("trova elementi simili in ~/Documents", "all"),
    ("ci sono documenti quasi uguali?", "documents"),
    ("foto simili nella cartella Documenti", "images"),
])
def test_similar_intent(text, kind):
    i = parse(text)
    assert i.type == "similar" and i.args["kind"] == kind


def test_bundles_are_not_entered(tmp_path):
    (tmp_path / "nota.rtfd").mkdir()
    (tmp_path / "nota.rtfd" / "TXT.rtf").write_text("x")
    (tmp_path / "vero.txt").write_text("y")
    assert [f.name for f in scan_directory(tmp_path).files] == ["vero.txt"]
