# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations


class AuthError(Exception):
    """Base for failures the API maps to a status code. ``detail`` is safe to show."""

    status = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidCredentialsError(AuthError):
    status = 401

    def __init__(self) -> None:
        super().__init__("invalid username or password")


class RateLimitedError(AuthError):
    status = 429

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"too many failed attempts; retry in {retry_after}s")
        self.retry_after = retry_after


class BackendDisabledError(AuthError):
    status = 404

    def __init__(self, backend: str) -> None:
        super().__init__(f"auth backend {backend!r} is not enabled")


class SetupNotRequiredError(AuthError):
    status = 409

    def __init__(self) -> None:
        super().__init__("setup already completed; an admin account exists")


class RegistrationDisabledError(AuthError):
    status = 403

    def __init__(self) -> None:
        super().__init__("self-registration is disabled (auth.local.allow_registration)")


class WeakPasswordError(AuthError):
    status = 422

    def __init__(self, minimum: int) -> None:
        super().__init__(f"password must be at least {minimum} characters")


class InvalidUsernameError(AuthError):
    status = 422

    def __init__(self) -> None:
        super().__init__(
            "username must be 1-64 chars of [a-z0-9._-], starting with a letter or digit"
        )


class UserConflictError(AuthError):
    status = 409

    def __init__(self, username: str) -> None:
        super().__init__(f"user {username!r} already exists")


class LockoutError(AuthError):
    status = 409


class NoSuchUserError(AuthError):
    status = 404

    def __init__(self, user_id: int) -> None:
        super().__init__(f"no user with id {user_id}")


class OidcFlowError(AuthError):
    status = 400


__all__ = [
    "AuthError",
    "BackendDisabledError",
    "InvalidCredentialsError",
    "InvalidUsernameError",
    "LockoutError",
    "NoSuchUserError",
    "OidcFlowError",
    "RateLimitedError",
    "RegistrationDisabledError",
    "SetupNotRequiredError",
    "UserConflictError",
    "WeakPasswordError",
]
