# SPDX-License-Identifier: Apache-2.0
"""Configuration Manager (PLAN.md §4.1): load, validate, watch and write back ``/config``.

* ``load()`` reads every document, validates it, and produces an immutable
  :class:`ConfigSnapshot` stamped with a content hash (the "config version" in the UI
  footer and ``/api/v1/health``).
* ``watch()`` polls file signatures (mtime, size) — inotify does not cross Docker Desktop
  bind mounts — and reloads on change.
* Failure is scoped to the file (invariant 6: a broken provider degrades one tile, never
  the dashboard). An invalid ``Provider``/``Board``/``Report`` file is *quarantined*: left
  out of the snapshot — or, when an earlier valid version of that file was loaded, that
  version keeps serving — and listed in ``snapshot.quarantined``. ``Settings`` and ``RBAC``
  stay all-or-nothing, because running on a half-read auth configuration is worse than
  not running: a fatal issue keeps the last good snapshot on reload (``last_error``) and
  refuses startup.
* ``edit()`` mutates a document in place through ruamel round-trip and writes atomically.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap

from hud.config.errors import ConfigError, ConfigIssue
from hud.config.loader import load_yaml, parse_yaml, unknown_keys, validate
from hud.config.schemas import (
    DEFAULT_RBAC_YAML,
    DEFAULT_SETTINGS_YAML,
    KIND_SCHEMAS,
    Document,
    RbacDocument,
    SettingsDocument,
)
from hud.config.secrets import SECRETS_FILE_NAME, scan_literal_secrets
from hud.config.writer import IndentStyle, atomic_write_text, dump_yaml, has_explicit_start

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.yaml"
RBAC_FILE = "rbac.yaml"

# Kinds whose failure is scoped to their own file, and the directories they live in. A file
# that cannot even be parsed is classified by its directory; one at the top level could be
# settings.yaml or rbac.yaml, so it stays fatal.
QUARANTINABLE_KINDS: dict[str, str] = {
    "providers": "Provider",
    "boards": "Board",
    "reports": "Report",
}
FATAL_KINDS = frozenset({"Settings", "RBAC"})

# Where documents live (PLAN.md §11.2). secrets.yaml is deliberately absent: it is read by
# SecretResolver only and never validated as a document or written back.
DOCUMENT_GLOBS: tuple[str, ...] = (
    "*.yaml",
    "providers/*.yaml",
    "boards/*.yaml",
    "reports/*.yaml",
)

ReloadHook = Callable[["ConfigSnapshot"], Awaitable[None]]

_DEFAULT_RBAC = validate(
    parse_yaml(DEFAULT_RBAC_YAML, Path(RBAC_FILE)), RbacDocument, Path(RBAC_FILE)
)


@dataclass(frozen=True)
class LoadedDocument:
    path: Path
    doc: CommentedMap
    model: Document
    explicit_start: bool
    revision: str = ""  # 12 hex chars of sha256 over this file's bytes; the edit precondition


def document_revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class QuarantinedDocument:
    """One invalid file whose failure is contained to itself."""

    path: Path
    kind: str  # declared kind if readable, else inferred from the directory
    name: str | None  # metadata.name if readable
    issues: tuple[ConfigIssue, ...]
    serving_last_good: bool  # an earlier valid version of this file is still in use

    @property
    def summary(self) -> str:
        return "; ".join(str(i) for i in self.issues)


class ConfigConflictError(Exception):
    """An edit's precondition failed: the file on disk is not the revision the caller saw."""

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        self.path, self.expected, self.actual = path, expected, actual
        super().__init__(f"{path.name} is at revision {actual}, not {expected}; reload and retry")


@dataclass(frozen=True)
class ConfigSnapshot:
    version: str  # 12 hex chars of sha256 over every document's bytes
    loaded_at: datetime
    documents: tuple[LoadedDocument, ...]
    warnings: tuple[ConfigIssue, ...] = field(default=())
    quarantined: tuple[QuarantinedDocument, ...] = field(default=())

    @property
    def settings(self) -> SettingsDocument:
        for d in self.documents:
            if isinstance(d.model, SettingsDocument):
                return d.model
        msg = "snapshot has no Settings document"  # load() guarantees one exists
        raise RuntimeError(msg)

    @property
    def rbac(self) -> RbacDocument:
        """The RBAC document, or the bundled default when ``/config`` has none."""
        for d in self.documents:
            if isinstance(d.model, RbacDocument):
                return d.model
        return _DEFAULT_RBAC


Signature = dict[Path, tuple[int, int]]


class ConfigManager:
    def __init__(self, config_dir: Path, poll_interval: float = 2.0) -> None:
        self.config_dir = config_dir
        self.poll_interval = poll_interval
        self._snapshot: ConfigSnapshot | None = None
        self._signature: Signature = {}
        self._last_error: ConfigError | None = None
        self._last_good: dict[Path, LoadedDocument] = {}
        self._hooks: list[ReloadHook] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ public state

    @property
    def snapshot(self) -> ConfigSnapshot:
        if self._snapshot is None:
            msg = "config not loaded yet"
            raise RuntimeError(msg)
        return self._snapshot

    @property
    def last_error(self) -> ConfigError | None:
        """Set when a reload failed and the previous snapshot is still being served."""
        return self._last_error

    def on_reload(self, hook: ReloadHook) -> None:
        self._hooks.append(hook)

    # ------------------------------------------------------------------ bootstrap / load

    def bootstrap(self) -> bool:
        """Create ``settings.yaml`` and ``rbac.yaml`` if the config dir lacks them (PLAN.md
        §14.2: an empty ``/config`` must boot). Returns True if anything was written."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        written = False
        for name, text, why in (
            (SETTINGS_FILE, DEFAULT_SETTINGS_YAML, "config directory had no settings"),
            (RBAC_FILE, DEFAULT_RBAC_YAML, "config directory had no RBAC document"),
        ):
            target = self.config_dir / name
            if target.exists():
                continue
            atomic_write_text(target, text)
            log.info("wrote %s (%s)", target, why)
            written = True
        return written

    def document_paths(self) -> list[Path]:
        seen: set[Path] = set()
        for pattern in DOCUMENT_GLOBS:
            for p in self.config_dir.glob(pattern):
                if p.is_file() and p.name != SECRETS_FILE_NAME:
                    seen.add(p)
        return sorted(seen)

    def load(self) -> ConfigSnapshot:
        """Load and validate everything. Invalid Provider/Board/Report files are quarantined
        (see the module docstring); raises :class:`ConfigError` only for fatal issues."""
        paths = self.document_paths()
        signature = _signature_of(paths)
        docs: list[LoadedDocument] = []
        issues: list[ConfigIssue] = []
        warnings: list[ConfigIssue] = []
        quarantined: list[QuarantinedDocument] = []
        fresh: dict[Path, LoadedDocument] = {}
        hasher = hashlib.sha256()

        for path in paths:
            text, result = self._load_one(path, hasher)
            if isinstance(result, tuple):
                loaded, warns = result
                docs.append(loaded)
                fresh[path] = loaded
                warnings.extend(warns)
                continue
            kind, name = self._peek(text)
            contained = self._contained_kind(path, kind)
            if contained is None:
                issues.extend(result.issues)
                continue
            last = self._last_good.get(path)
            if last is not None:
                docs.append(last)
            quarantined.append(
                QuarantinedDocument(path, contained, name, result.issues, last is not None)
            )

        issues.extend(self._cross_document_issues(docs, has_issues=bool(issues)))
        if issues:
            raise ConfigError(issues)

        for w in warnings:
            log.warning("%s", w)
        for q in quarantined:
            action = "serving its last valid version" if q.serving_last_good else "not loaded"
            log.error("%s %s is invalid, %s:\n%s", q.kind, q.path.name, action, q.summary)
        snapshot = ConfigSnapshot(
            version=hasher.hexdigest()[:12],
            loaded_at=datetime.now(UTC),
            documents=tuple(docs),
            warnings=tuple(warnings),
            quarantined=tuple(quarantined),
        )
        # Only on success: a fatal load must not forget the last valid copy of any file.
        kept = {q.path: self._last_good[q.path] for q in quarantined if q.serving_last_good}
        self._last_good = {**kept, **fresh}
        self._snapshot = snapshot
        self._signature = signature
        self._last_error = None
        return snapshot

    def _load_one(
        self,
        path: Path,
        hasher: Any,  # noqa: ANN401 — hashlib's _Hash is private
    ) -> tuple[str | None, tuple[LoadedDocument, list[ConfigIssue]] | ConfigError]:
        """Read, hash, parse and validate one file. Returns its text (None if unreadable)
        and either the loaded document with its warnings, or the error."""
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return None, ConfigError.single(path, f"not UTF-8: {exc.reason}")
        # The version reflects what is on disk, broken files included, so fixing one moves
        # it even while an older valid copy is still serving.
        hasher.update(str(path.relative_to(self.config_dir)).encode())
        hasher.update(b"\0")
        hasher.update(text.encode("utf-8"))
        hasher.update(b"\0")
        try:
            doc = parse_yaml(text, path)
            model, warns = self._validate_document(path, doc)
        except ConfigError as exc:
            return text, exc
        loaded = LoadedDocument(path, doc, model, has_explicit_start(text), document_revision(text))
        return text, (loaded, warns)

    def _cross_document_issues(
        self, docs: list[LoadedDocument], *, has_issues: bool
    ) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        settings_docs = [d for d in docs if isinstance(d.model, SettingsDocument)]
        if not settings_docs and not has_issues:
            issues.append(ConfigIssue(self.config_dir / SETTINGS_FILE, "settings.yaml is missing"))
        elif len(settings_docs) > 1:
            issues.extend(
                ConfigIssue(d.path, "more than one Settings document; keep only settings.yaml")
                for d in settings_docs[1:]
            )
        rbac_docs = [d for d in docs if isinstance(d.model, RbacDocument)]
        if len(rbac_docs) > 1:
            issues.extend(
                ConfigIssue(d.path, "more than one RBAC document; keep only rbac.yaml")
                for d in rbac_docs[1:]
            )
        return issues

    def _contained_kind(self, path: Path, kind: str | None) -> str | None:
        """The kind whose failure stays inside this file, or None when it must be fatal."""
        if kind in FATAL_KINDS:
            return None
        rel = path.relative_to(self.config_dir).parts
        folder = QUARANTINABLE_KINDS.get(rel[0]) if len(rel) > 1 else None
        if kind in QUARANTINABLE_KINDS.values():
            return kind
        # Unreadable or unknown kind: trust the directory. A newer image's new kind in its
        # own folder degrades one file here rather than refusing to start (PLAN §11.3a);
        # at the top level it could be settings.yaml or rbac.yaml, so it stays fatal.
        return folder

    @staticmethod
    def _peek(text: str | None) -> tuple[str | None, str | None]:
        """Best-effort kind and metadata.name from a file that failed to load."""
        if text is None:
            return None, None
        try:
            doc = parse_yaml(text, Path("peek.yaml"))
        except ConfigError:
            return None, None
        kind = doc.get("kind")
        meta = doc.get("metadata")
        name = meta.get("name") if isinstance(meta, CommentedMap) else None
        return (
            kind if isinstance(kind, str) else None,
            name if isinstance(name, str) else None,
        )

    @staticmethod
    def _validate_document(path: Path, doc: CommentedMap) -> tuple[Document, list[ConfigIssue]]:
        secret_issues = scan_literal_secrets(doc, path)
        if secret_issues:
            raise ConfigError(secret_issues)
        kind = doc.get("kind")
        if not isinstance(kind, str):
            line, col = doc.lc.key(next(iter(doc))) if len(doc) else (0, 0)
            raise ConfigError.single(path, "missing 'kind'", line + 1, col + 1)
        schema = KIND_SCHEMAS.get(kind)
        if schema is None:
            line, col = doc.lc.key("kind")
            known = ", ".join(sorted(KIND_SCHEMAS))
            raise ConfigError.single(
                path, f"unsupported kind {kind!r} (known: {known})", line + 1, col + 1
            )
        model = validate(doc, schema, path)
        return model, unknown_keys(doc, schema, path)

    # ------------------------------------------------------------------ hot reload

    def changed_on_disk(self) -> bool:
        return _signature_of(self.document_paths()) != self._signature

    async def reload(self) -> bool:
        """Reload if files changed. Returns True when a new snapshot was installed."""
        async with self._lock:
            if not self.changed_on_disk():
                return False
            try:
                snapshot = await asyncio.to_thread(self.load)
            except ConfigError as exc:
                # Keep serving the last good snapshot; remember why (invariant 6).
                self._last_error = exc
                self._signature = _signature_of(self.document_paths())
                log.error("config reload failed; keeping previous version:\n%s", exc)
                return False
        log.info("config reloaded, version %s", snapshot.version)
        for hook in self._hooks:
            try:
                await hook(snapshot)
            except Exception:
                log.exception("reload hook %r failed", hook)
        return True

    async def watch(self) -> None:
        """Poll for changes until cancelled. Runs as a task inside the app lifespan."""
        while True:
            await asyncio.sleep(self.poll_interval)
            with contextlib.suppress(Exception):
                await self.reload()

    # ------------------------------------------------------------------ write-back

    def edit(
        self,
        relative_path: str,
        mutate: Callable[[CommentedMap], None],
        *,
        expected_revision: str | None = None,
    ) -> ConfigSnapshot:
        """Round-trip edit: load fresh from disk, ``mutate(doc)``, validate, write atomically.

        The mutation receives the live ruamel mapping, so comments, anchors and key order
        outside the touched region are preserved byte-for-byte. The write is refused (and
        nothing touched) if the result would fail validation or contain a literal secret.
        With ``expected_revision`` the write is also refused when the file on disk is no
        longer the revision the caller loaded — a hand edit in the meantime wins.
        """
        path = self.config_dir / relative_path
        if path.name == SECRETS_FILE_NAME:
            msg = "secrets.yaml is never written through the config manager"
            raise ValueError(msg)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if expected_revision is not None and document_revision(text) != expected_revision:
            raise ConfigConflictError(path, expected_revision, document_revision(text))
        doc = parse_yaml(text, path) if text.strip() else CommentedMap()
        mutate(doc)
        self._validate_document(path, doc)
        rendered = dump_yaml(
            doc, explicit_start=has_explicit_start(text), indent=IndentStyle.detect(text)
        )
        atomic_write_text(path, rendered)
        return self.load()

    async def edit_async(
        self,
        relative_path: str,
        mutate: Callable[[CommentedMap], None],
        *,
        expected_revision: str | None = None,
    ) -> ConfigSnapshot:
        """:meth:`edit` under the reload lock and off the event loop, then the reload hooks,
        so an API write behaves exactly like a file change the watcher noticed."""
        async with self._lock:
            snapshot = await asyncio.to_thread(
                self.edit, relative_path, mutate, expected_revision=expected_revision
            )
        for hook in self._hooks:
            try:
                await hook(snapshot)
            except Exception:
                log.exception("reload hook %r failed", hook)
        return snapshot

    def iter_documents(self, kind: str) -> Iterator[LoadedDocument]:
        return (d for d in self.snapshot.documents if d.model.kind == kind)


def _signature_of(paths: list[Path]) -> Signature:
    sig: Signature = {}
    for p in paths:
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        sig[p] = (st.st_mtime_ns, st.st_size)
    return sig


__all__ = [
    "ConfigConflictError",
    "ConfigManager",
    "ConfigSnapshot",
    "LoadedDocument",
    "document_revision",
    "load_yaml",
]
