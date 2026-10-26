import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

KEY_PREFIX = "k1"


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _keys() -> list[tuple[str, Fernet]]:
    primary = os.environ.get("CRM_SECRET_KEY", "")
    if len(primary) < 32:
        raise RuntimeError("CRM_SECRET_KEY must be set to at least 32 characters")
    keys = [(KEY_PREFIX, _fernet(primary))]
    previous = os.environ.get("CRM_SECRET_KEY_PREVIOUS", "")
    if previous:
        keys.append(("k0", _fernet(previous)))
    return keys


def encrypt(value: str) -> str:
    key_id, fernet = _keys()[0]
    return f"{key_id}:{fernet.encrypt(value.encode()).decode()}"


def decrypt(value: str) -> str:
    key_id, _, token = value.partition(":")
    if key_id not in {"k0", "k1"} or not token:
        raise RuntimeError("Encrypted value uses an unknown key id")
    try:
        return MultiFernet([fernet for _, fernet in _keys()]).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Encrypted value cannot be decrypted with the configured secret keys") from exc


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
