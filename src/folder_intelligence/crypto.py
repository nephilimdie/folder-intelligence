"""Cifratura a riposo dei dati dell'app (AES-256-GCM).

- Chiave master casuale da 256 bit, conservata nel Portachiavi di macOS (via `keyring`).
  Alternative: variabile `FI_MASTER_KEY` (base64, utile per test/server) oppure, solo se
  il portachiavi non è disponibile, file `~/.folder_intelligence/master.key` (0600).
- Ogni blob ha nonce casuale e "associated data" con lo scopo (index, memory, audit,
  history…): un dato cifrato per uno scopo non può essere riusato per un altro.
- Formato testo: `enc:v1:<base64(nonce|ciphertext|tag)>`.

Cosa NON protegge: chi ha accesso alla tua sessione utente sbloccata può leggere il
portachiavi (come per qualsiasi app). Protegge da copie del disco/backup, altri utenti,
sync accidentali e strumenti che leggono i file dell'app.
"""

from __future__ import annotations

from .i18n import _

import base64
import os
import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1:"
MAGIC = b"FIENC1"
SERVICE = "folder-intelligence"
ACCOUNT = "master-key"


class CryptoError(Exception):
    pass


def _home() -> Path:
    from .audit import app_home

    return app_home()


def _from_keyring() -> bytes | None:
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None
    try:
        stored = keyring.get_password(SERVICE, ACCOUNT)
        if stored:
            return base64.b64decode(stored)
        key = AESGCM.generate_key(bit_length=256)
        keyring.set_password(SERVICE, ACCOUNT, base64.b64encode(key).decode())
        # rilettura: verifica che il backend abbia davvero salvato
        if keyring.get_password(SERVICE, ACCOUNT):
            return key
    except (KeyringError, RuntimeError, OSError):
        return None
    return None


def _from_file() -> bytes:
    path = _home() / "master.key"
    if path.exists():
        return base64.b64decode(path.read_text().strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    key = AESGCM.generate_key(bit_length=256)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(key).decode())
    return key


@lru_cache(maxsize=1)
def master_key() -> bytes:
    env = os.environ.get("FI_MASTER_KEY")
    if env:
        key = base64.b64decode(env)
    elif os.environ.get("FI_KEY_BACKEND") == "file":
        key = _from_file()
    else:
        key = _from_keyring() or _from_file()
    if len(key) != 32:
        raise CryptoError(_("Chiave master non valida (servono 32 byte)"))
    return key


def key_location() -> str:
    if os.environ.get("FI_MASTER_KEY"):
        return "variabile FI_MASTER_KEY"
    if (_home() / "master.key").exists():
        return str(_home() / "master.key")
    return _("Portachiavi macOS (servizio '{service}')", service=SERVICE)


def encrypt_bytes(data: bytes, purpose: str) -> bytes:
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(master_key()).encrypt(nonce, data, purpose.encode())


def decrypt_bytes(blob: bytes, purpose: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise CryptoError(_("Formato non cifrato o sconosciuto"))
    nonce, ct = blob[len(MAGIC):len(MAGIC) + 12], blob[len(MAGIC) + 12:]
    try:
        return AESGCM(master_key()).decrypt(nonce, ct, purpose.encode())
    except Exception as e:  # InvalidTag
        raise CryptoError(_("Impossibile decifrare: chiave errata o dati manomessi")) from e


def is_encrypted_bytes(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def encrypt_text(text: str, purpose: str) -> str:
    return PREFIX + base64.b64encode(encrypt_bytes(text.encode("utf-8"), purpose)[len(MAGIC):]).decode()


def decrypt_text(token: str, purpose: str) -> str:
    if not token.startswith(PREFIX):
        return token  # dato legacy in chiaro
    raw = MAGIC + base64.b64decode(token[len(PREFIX):])
    return decrypt_bytes(raw, purpose).decode("utf-8")


def write_encrypted(path: Path, data: bytes, purpose: str) -> None:
    """Scrittura atomica (tmp + rename) con permessi 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(encrypt_bytes(data, purpose))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_encrypted(path: Path, purpose: str) -> bytes:
    return decrypt_bytes(path.read_bytes(), purpose)


def secure_delete(path: Path) -> None:
    """Sovrascrive con byte casuali e cancella (best effort: su SSD/APFS non è garantito)."""
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(secrets.token_bytes(min(size, 64 * 1024 * 1024)))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)
