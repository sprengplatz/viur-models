"""SQLList prototype — actions, hooks, query building, merge semantics.

Runs under viur-light-mock (identity decorators, Module stand-in) with a
real SQLite in-memory database; the envelope render is replaced by a
recording fake — the real envelope is exercised by the integration suite.
"""
import json
from types import SimpleNamespace

import pytest
from pydantic import computed_field
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, select

from viur.core import errors

from viur.models import Language, Password, Field, Model, db
from viur.models.sqllist import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ModelList,
    SQLList,
    _clamp_limit,
    _decode_cursor,
)


class Ticket(Model, table=True):
    __tablename__ = "viur_models_test_ticket"
    name: str = Field(descr="Name", max_length=50)
    rating: int | None = Field(default=None, ge=1, le=5)
    due: __import__("datetime").datetime | None = Field(default=None, descr="Fällig")
    secret: Password | None = Field(default=None, descr="Geheimnis")


class Leaflet(Model, table=True):
    """Multilingual model — the dotted-merge path of ``edit``."""

    __tablename__ = "viur_models_test_leaflet"
    title: Language[str] = Field(
        default=None, languages=("de", "en"), sa_type=JSON, descr="Titel",
    )


class Quote(Model, table=True):
    """Numeric fields in the three shapes the empty-value rule distinguishes."""

    __tablename__ = "viur_models_test_quote"
    rating: int | None = Field(default=None, ge=1, le=5)          # Optional → None
    price: __import__("decimal").Decimal | None = Field(default=None, decimal_places=2)
    factor: float = Field(default=1.0)                             # not Optional → default
    amount: int = Field()                                          # required → error


class QuoteModule(SQLList):
    model = Quote

    def __init__(self):
        super().__init__("quotes", "/quotes")
        self.render = RecordingRender()

    def can(self, instance):
        return True


class Report(Model, table=True):
    """Model with a computed bone — it reads columns a bonelist may not name."""

    __tablename__ = "viur_models_test_report"
    name: str = Field(default="", required=False)
    score: int = Field(default=0)

    @computed_field
    @property
    def label(self) -> str:
        return f"{self.name}:{self.score}"


class RecordingRender:
    """Captures render calls; returns (verb, skel) tuples for asserting."""

    version = 2  # declares envelope-v2 compliance (see _require_v2_render)

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


class LeafletModule(SQLList):
    model = Leaflet

    def __init__(self):
        super().__init__("leaflets", "/leaflets")
        self.render = RecordingRender()

    def can(self, instance):
        return True


class ReportModule(SQLList):
    model = Report

    def __init__(self):
        super().__init__("reports", "/reports")
        self.render = RecordingRender()

    def can(self, instance):
        return True


def _bonelist(value, *, with_response=True):
    """Request context carrying ``X-VIUR-BONELIST`` (and a response for ``Vary``)."""
    from viur.core import current

    context = SimpleNamespace(
        skey_checked=True, isPostRequest=True,
        request=SimpleNamespace(headers={"X-VIUR-BONELIST": value}),
    )
    if with_response:
        context.response = SimpleNamespace(vary=None)
    current.request.set(context)
    return context


@pytest.fixture()
def module():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db.configure(engine)
    yield TicketModule()
    db.reset()


def _error_text(exc: BaseException) -> str:
    """The human-readable message of a viur-core error, in either mock mode.

    A real viur-core error is an ``HTTPException`` whose ``__init__`` calls
    ``Exception.__init__()`` with NO arguments and keeps the text in
    ``descr`` — so ``str(exc)`` is empty and ``pytest.raises(match=…)`` can
    never match it. The stand-in errors are plain exceptions carrying the
    text in ``args``. This reads whichever applies.
    """
    return getattr(exc, "descr", None) or str(exc)


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
    closed.render = RecordingRender()  # v2 guard passes; the hooks refuse
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


def test_list_ignores_orderby_on_write_only_bones(module):
    """A write-only bone must not be observable through ordering either —
    the filters excluded it, ``orderby`` did not (regression)."""
    _seed(("a", 1), ("b", 2))
    with db.get_session() as session:
        for ticket in session.exec(select(Ticket)).all():
            ticket.secret = f"pw-{ticket.name}"
            session.add(ticket)

    _, result = module.list(orderby="secret", orderdir="desc")
    assert result.get_orders() == []             # not orderable …
    assert [t.name for t in result] == ["a", "b"]  # … falls back to id order


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

    # skey_checked: the real @skey decorator (overlay mode) reads it; the
    # point of this test is the GET, not the security key.
    current.request.set(
        type("Req", (), {"isPostRequest": False, "skey_checked": True})())
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


def test_edit_partial_dotted_language_keeps_the_other_languages(module):
    # Regression: the merge seeds the stored dump, but a dotted sub-key used
    # to REPLACE the whole language dict — an edit posting only ``title.de``
    # wiped every other language of the row.
    leaflets = LeafletModule()
    leaflets.add(skey="csrf", **{"title.de": "Hallo", "title.en": "Hello"})
    key = leaflets.render.calls[-1][1].viur_key

    verb, instance = leaflets.edit(key, skey="csrf", **{"title.de": "Servus"})
    assert verb == "editSuccess"
    assert instance.title == {"de": "Servus", "en": "Hello"}
    with db.get_session() as session:
        stored = session.exec(select(Leaflet)).one()
    assert stored.title == {"de": "Servus", "en": "Hello"}


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

    # skey_checked: the real @skey decorator (overlay mode) reads it; the
    # point of this test is the GET, not the security key.
    current.request.set(
        type("Req", (), {"isPostRequest": False, "skey_checked": True})())
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

def test_view_checks_permission_while_the_instance_is_still_attached(module):
    """``view`` used to close the session BEFORE calling the can-hook, so a
    hook touching an attribute that was not eager-loaded raised on a detached
    instance. ``edit``/``delete`` always checked inside the session; ``view``
    now does too."""
    from sqlalchemy import inspect as sa_inspect

    class Attached(TicketModule):
        seen = []

        def can(self, instance):
            if instance is not None:
                self.seen.append(sa_inspect(instance).session is not None)
            return True

    attached = Attached()
    attached.add(name="a", skey="csrf")
    key = attached.render.calls[-1][1].viur_key
    attached.seen.clear()

    verb, _ = attached.view(key)
    assert verb == "view"
    assert attached.seen == [True]  # attached to a live session during can()


# --------------------------------------------------------------------------- #
# Empty numeric input — NumericBone.isEmpty parity                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("junk", ["", " ", "None", "null", "undefined"])
def test_null_token_numeric_input_counts_as_empty(junk):
    """vi-vue-utils posts ``"" + value`` — the string ``"null"``/``"undefined"`` for an
    untouched field. Core's ``NumericBone.isEmpty`` treats that as empty; a Model must too."""
    from viur.core.bones.base import ReadFromClientErrorSeverity

    instance, errors = Quote.viur_from_client({"rating": junk, "price": junk, "factor": junk, "amount": "3"})
    assert errors == []
    assert (instance.rating, instance.price) == (None, None)  # Optional → None
    assert instance.factor == 1.0                              # not Optional → its default

    _, errors = Quote.viur_from_client({"amount": junk})       # required → NotSet, like core
    assert [(tuple(e.fieldPath), e.severity) for e in errors] == [
        (("amount",), ReadFromClientErrorSeverity.NotSet),
    ]


def test_numeric_input_accepts_comma_and_whitespace():
    instance, errors = Quote.viur_from_client({"rating": " 4 ", "price": "1,50", "amount": "2"})
    assert errors == []
    assert (instance.rating, str(instance.price)) == (4, "1.50")


def test_real_garbage_numeric_input_is_still_an_error():
    """Deliberate deviation from core, where isEmpty swallows anything unparseable:
    a typo must surface, not silently become the empty value."""
    _, errors = Quote.viur_from_client({"rating": "3x", "amount": "2"})
    assert [tuple(e.fieldPath) for e in errors] == [("rating",)]


def test_edit_with_junk_numeric_input_clears_the_field(module):
    quotes = QuoteModule()
    quotes.add(rating="3", amount="1", skey="csrf")
    key = quotes.render.calls[-1][1].viur_key

    verb, instance = quotes.edit(key, rating="undefined", skey="csrf")
    assert verb == "editSuccess"
    assert instance.rating is None and instance.amount == 1


# --------------------------------------------------------------------------- #
# X-VIUR-BONELIST — load and dump only what the client asked for              #
# --------------------------------------------------------------------------- #

def test_list_loads_and_dumps_only_the_client_bonelist(module):
    """Core unserializes a bone on access; here an unrequested column is not
    even fetched (deferred), and dump/structure are subskel-shaped."""
    module.add(name="a", rating="1", skey="csrf")
    module.add(name="b", rating="2", skey="csrf")
    context = _bonelist("name, nope")  # unknown names are ignored

    verb, rows = module.list()
    assert verb == "list" and len(rows) == 2
    for row in rows:
        assert set(row.dump()) == {"key", "name"}
        assert set(row.structure()) == {"key", "name"}
        assert {"rating", "due", "secret", "creationdate", "changedate"} <= sa_inspect(row).unloaded
    assert context.response.vary[0] == "X-VIUR-BONELIST"


def test_view_honours_the_bonelist_and_the_always_bones(module, monkeypatch):
    monkeypatch.setattr(Ticket, "viur_bones_always", ("rating",))  # the "*"-subskel analogue
    module.add(name="a", rating="3", skey="csrf")
    key = module.render.calls[-1][1].viur_key
    _bonelist("name", with_response=False)  # no response object → nothing to Vary

    verb, instance = module.view(key)
    assert verb == "view"
    assert set(instance.dump()) == {"key", "name", "rating"}
    assert instance.dump()["rating"] == 3
    assert "due" in sa_inspect(instance).unloaded


def test_bonelist_still_loads_the_sort_column_for_the_cursor(module):
    for index in range(3):
        module.add(name=f"t{index}", rating=str(index + 1), skey="csrf")
    _bonelist("name")

    verb, rows = module.list(orderby="rating", limit=2)  # rating: sorted by, not dumped
    assert rows.getCursor()                              # the cursor encodes the sort value
    assert [row.name for row in rows] == ["t0", "t1"]
    assert "rating" not in rows[0].dump()


def test_bonelist_with_a_computed_bone_loads_the_full_row(module):
    """A computed bone reads whatever columns it likes — no load_only then."""
    reports = ReportModule()
    reports.add(name="x", score="7", skey="csrf")
    _bonelist("label")

    verb, rows = reports.list()
    assert rows[0].dump() == {"key": rows[0].viur_key, "label": "x:7"}
    assert "score" not in sa_inspect(rows[0]).unloaded


def test_empty_bonelist_header_means_no_restriction(module):
    module.add(name="a", rating="2", skey="csrf")
    _bonelist("")
    verb, rows = module.list()
    assert "rating" in rows[0].dump()


def test_edit_ignores_the_bonelist(module):
    """edit merges the stored dump — it must never see a restricted one."""
    module.add(name="orig", rating="2", skey="csrf")
    key = module.render.calls[-1][1].viur_key
    _bonelist("name")

    verb, instance = module.edit(key, rating="5", skey="csrf")
    assert verb == "editSuccess"
    assert (instance.name, instance.rating) == ("orig", 5)
    assert "rating" in instance.dump()


def test_structure_renders_per_action(module):
    verb, form = module.structure(action="view")
    assert verb == "structure.view"
    assert form.structure() == Ticket.viur_structure()


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


class NumbersOnly(Model, table=True):
    __tablename__ = "viur_models_test_numbersonly"
    value: int | None = Field(default=None)


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


# --------------------------------------------------------------------------- #
# envelope-v2 enforcement                                                     #
# --------------------------------------------------------------------------- #

class _V1Render:
    """A render WITHOUT the v2 marker — viur-core's DefaultRender shape."""

    def view(self, skel, **kwargs): return ("view", skel)
    def list(self, skellist, **kwargs): return ("list", skellist)


def test_every_action_refuses_a_non_v2_render(module):
    """SQLList has no v1 wire shape: core's v1 renderEntry is isinstance-
    gated on SkeletonInstance and would serialize a Model through a
    deprecation fallback — silently wrong responses. A non-upgraded mount
    must therefore answer 406, not degrade."""
    _seed(("a", 1),)
    with db.get_session() as session:
        key = session.exec(select(Ticket)).one().viur_key
    module.render = _V1Render()

    for call in (
        lambda: module.list(),
        lambda: module.view(key),
        lambda: module.add(name="x", skey="s"),
        lambda: module.edit(key, name="y", skey="s"),
        lambda: module.delete(key, skey="s"),
        lambda: module.structure("view"),
    ):
        with pytest.raises(errors.NotAcceptable) as raised:
            call()
        assert "envelope v2" in _error_text(raised.value)


def test_missing_render_is_also_refused(module):
    del module.render
    with pytest.raises(errors.NotAcceptable) as raised:
        module.list()
    assert "viur.actions.install" in _error_text(raised.value)


def test_the_v2_pin_is_built_in():
    """``viur.actions.install()`` upgrades every mount of a class carrying
    ``json_version = 2`` — subclasses must not need to repeat it."""
    assert SQLList.json_version == 2
    assert TicketModule.json_version == 2  # inherited
