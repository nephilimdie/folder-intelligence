import base64
import os
from pathlib import Path

import pytest

# chiave di test: i test non devono mai usare il Portachiavi reale
os.environ.setdefault("FI_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
os.environ["FI_MCP_MODE"] = "off"
os.environ["FI_LOW_MEMORY"] = "0"  # risultato indipendente dalla RAM del Mac che esegue i test
os.environ["FI_LANG"] = "it"       # i test verificano i testi italiani (quelli inglesi in test_i18n)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Journal, memoria e Cestino in cartelle temporanee: i test non toccano mai ~."""
    from folder_intelligence import i18n

    monkeypatch.setenv("FI_HOME", str(tmp_path / "fi_home"))
    monkeypatch.setenv("FI_TRASH", str(tmp_path / "Trash"))
    i18n.set_language("it")
    yield
    i18n.set_language("it")


@pytest.fixture
def sample_dir(tmp_path) -> Path:
    root = tmp_path / "Downloads"
    (root / "sub").mkdir(parents=True)
    (root / "foto.jpg").write_bytes(b"JPEGDATA" * 100)
    (root / "foto copia.jpg").write_bytes(b"JPEGDATA" * 100)
    (root / "sub" / "foto.jpg").write_bytes(b"JPEGDATA" * 100)
    (root / "fattura_2024.pdf").write_bytes(b"%PDF-1.4 fattura")
    (root / "note.txt").write_text("appunti riunione")
    (root / "same_size_a.bin").write_bytes(b"A" * 50)
    (root / "same_size_b.bin").write_bytes(b"B" * 50)
    (root / "empty.txt").write_bytes(b"")
    (root / ".hidden").write_text("x")
    (root / "musica.mp3").write_bytes(b"ID3" + b"0" * 10)
    return root
