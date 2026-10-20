"""Encrypted vault; master key and ciphertext must be mounted on separate volumes."""

from __future__ import annotations

import json
import os
import uuid

from cryptography.fernet import Fernet, InvalidToken

from core.errors import AppError, ErrorCode
from core.settings import get_settings


class SecretStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.directory = settings.vault_data_dir
        self.keyfile = settings.vault_key_file

    def _cipher(self) -> Fernet:
        self.keyfile.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.keyfile.exists():
            temporary = self.keyfile.with_name("key-" + uuid.uuid4().hex)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self.keyfile)
            except FileExistsError:
                pass
            finally:
                temporary.unlink()
        return Fernet(self.keyfile.read_bytes())

    def put(self, scope: str, value: str) -> str:
        cipher = self._cipher()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        ref = str(uuid.uuid4())
        fd = os.open(self.directory / ref, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(cipher.encrypt(json.dumps({"scope": scope, "value": value}).encode()))
        return ref

    def get(self, scope: str, ref: str) -> str:
        try:
            ref = str(uuid.UUID(ref))
            data = json.loads(self._cipher().decrypt((self.directory / ref).read_bytes()))
            if data["scope"] != scope:
                raise ValueError("scope mismatch")
            return str(data["value"])
        except (ValueError, KeyError, OSError, InvalidToken) as exc:
            raise AppError(ErrorCode.INTERNAL, "비밀 저장소에서 값을 읽을 수 없습니다.") from exc

    def delete(self, ref: str) -> None:
        (self.directory / str(uuid.UUID(ref))).unlink(missing_ok=True)
