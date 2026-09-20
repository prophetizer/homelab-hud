# SPDX-License-Identifier: Apache-2.0
"""Configuration Manager (PLAN.md §4.1): load, validate, watch and write back ``/config``.

* ``load()`` reads every document, validates it, and produces an immutable
  :class:`ConfigSnapshot` stamped with a content hash (the "config version" in the UI
  footer and ``/api/v1/health``).
* ``watch()`` polls file signatures (mtime, size) — inotify does not cross Docker Desktop
  bind mounts — and reloads on change. A bad edit keeps the last good snapshot and is
  surfaced via ``last_error``; startup with a bad config refuses to start.
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


@dataclass(frozen=True)
class ConfigSnapshot:
    version: str  # 12 hex chars of sha256 over every document's bytes
    loaded_at: datetime
    documents: tuple[LoadedDocument, ...]
    warnings: tuple[ConfigIssue, ...] = field(default=())

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
        """Load and validate everything. Raises :class:`ConfigError` listing every issue."""
        paths = self.document_paths()
        signature = _signature_of(paths)
        docs: list[LoadedDocument] = []
        issues: list[ConfigIssue] = []
        warnings: list[ConfigIssue] = []
        hasher = hashlib.sha256()

        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
                doc = parse_yaml(text, path)
                loaded, warns = self._validate_document(path, doc)
            except ConfigError as exc:
                issues.extend(exc.issues)
                continue
            hasher.update(str(path.relative_to(self.config_dir)).encode())
            hasher.update(b"\0")
            hasher.update(text.encode("utf-8"))
            hasher.update(b"\0")
            docs.append(LoadedDocument(path, doc, loaded, has_explicit_start(text)))
            warnings.extend(warns)

        settings_docs = [d for d in docs if isinstance(d.model, SettingsDocument)]
        if not settings_docs and not issues:
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
        if issues:
            raise ConfigError(issues)

        for w in warnings:
            log.warning("%s", w)
        snapshot = ConfigSnapshot(
            version=hasher.hexdigest()[:12],
            loaded_at=datetime.now(UTC),
            documents=tuple(docs),
            warnings=tuple(warnings),
        )
        self._snapshot = snapshot
        self._signature = signature
        self._last_error = None
        return snapshot

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

    def edit(self, relative_path: str, mutate: Callable[[CommentedMap], None]) -> ConfigSnapshot:
        """Round-trip edit: load fresh from disk, ``mutate(doc)``, validate, write atomically.

        The mutation receives the live ruamel mapping, so comments, anchors and key order
        outside the touched region are preserved byte-for-byte. The write is refused (and
        nothing touched) if the result would fail validation or contain a literal secret.
        """
        path = self.config_dir / relative_path
        if path.name == SECRETS_FILE_NAME:
            msg = "secrets.yaml is never written through the config manager"
            raise ValueError(msg)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        doc = parse_yaml(text, path) if text.strip() else CommentedMap()
        mutate(doc)
        self._validate_document(path, doc)
        rendered = dump_yaml(
            doc, explicit_start=has_explicit_start(text), indent=IndentStyle.detect(text)
        )
        atomic_write_text(path, rendered)
        return self.load()

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


__all__ = ["ConfigManager", "ConfigSnapshot", "LoadedDocument", "load_yaml"]
