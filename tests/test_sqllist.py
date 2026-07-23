"""SQLList prototype — actions, hooks, query building, merge semantics.

Runs under viur-light-mock (identity decorators, Module stand-in) with a
real SQLite in-memory database; the envelope render is replaced by a
recording fake — the real envelope is exercised by the integration suite.
"""
import json

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, select

from viur.core import errors

from viur.models import Password, ViURField, ViURModel, db
from viur.models.sqllist import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ModelList,
    SQLList,
    _clamp_limit,
    _decode_cursor,
)


class Ticket(ViURModel, table=True):
    __tablename__ = "viur_models_test_ticket"
    name: str = ViURField(descr="Name", max_length=50)
    rating: int | None = ViURField(default=None, ge=1, le=5)
    due: __import__("datetime").datetime | None = ViURField(default=None, descr="Fällig")
    secret: Password | None = ViURField(default=None, descr="Geheimnis")


class RecordingRender:
    """Captures render calls; returns (verb, skel) tuples for asserting."""

    def __init__(self):
        self.calls = []

    def _record(self, verb, skel, **kwargs):
        self.calls.append((verb, skel, kwargs))
        return (verb, skel)

    def view(self, skel, **kwargs): return self._record("view", skel, **kwargs)
    def add(self, skel, **kwargs): return self._record("add", skel, **kwargs)
    def edit(self, skel, **kwargs): return self._record("edit", skel, **kwargs)
    def addSuccess(self, skel, **kwargs): return self._record("addSuccess", skel, **kwargs)
    def editSuccess(self, skel, **kwargs): return self._record("editSuccess", skel, **kwargs)
    def deleteSuccess(self, skel, **kwargs): return self._record("deleteSuccess", skel, **kwargs)
    def list(self, skellist, **kwargs): return self._record("list", skellist, **kwargs)
    def render(self, action, skel, **kwargs): return self._record(action, skel, **kwargs)


class TicketModule(SQLList):
    model = Ticket

    def __init__(self):
        super().__init__("tickets", "/tickets")
        self.render = RecordingRender()
        self.hook_log = []

    def can(self, instance):
        return True

    def on(self, instance):
        self.hook_log.append(("on", instance))

    def then(self, instance):
        self.hook_log.append(("then", instance))


@pytest.fixture()
def module():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db.configure(engine)
    yield TicketModule()
    db.reset()


def _seed(*names_ratings):
    with db.get_session() as session:
        for name, rating in names_ratings:
            session.add(Ticket(name=name, rating=rating))


# --------------------------------------------------------------------------- #
# Construction & defaults                                                     #
# --------------------------------------------------------------------------- #

def test_module_without_model_fails_fast():
    class Broken(SQLList):
        pass

    with pytest.raises(NotImplementedError, match="model"):
        Broken("broken", "/broken")


def test_default_hooks_are_fail_closed(module):
    class Closed(SQLList):
        model = Ticket

    closed = Closed("closed", "/closed")
    with pytest.raises(errors.Forbidden):
        closed.list()
    # on/then defaults are no-ops
    assert closed.on(None) is None and closed.then(None) is None


def test_skel_slot_returns_the_model(module):
    assert module.skel() is Ticket


# --------------------------------------------------------------------------- #
# Cursor / limit helpers                                                      #
# --------------------------------------------------------------------------- #

def test_cursor_garbage_handling():
    assert _decode_cursor(None) is None
    assert _decode_cursor("") is None
    assert _decode_cursor("&& garbage &&") is None
    # valid b64/JSON but wrong shape -> restart
    import base64, json
    flat = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).decode().rstrip("=")
    assert _decode_cursor(flat) is None


def test_limit_clamping():
    assert _clamp_limit("7") == 7
    assert _clamp_limit(None) == DEFAULT_LIMIT
    assert _clamp_limit("garbage") == DEFAULT_LIMIT
    assert _clamp_limit(0) == 1
    assert _clamp_limit(10_000) == MAX_LIMIT


def test_modellist_protocol():
    model_list = ModelList([1, 2], cursor="abc", orders=[("name", "asc")])
    assert list(model_list) == [1, 2]
    assert model_list.getCursor() == "abc"
    assert model_list.get_orders() == [("name", "asc")]


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #

def test_list_paginates_with_opaque_cursor(module):
    _seed(("a", 1), ("b", 2), ("c", 3))
    verb, result = module.list(limit="2", orderby="name")
    assert verb == "list"
    assert [t.name for t in result] == ["a", "b"]
    assert result.get_orders() == [("name", "asc")]
    cursor = result.getCursor()
    assert cursor is not None

    _, page2 = module.list(limit="2", orderby="name", cursor=cursor)
    assert [t.name for t in page2] == ["c"]
    assert page2.getCursor() is None  # last page


def test_list_orders_descending_and_filters(module):
    _seed(("a", 1), ("b", 2), ("c", 2))
    _, result = module.list(orderby="name", orderdir="desc", rating=2)
    assert [t.name for t in result] == ["c", "b"]
    assert result.get_orders() == [("name", "desc")]


def test_list_ignores_unknown_and_readonly_parameters(module):
    _seed(("a", 1),)
    _, result = module.list(orderby="creationdate", nonsense="x", creationdate="y")
    assert [t.name for t in result] == ["a"]
    assert result.get_orders() == []  # readonly orderby ignored


def test_list_applies_sql_filter_hook(module):
    _seed(("a", 1), ("b", 5))

    class Filtered(TicketModule):
        def sqlFilter(self, stmt):
            return stmt.where(Ticket.rating >= 5)

    _, result = Filtered().list()
    assert [t.name for t in result] == ["b"]


# --------------------------------------------------------------------------- #
# view                                                                        #
# --------------------------------------------------------------------------- #

def test_view_renders_the_instance(module):
    _seed(("a", 1),)
    with db.get_session() as session:
        key = session.exec(select(Ticket)).one().viur_key
    verb, instance = module.view(key)
    assert (verb, instance.name) == ("view", "a")


def test_view_unknown_and_malformed_keys_are_not_found(module):
    _seed(("a", 1),)
    with pytest.raises(errors.NotFound):
        module.view("garbage-key")
    with pytest.raises(errors.NotFound):
        module.view(Ticket(id=999).viur_key)


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #

def test_add_without_payload_renders_fresh_form(module):
    verb, form = module.add()
    assert verb == "add"
    assert form.id is None and form.errors == []


def test_add_rejected_payload_renders_errors_and_persists_nothing(module):
    verb, form = module.add(rating="99", skey="csrf")
    assert verb == "add"
    paths = {tuple(error.fieldPath) for error in form.errors}
    assert ("name",) in paths and ("rating",) in paths
    with db.get_session() as session:
        assert session.exec(select(Ticket)).all() == []


def test_add_rejected_payload_roundtrips_submitted_values(module):
    # the re-rendered form carries the client's input — valid AND invalid
    # fields — so nothing typed is lost (skel.fromClient parity)
    verb, form = module.add(name="Typed Name", rating="99", skey="csrf")
    assert verb == "add"
    assert form.errors
    dump = form.viur_dump()
    assert dump["name"] == "Typed Name"
    assert dump["rating"] == "99"  # raw input, unvalidated
    with db.get_session() as session:
        assert session.exec(select(Ticket)).all() == []


def test_add_persists_and_runs_hooks_in_order(module):
    verb, instance = module.add(name="new", rating="4", skey="csrf")
    assert verb == "addSuccess"
    assert instance.id is not None and instance.rating == 4
    assert module.hook_log == [("on", instance), ("then", instance)]
    with db.get_session() as session:
        assert session.exec(select(Ticket)).one().name == "new"


def test_add_bounce_only_renders_fresh_form_and_persists_nothing(module):
    # vi/admin4 opens the add form via POST with skey + bounce=true
    verb, form = module.add(bounce="true", skey="csrf")
    assert verb == "add"
    assert form.id is None and form.errors == []
    with db.get_session() as session:
        assert session.exec(select(Ticket)).all() == []


def test_add_bounce_with_data_renders_validated_preview_without_saving(module):
    verb, form = module.add(name="draft", rating="3", bounce="true", skey="csrf")
    assert verb == "add"
    assert (form.name, form.rating, form.id) == ("draft", 3, None)
    assert module.hook_log == []  # neither onAdd nor thenAdd ran
    with db.get_session() as session:
        assert session.exec(select(Ticket)).all() == []


def test_add_get_request_never_writes(module):
    from viur.core import current

    current.request.set(type("Req", (), {"isPostRequest": False})())
    try:
        verb, form = module.add(name="sneaky", skey="csrf")
    finally:
        current.request.set(None)
    assert verb == "add"
    assert form.name == "sneaky" and form.id is None  # preview only
    with db.get_session() as session:
        assert session.exec(select(Ticket)).all() == []


# --------------------------------------------------------------------------- #
# edit                                                                        #
# --------------------------------------------------------------------------- #

def test_edit_without_payload_renders_form(module):
    module.add(name="orig", rating="2", skey="csrf")
    key = module.render.calls[-1][1].viur_key
    verb, form = module.edit(key)
    assert (verb, form.name) == ("edit", "orig")


def test_edit_merges_and_keeps_unsubmitted_fields(module):
    module.add(name="orig", rating="2", skey="csrf")
    created = module.render.calls[-1][1]
    old_changedate = created.changedate

    verb, instance = module.edit(created.viur_key, rating="5", skey="csrf")
    assert verb == "editSuccess"
    assert (instance.name, instance.rating) == ("orig", 5)  # name kept
    assert instance.changedate > old_changedate
    with db.get_session() as session:
        stored = session.exec(select(Ticket)).one()
    assert (stored.name, stored.rating) == ("orig", 5)


def test_edit_rejected_payload_keeps_stored_values(module):
    module.add(name="orig", rating="2", skey="csrf")
    key = module.render.calls[-1][1].viur_key

    verb, form = module.edit(key, rating="99")
    assert verb == "edit"
    assert [tuple(e.fieldPath) for e in form.errors] == [("rating",)]
    # the re-render shows the submitted (merged) values, keyed like the row …
    assert (form.name, form.rating, form.viur_key) == ("orig", "99", key)
    # … while the stored row keeps its values
    with db.get_session() as session:
        assert session.exec(select(Ticket)).one().rating == 2


def test_edit_bounce_renders_merged_preview_without_writing(module):
    module.add(name="orig", rating="2", skey="csrf")
    created = module.render.calls[-1][1]

    verb, preview = module.edit(created.viur_key, rating="5", bounce="true", skey="csrf")
    assert verb == "edit"
    # preview shows the merged values, keyed like the stored row …
    assert (preview.name, preview.rating) == ("orig", 5)
    assert preview.viur_key == created.viur_key
    # … but nothing was written (SQLite returns naive UTC datetimes)
    import datetime as dt
    with db.get_session() as session:
        stored = session.exec(select(Ticket)).one()
    assert stored.rating == 2
    assert stored.changedate.replace(tzinfo=dt.timezone.utc) == created.changedate


def test_edit_empty_value_clears_date_bones(module):
    # HTML forms send cleared inputs as "" — non-string bones clear to None
    module.add(name="t", rating="2", due="2026-01-01T10:00:00+00:00", skey="csrf")
    created = module.render.calls[-1][1]
    assert created.due is not None

    verb, instance = module.edit(created.viur_key, due="", skey="csrf")
    assert verb == "editSuccess" and instance.due is None
    with db.get_session() as session:
        assert session.exec(select(Ticket)).one().due is None


def test_edit_empty_write_only_input_is_ignored(module):
    # PasswordBone semantics: the form renders write-only bones empty, so
    # an unsubmitted or empty value must keep the stored secret
    module.add(name="t", rating="2", secret="altes-geheimnis", skey="csrf")
    key = module.render.calls[-1][1].viur_key

    module.edit(key, rating="3", skey="csrf")   # not submitted
    module.edit(key, secret="", skey="csrf")    # explicitly empty
    with db.get_session() as session:
        stored = session.exec(select(Ticket)).one()
    assert (stored.secret, stored.rating) == ("altes-geheimnis", 3)

    module.edit(key, secret="neues-geheimnis", skey="csrf")  # non-empty wins
    with db.get_session() as session:
        assert session.exec(select(Ticket)).one().secret == "neues-geheimnis"


def test_edit_get_request_never_writes(module):
    from viur.core import current

    module.add(name="orig", rating="2", skey="csrf")
    key = module.render.calls[-1][1].viur_key

    current.request.set(type("Req", (), {"isPostRequest": False})())
    try:
        verb, preview = module.edit(key, rating="4", skey="csrf")
    finally:
        current.request.set(None)
    assert (verb, preview.rating) == ("edit", 4)
    with db.get_session() as session:
        assert session.exec(select(Ticket)).one().rating == 2


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #

def test_delete_removes_row_and_renders_entity(module):
    module.add(name="doomed", rating="1", skey="csrf")
    doomed = module.render.calls[-1][1]
    module.hook_log.clear()

    verb, instance = module.delete(doomed.viur_key)
    assert (verb, instance.name) == ("deleteSuccess", "doomed")
    assert module.hook_log == [("on", instance), ("then", instance)]
    with db.get_session() as session:
        assert session.exec(select(Ticket)).all() == []


# --------------------------------------------------------------------------- #
# structure                                                                   #
# --------------------------------------------------------------------------- #

def test_structure_renders_per_action(module):
    verb, form = module.structure(action="view")
    assert verb == "structure.view"
    assert form.structure() is Ticket.viur_structure()


def test_structure_unknown_action_is_not_implemented(module):
    with pytest.raises(errors.NotImplemented):
        module.structure(action="fly")


def test_renderer_opt_in_flags_and_handler():
    # __build_app only mounts modules whose class carries a truthy attribute
    # per renderer family — SQLList targets the json/vi envelope APIs.
    assert SQLList.json is True and SQLList.vi is True
    assert SQLList.handler == "list"


# --------------------------------------------------------------------------- #
# search + query language                                                      #
# --------------------------------------------------------------------------- #

def test_search_matches_string_fields_case_insensitively(module):
    _seed(("Alpha", 1), ("beta", 2), ("Gamma%x", 3))
    _, result = module.list(search="alph")
    assert [t.name for t in result] == ["Alpha"]
    # LIKE wildcards in the term are escaped — "%" matches only literally
    _, result = module.list(search="%")
    assert [t.name for t in result] == ["Gamma%x"]
    _, result = module.list(search="")
    assert len(result) == 3  # empty term: no restriction


class NumbersOnly(ViURModel, table=True):
    __tablename__ = "viur_models_test_numbersonly"
    value: int | None = ViURField(default=None)


class NumbersModule(SQLList):
    model = NumbersOnly

    def __init__(self):
        super().__init__("numbers", "/numbers")
        self.render = RecordingRender()

    def can(self, instance):
        return True


def test_search_without_searchable_fields_is_unsatisfiable(module):
    with db.get_session() as session:
        session.add(NumbersOnly(value=1))
    _, result = NumbersModule().list(search="1")
    assert list(result) == []  # like core without a fulltext adapter


def test_operator_filters(module):
    _seed(("a", 1), ("b", 2), ("c", 3))
    cases = [
        ({"rating$gt": "1"}, ["b", "c"]),
        ({"rating$ge": 2}, ["b", "c"]),
        ({"rating$lt": 3}, ["a", "b"]),
        ({"rating$le": "2"}, ["a", "b"]),
        ({"name$lk": "B"}, ["b"]),            # case-insensitive prefix
        ({"name$unknown": "b"}, ["b"]),       # unknown suffix -> equality
        ({"rating$gt": [1, 2]}, ["a", "b", "c"]),  # operators take scalars only
        ({"rating$gt": "abc"}, ["a", "b", "c"]),   # unusable numeric -> ignored
        ({"rating": [1, 3]}, ["a", "c"]),     # list -> IN
        ({"rating": ["1", "x", "3"]}, ["a", "c"]),  # list items coerced/dropped
    ]
    for params, expected in cases:
        _, result = module.list(orderby="name", **params)
        assert [t.name for t in result] == expected, params


def test_relation_and_write_only_fields_are_not_filterable(module):
    from tests.test_relations import PostModule, _author

    author = _author("Alice")
    post_module = PostModule()
    post_module.add(title="x", author=author.viur_key, skey="csrf")
    # filtering on the relation bone name is ignored (no crash, no effect)
    _, result = post_module.list(author="nonsense")
    assert len(result) == 1


# --------------------------------------------------------------------------- #
# keyset pagination                                                            #
# --------------------------------------------------------------------------- #

def test_keyset_is_stable_under_concurrent_inserts(module):
    _seed(("b", 1), ("d", 2), ("f", 3))
    _, page1 = module.list(limit="2", orderby="name")
    assert [t.name for t in page1] == ["b", "d"]

    # a row inserted BEFORE the cursor position must not shift the next page
    _seed(("a", 9),)
    _, page2 = module.list(limit="2", orderby="name", cursor=page1.getCursor())
    assert [t.name for t in page2] == ["f"]  # OFFSET would have repeated "d"
    assert page2.getCursor() is None


def test_keyset_descending_and_duplicate_sort_values(module):
    _seed(("x", 1), ("x", 2), ("a", 3))
    _, page1 = module.list(limit="2", orderby="name", orderdir="desc")
    assert [(t.name, t.rating) for t in page1] == [("x", 1), ("x", 2)]  # id tiebreaker
    _, page2 = module.list(
        limit="2", orderby="name", orderdir="desc", cursor=page1.getCursor(),
    )
    assert [t.name for t in page2] == ["a"]


def test_keyset_null_tail(module):
    _seed(("a", 1), ("b", None), ("c", None))
    _, page1 = module.list(limit="2", orderby="rating")
    # NULLS LAST: the valued row first, then the NULL tail
    assert [t.name for t in page1] == ["a", "b"]
    _, page2 = module.list(limit="2", orderby="rating", cursor=page1.getCursor())
    assert [t.name for t in page2] == ["c"]


def test_keyset_without_orderby_pages_by_id(module):
    _seed(("a", 1), ("b", 2), ("c", 3))
    _, page1 = module.list(limit="2")
    assert [t.name for t in page1] == ["a", "b"]
    _, page2 = module.list(limit="2", cursor=page1.getCursor())
    assert [t.name for t in page2] == ["c"]


def test_cursor_is_bound_to_its_order(module):
    _seed(("a", 3), ("b", 2), ("c", 1))
    _, page1 = module.list(limit="1", orderby="name")
    # different order -> cursor ignored, listing restarts
    _, restarted = module.list(limit="3", orderby="rating", cursor=page1.getCursor())
    assert [t.rating for t in restarted] == [1, 2, 3]


def test_keyset_datetime_cursor_roundtrip(module):
    import datetime as dt

    base = dt.datetime(2026, 7, 15, 12, 0)
    with db.get_session() as session:
        for index, name in enumerate(("a", "b", "c")):
            session.add(Ticket(name=name, due=base + dt.timedelta(hours=index)))

    _, page1 = module.list(limit="2", orderby="due")
    assert [t.name for t in page1] == ["a", "b"]
    # the datetime travels through the JSON cursor and back into a comparison
    _, page2 = module.list(limit="2", orderby="due", cursor=page1.getCursor())
    assert [t.name for t in page2] == ["c"]


def test_cursor_value_coercion_branches():
    import datetime as dt
    import decimal
    from types import SimpleNamespace as NS

    from viur.models.sqllist import _coerce_cursor_value

    def col(python_type):
        return NS(type=NS(python_type=python_type))

    class RaisingType:
        @property
        def python_type(self):
            raise NotImplementedError

    assert _coerce_cursor_value(NS(type=RaisingType()), "x") == "x"
    assert _coerce_cursor_value(
        col(dt.datetime), "2026-07-15T12:00:00+00:00",
    ) == dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)
    assert _coerce_cursor_value(col(dt.date), "2026-07-15") == dt.date(2026, 7, 15)
    assert _coerce_cursor_value(col(dt.time), "10:30:00") == dt.time(10, 30)
    assert _coerce_cursor_value(col(decimal.Decimal), 9.5) == decimal.Decimal("9.5")
    assert _coerce_cursor_value(col(int), 7) == 7
    assert _coerce_cursor_value(col(int), None) is None
