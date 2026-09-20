# SPDX-License-Identifier: Apache-2.0
"""Users, sessions and audit rows in ``dashboard.db`` (PLAN.md §6.1, §10).

Synchronous SQLAlchemy Core; callers run these through ``asyncio.to_thread`` like the store
writer does. Every method opens its own short transaction.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, delete, func, insert, select, update

from hud.auth.principal import Source
from hud.store.tables import audit_log, sessions, users

# A session's last_seen is bumped at most this often, so reads stay reads.
TOUCH_INTERVAL = 60


@dataclass(frozen=True)
class User:
    id: int
    subject: str
    display_name: str
    source: Source
    groups: frozenset[str]
    has_password: bool
    created_at: int
    last_seen: int | None


@dataclass(frozen=True)
class Session:
    token_hash: str
    user_id: int
    csrf_token: str
    created_at: int
    expires_at: int
    last_seen: int


class UserExistsError(ValueError):
    pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _row_user(row: Any) -> User:  # noqa: ANN401 — SQLAlchemy Row
    return User(
        id=row.id,
        subject=row.subject,
        display_name=row.display_name or row.subject,
        source=row.source,
        groups=frozenset(json.loads(row.groups_json or "[]")),
        has_password=row.password_hash is not None,
        created_at=row.created_at,
        last_seen=row.last_seen,
    )


class AuthStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------ users

    def count_users(self, source: Source | None = None) -> int:
        q = select(func.count()).select_from(users)
        if source is not None:
            q = q.where(users.c.source == source)
        with self._engine.connect() as conn:
            return int(conn.execute(q).scalar_one())

    def get_user(self, user_id: int) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.id == user_id)).first()
        return _row_user(row) if row else None

    def get_user_by_subject(self, subject: str, source: Source) -> User | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(users).where(users.c.subject == subject, users.c.source == source)
            ).first()
        return _row_user(row) if row else None

    def password_hash_for(self, subject: str) -> tuple[User | None, str | None]:
        """Local user and hash in one read, for login."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(users).where(users.c.subject == subject, users.c.source == "local")
            ).first()
        if row is None:
            return None, None
        return _row_user(row), row.password_hash

    def list_users(self) -> list[User]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(users).order_by(users.c.source, users.c.subject)).all()
        return [_row_user(r) for r in rows]

    def create_user(
        self,
        subject: str,
        *,
        source: Source,
        display_name: str | None = None,
        password_hash: str | None = None,
        groups: set[str] | frozenset[str] = frozenset(),
    ) -> User:
        now = int(time.time())
        with self._engine.begin() as conn:
            exists = conn.execute(select(users.c.id).where(users.c.subject == subject)).first()
            if exists:
                msg = f"user {subject!r} already exists"
                raise UserExistsError(msg)
            result = conn.execute(
                insert(users).values(
                    subject=subject,
                    display_name=display_name,
                    source=source,
                    password_hash=password_hash,
                    groups_json=json.dumps(sorted(groups)),
                    created_at=now,
                    last_seen=now,
                )
            )
            pk = result.inserted_primary_key
            assert pk is not None
            user_id = int(pk[0])
        user = self.get_user(user_id)
        assert user is not None
        return user

    def upsert_external(
        self, subject: str, *, source: Source, display_name: str | None, groups: set[str]
    ) -> User:
        """Forward/OIDC identities are mirrored so prefs and audit rows have a user id. Groups
        are overwritten on every sight: the IdP is the source of truth for them."""
        now = int(time.time())
        with self._engine.begin() as conn:
            row = conn.execute(
                select(users.c.id).where(users.c.subject == subject, users.c.source == source)
            ).first()
            if row is None:
                conn.execute(
                    insert(users).values(
                        subject=subject,
                        display_name=display_name,
                        source=source,
                        password_hash=None,
                        groups_json=json.dumps(sorted(groups)),
                        created_at=now,
                        last_seen=now,
                    )
                )
            else:
                conn.execute(
                    update(users)
                    .where(users.c.id == row.id)
                    .values(
                        display_name=display_name,
                        groups_json=json.dumps(sorted(groups)),
                        last_seen=now,
                    )
                )
        user = self.get_user_by_subject(subject, source)
        assert user is not None
        return user

    def set_password(self, user_id: int, password_hash: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(users).where(users.c.id == user_id).values(password_hash=password_hash)
            )
            # A password change ends every other session for that user.
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))

    def set_groups(self, user_id: int, groups: set[str]) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(users)
                .where(users.c.id == user_id)
                .values(groups_json=json.dumps(sorted(groups)))
            )

    def delete_user(self, user_id: int) -> bool:
        with self._engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
            result = conn.execute(delete(users).where(users.c.id == user_id))
        return result.rowcount > 0

    # ------------------------------------------------------------------ sessions

    def create_session(self, user_id: int, lifetime_seconds: int) -> tuple[str, Session]:
        """Returns the cookie value (never stored) and the row."""
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        row = Session(
            token_hash=_hash_token(token),
            user_id=user_id,
            csrf_token=secrets.token_urlsafe(24),
            created_at=now,
            expires_at=now + lifetime_seconds,
            last_seen=now,
        )
        with self._engine.begin() as conn:
            conn.execute(insert(sessions).values(**row.__dict__))
            conn.execute(update(users).where(users.c.id == user_id).values(last_seen=now))
        return token, row

    def lookup_session(self, token: str) -> tuple[Session, User] | None:
        now = int(time.time())
        token_hash = _hash_token(token)
        with self._engine.begin() as conn:
            row = conn.execute(select(sessions).where(sessions.c.token_hash == token_hash)).first()
            if row is None:
                return None
            if row.expires_at <= now:
                conn.execute(delete(sessions).where(sessions.c.token_hash == token_hash))
                return None
            user_row = conn.execute(select(users).where(users.c.id == row.user_id)).first()
            if user_row is None:
                return None
            if now - row.last_seen >= TOUCH_INTERVAL:
                conn.execute(
                    update(sessions)
                    .where(sessions.c.token_hash == token_hash)
                    .values(last_seen=now)
                )
                conn.execute(update(users).where(users.c.id == row.user_id).values(last_seen=now))
        session = Session(
            token_hash=row.token_hash,
            user_id=row.user_id,
            csrf_token=row.csrf_token,
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_seen=row.last_seen,
        )
        return session, _row_user(user_row)

    def delete_session(self, token: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.token_hash == _hash_token(token)))

    def purge_expired_sessions(self) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(delete(sessions).where(sessions.c.expires_at <= int(time.time())))
        return result.rowcount

    # ------------------------------------------------------------------ audit

    def audit(
        self,
        subject: str | None,
        action: str,
        target: str | None,
        result: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(audit_log).values(
                    ts=int(time.time()),
                    subject=subject,
                    action=action,
                    target=target,
                    result=result,
                    detail_json=json.dumps(detail) if detail else None,
                )
            )


__all__ = ["AuthStore", "Session", "User", "UserExistsError"]
