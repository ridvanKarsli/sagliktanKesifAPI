from __future__ import annotations

import pytest

from kesif_server.application.authorization import require
from kesif_server.domain.entities import AnalysisStep, Candidate, Page, Role, User
from kesif_server.domain.errors import AuthenticationError, AuthorizationError


def test_role_hierarchy():
    assert Role.ADMIN.at_least(Role.VIEWER) and not Role.VIEWER.at_least(Role.ANALYST)
    u = User(email="a@b.c", display_name="a", role=Role.ANALYST)
    assert u.can(Role.VIEWER) and not u.can(Role.ADMIN)
    u.is_active = False
    assert not u.can(Role.VIEWER)


def test_require():
    with pytest.raises(AuthenticationError):
        require(None, Role.VIEWER)
    with pytest.raises(AuthorizationError):
        require(User(email="a@b.c", display_name="a", role=Role.VIEWER), Role.ADMIN)


def test_steps_ordered():
    assert [s.value for s in AnalysisStep.ordered(["report", "fetch", "rank"])] == ["fetch", "rank", "report"]
    with pytest.raises(ValueError):
        AnalysisStep.ordered(["nope"])


def test_candidate_from_row_defaults_and_extra():
    c = Candidate.from_row("d", {"rank": 1, "chemical_id": "x", "chemical": "X", "score": 1.0, "foo": "bar", "kind": None})
    assert c.kind == "ilaç" and c.status == "yeni" and c.extra == {"foo": "bar"}
    assert c.to_row()["foo"] == "bar"


def test_page_bounds():
    p = Page(offset=-5, limit=99999)
    assert p.offset == 0 and p.limit == 1000
