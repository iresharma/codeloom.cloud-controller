from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from codeloom_cloud.config import Settings


class TokenError(Exception):
    pass


def fernet_for(settings: Settings) -> Fernet:
    raw = settings.token_encryption_key.strip()
    if raw:
        try:
            return Fernet(raw.encode())
        except (ValueError, TypeError) as exc:
            raise TokenError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from exc
    digest = hashlib.sha256(settings.session_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(token: str, settings: Settings) -> str:
    return fernet_for(settings).encrypt(token.encode()).decode()


def decrypt_token(encrypted: str, settings: Settings) -> str:
    try:
        return fernet_for(settings).decrypt(encrypted.encode()).decode()
    except (InvalidToken, TokenError, ValueError) as exc:
        raise TokenError("stored github token could not be decrypted") from exc
