import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class CheckpointDecryptionError(RuntimeError):
    code = "delivery_checkpoint_decryption_failed"


class CheckpointCipher:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("delivery checkpoint secret must contain at least 32 characters")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CheckpointDecryptionError("delivery checkpoint cannot be decrypted") from exc
