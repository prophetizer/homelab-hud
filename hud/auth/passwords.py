# SPDX-License-Identifier: Apache-2.0
"""Argon2id hashing for the ``local`` backend. Parameters are argon2-cffi's defaults, which
track the RFC 9106 recommendations; ``needs_rehash`` lets them move without a migration."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id by default


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    """Constant-time-ish by construction; a missing hash still runs a verification against a
    dummy so a user's existence is not visible in the response time."""
    target = hashed or _DUMMY_HASH
    try:
        return _hasher.verify(target, password) and hashed is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


_DUMMY_HASH = _hasher.hash("not-a-real-password")

__all__ = ["hash_password", "needs_rehash", "verify_password"]
