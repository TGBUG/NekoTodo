from __future__ import annotations

from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except Argon2Error:
        return False


def create_access_token(account_id: int, auth_version: int, secret: str, algorithm: str = "HS256") -> str:
    payload = {"sub": str(account_id), "av": auth_version}
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, secret: str, algorithm: str = "HS256") -> dict[str, Any] | None:
    try:
        return jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.InvalidTokenError:
        return None
