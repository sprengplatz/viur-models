"""SQLList over the REAL stack: viur-core Module + viur-actions envelope v2.

The unit suite fakes the render; here the module runs with the real
``EnvelopeRenderMixin`` over the real ``DefaultRender`` against SQLite —
asserting the actual wire format (the protocol gate in viur-actions lets
ViURModels through since the isinstance→protocol change).

Actions are invoked through ``Method._func`` — the raw bodies — because
core's ``Method.__call__`` executes the ``@skey``/SSL guards, which need a
live request context that this suite deliberately does not fake.
"""
import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from viur.actions.render_v2 import make_envelope_render_cls
from viur.core import errors
from viur.core.render.json.default import DefaultRender

from viur.models import ViURField, ViURModel, db
from viur.models.sqllist import SQLList


class IntTicket(ViURModel, table=True):
    __tablename__ = "viur_models_int_ticket"
    name: str = ViURField(descr="Name", max_length=50)
    rating: int | None = ViURField(default=None, ge=1, le=5)


class TicketsModule(SQLList):
    model = IntTicket

    def can(self, instance):
        return True


def _call(module, action_name, /, *args, **kwargs):
    return getattr(type(module), action_name)._func(module, *args, **kwargs)


@pytest.fixture()
def module():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db.configure(engine)
    mod = TicketsModule("tickets", "tickets")
    mod.render = make_envelope_render_cls(DefaultRender)(parent=mod)
    yield mod
    db.reset()


def test_add_view_edit_roundtrip_emits_v2_envelopes(module):
    added = json.loads(_call(module, "add", name="Alice", rating="4"))
    assert str(added["version"]) == "2"
    assert (added["action"], added["status"]) == ("add", "success")
    assert added["module"] == "tickets"
    assert added["data"]["name"] == "Alice" and added["data"]["rating"] == 4
    assert added["data"]["key"] and "id" not in added["data"]
    assert "name" in added["structure"]

    key = added["data"]["key"]
    viewed = json.loads(_call(module, "view", key))
    assert (viewed["action"], viewed["status"]) == ("view", "success")
    assert viewed["data"] == added["data"]

    edited = json.loads(_call(module, "edit", key, rating="5"))
    assert (edited["action"], edited["status"]) == ("edit", "success")
    assert edited["data"]["name"] == "Alice"  # merge keeps unsubmitted field
    assert edited["data"]["rating"] == 5


def test_rejected_add_carries_errors_in_the_envelope(module):
    out = json.loads(_call(module, "add", rating="99"))
    assert (out["action"], out["status"]) == ("add", "rejected")
    by_path = {tuple(error["fieldPath"]): error for error in out["errors"]}
    assert by_path[("name",)]["error"] == "NOTSET"
    assert by_path[("rating",)]["error"] == "INVALID"
    assert out["data"]["key"] is None  # nothing persisted


def test_fresh_add_form_is_init(module):
    out = json.loads(_call(module, "add"))
    assert (out["action"], out["status"]) == ("add", "init")
    assert out["errors"] == []


def test_list_envelope_carries_cursor_and_orders(module):
    for name in ("a", "b", "c"):
        _call(module, "add", name=name, rating="3")

    out = json.loads(_call(module, "list", limit="2", orderby="name", orderdir="desc"))
    assert (out["action"], out["datatype"], out["module"]) == ("list", "list", "tickets")
    assert [row["name"] for row in out["data"]] == ["c", "b"]
    assert out["orders"] == [{"field": "name", "dir": "desc"}]
    assert out["cursor"]

    page2 = json.loads(_call(
        module, "list", limit="2", orderby="name", orderdir="desc", cursor=out["cursor"],
    ))
    assert [row["name"] for row in page2["data"]] == ["a"]
    assert page2["cursor"] is None


def test_delete_returns_the_entity(module):
    added = json.loads(_call(module, "add", name="doomed"))
    out = json.loads(_call(module, "delete", added["data"]["key"]))
    assert (out["action"], out["status"]) == ("delete", "success")
    assert out["data"]["name"] == "doomed"

    with pytest.raises(errors.NotFound):
        _call(module, "view", added["data"]["key"])


def test_structure_action_renders_per_action_envelope(module):
    out = json.loads(_call(module, "structure", action="edit"))
    assert out["action"] == "structure.edit"
    assert "rating" in out["structure"]
    assert out["structure"]["rating"]["min"] == 1


def test_forbidden_without_can_override(module):
    class Closed(SQLList):
        model = IntTicket

    closed = Closed("closed", "closed")
    closed.render = make_envelope_render_cls(DefaultRender)(parent=closed)
    with pytest.raises(errors.Forbidden):
        _call(closed, "list")


def test_list_search_and_operator_filters(module):
    for name, rating in (("Alpha", 1), ("beta", 4)):
        _call(module, "add", name=name, rating=str(rating))

    out = json.loads(_call(module, "list", search="alp"))
    assert [row["name"] for row in out["data"]] == ["Alpha"]

    out = json.loads(_call(module, "list", **{"rating$ge": "4"}))
    assert [row["name"] for row in out["data"]] == ["beta"]

    out = json.loads(_call(module, "list", **{"name$lk": "BE"}))
    assert [row["name"] for row in out["data"]] == ["beta"]
