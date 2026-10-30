import hashlib
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("パスワードは12文字以上にしてください")
    return password_hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return password_hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def digest(value: str) -> str:
    # Keys and sessions have 256 bits of random entropy. Never store their plaintext.
    return hashlib.sha256(value.encode()).hexdigest()


def new_key() -> str:
    return "llm_router_" + secrets.token_urlsafe(32)


class SecretBox:
    def __init__(self, data_dir: Path, explicit_key: str | None = None):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data_dir, 0o700)
        key_path = data_dir / "encryption.key"
        if explicit_key:
            key = explicit_key.encode()
        elif key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            # Losing the key for an existing database must not silently replace it.
            if (data_dir / "router.sqlite3").exists():
                raise RuntimeError("暗号鍵がありません。バックアップからencryption.keyを復元してください")
            key = Fernet.generate_key()
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        self.fernet = Fernet(key)

    def encrypt(self, value: str | None) -> str | None:
        return self.fernet.encrypt(value.encode()).decode() if value else None

    def decrypt(self, value: str | None) -> str | None:
        return self.fernet.decrypt(value.encode()).decode() if value else None


class LoginLimiter:
    def __init__(self):
        self.attempts: dict[str, deque] = defaultdict(deque)

    def allowed(self, address: str) -> bool:
        now = time.monotonic()
        events = self.attempts[address]
        while events and events[0] < now - 60:
            events.popleft()
        if len(events) >= 5:
            return False
        events.append(now)
        # Avoid retaining addresses forever on a long-lived gateway.
        if len(self.attempts) > 1000:
            self.attempts = defaultdict(
                deque, {k: v for k, v in self.attempts.items() if v and v[-1] > now - 60}
            )
        return True
