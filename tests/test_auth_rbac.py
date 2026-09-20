# SPDX-License-Identifier: Apache-2.0
"""Permission matching, group resolution and the board visibility rule (PLAN.md §10.2)."""

from pathlib import Path

from hud.auth import Authorizer, Principal, permission_matches
from hud.auth.limiter import LoginLimiter
from hud.auth.passwords import hash_password, verify_password
from hud.config.loader import parse_yaml, validate
from hud.config.schemas import BoardDocument, RbacDocument
from hud.config.schemas.rbac import DEFAULT_RBAC_YAML


def _principal(groups: set[str], authz: Authorizer) -> Principal:
    return Principal(
        subject="u",
        display_name="u",
        groups=frozenset(groups),
        source="local",
        permissions=authz.permissions_for(groups),
        csrf_token="x",
    )


def _board(name: str, visible_to: list[str] | None = None) -> BoardDocument:
    vis = f"  visible_to: {visible_to}\n" if visible_to is not None else ""
    text = f"apiVersion: hud/v1\nkind: Board\nmetadata:\n  name: {name}\n{vis}spec: {{}}\n"
    doc = validate(parse_yaml(text, Path("b.yaml")), BoardDocument, Path("b.yaml"))
    assert isinstance(doc, BoardDocument)
    return doc


def test_permission_matching() -> None:
    assert permission_matches("*", "anything:at:all")
    assert permission_matches("boards:view:*", "boards:view:media")
    assert permission_matches("boards:view:*", "boards:view")
    assert not permission_matches("boards:view:*", "boards:edit:media")
    assert permission_matches("boards:view:media", "boards:view:media")
    assert not permission_matches("boards:view:media", "boards:view:media2")
    assert not permission_matches("boards", "boards:view")


def test_default_rbac_resolution() -> None:
    doc = validate(parse_yaml(DEFAULT_RBAC_YAML, Path("r.yaml")), RbacDocument, Path("r.yaml"))
    assert isinstance(doc, RbacDocument)
    authz = Authorizer(doc.spec)
    assert authz.permissions_for({"admins"}) == {"*"}
    assert authz.permissions_for({"household"}) == {"boards:view:*"}
    # Unlisted groups fall through to the unmatched group, which grants nothing.
    assert authz.permissions_for({"from-idp"}) == frozenset()
    assert authz.permissions_for(set()) == frozenset()
    # A user in one listed and one unlisted group gets only the listed group's grants.
    assert authz.permissions_for({"household", "from-idp"}) == {"boards:view:*"}


def test_board_visibility_is_permission_or_visible_to() -> None:
    doc = validate(parse_yaml(DEFAULT_RBAC_YAML, Path("r.yaml")), RbacDocument, Path("r.yaml"))
    assert isinstance(doc, RbacDocument)
    authz = Authorizer(doc.spec)
    admin = _principal({"admins"}, authz)
    household = _principal({"household"}, authz)
    guest = _principal({"guests"}, authz)
    kids = _principal({"kids"}, authz)  # not in rbac.yaml at all

    media = _board("media")
    family = _board("family", visible_to=["kids"])
    assert authz.can_view_board(admin, media) and authz.can_view_board(admin, family)
    assert authz.can_view_board(household, media) and authz.can_view_board(household, family)
    assert not authz.can_view_board(guest, media)
    assert not authz.can_view_board(guest, family)
    # visible_to grants access to a group that holds no permission at all.
    assert not authz.can_view_board(kids, media)
    assert authz.can_view_board(kids, family)


def test_unmatched_group_must_not_be_required_in_groups() -> None:
    text = "apiVersion: hud/v1\nkind: RBAC\nspec:\n  groups: {}\n"
    doc = validate(parse_yaml(text, Path("r.yaml")), RbacDocument, Path("r.yaml"))
    assert isinstance(doc, RbacDocument)
    assert Authorizer(doc.spec).permissions_for({"anyone"}) == frozenset()


def test_password_hashing_roundtrip_and_unknown_user_path() -> None:
    h = hash_password("hunter2hunter2")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "hunter2hunter2")
    assert not verify_password(h, "hunter2hunter3")
    assert not verify_password(None, "anything")  # runs a dummy verify, always False


def test_login_limiter_windows() -> None:
    lim = LoginLimiter(max_failures=3, window_seconds=100)
    assert lim.retry_after("ip:1", now=0) == 0
    for t in (1, 2, 3):
        lim.record_failure("ip:1", now=t)
    assert lim.retry_after("ip:1", now=4) == 98  # window from the oldest failure at t=1
    assert lim.retry_after("ip:2", now=4) == 0
    assert lim.retry_after("ip:1", now=102) == 0  # oldest failure aged out
    lim.record_failure("ip:1", now=103)
    lim.reset("ip:1")
    assert lim.retry_after("ip:1", now=103) == 0
