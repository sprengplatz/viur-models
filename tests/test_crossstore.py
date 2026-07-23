"""Cross-store references — SQL models referencing datastore skeletons.

The core lookups are patched here (``resolve_relskel`` / ``read_dest``);
the integration suite runs the structure resolution against the REAL
skeleton registry.
"""
import pytest
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, select

from sqlmodel import Field, Relationship

from viur.models import (
    FileRef,
    SkeletonLink,
    SkeletonRef,
    UserRef,
    ViURField,
    ViURModel,
    db,
)
from viur.models import crossstore
from viur.models.sqllist import SQLList

from tests.test_sqllist import RecordingRender

# Originals, saved before the autouse fixture patches the module attributes —
# the core-lookup tests below exercise the real function bodies.
_orig_resolve_relskel = crossstore.resolve_relskel
_orig_read_dest = crossstore.read_dest

FAKE_RELSKEL = {
    "key": {"type": "key"},
    "shortkey": {"type": "raw"},
    "name": {"type": "str"},
}

KNOWN = {
    "user/1": {"key": "user/1", "shortkey": "u1", "name": "alice@example.com"},
    "user/2": {"key": "user/2", "shortkey": "u2", "name": "bob@example.com"},
}


@pytest.fixture(autouse=True)
def _patched_core(monkeypatch):
    monkeypatch.setattr(crossstore, "resolve_relskel", lambda marker: dict(FAKE_RELSKEL))
    monkeypatch.setattr(
        crossstore, "read_dest", lambda marker, key: KNOWN.get(key),
    )


class Article(ViURModel, table=True):
    __tablename__ = "viur_models_test_xstore_article"
    title: str = ViURField(default="", required=False)
    author: UserRef() | None = ViURField(default=None, sa_type=JSON, descr="Autor")
    reviewers: SkeletonRef("user", multiple=True) | None = ViURField(
        default=None, sa_type=JSON, descr="Reviewer",
    )


class ArticleModule(SQLList):
    model = Article

    def __init__(self):
        super().__init__("articles", "/articles")
        self.render = RecordingRender()

    def can(self, instance):
        return True


@pytest.fixture()
def module():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db.configure(engine)
    yield ArticleModule()
    db.reset()


# --------------------------------------------------------------------------- #
# structure                                                                    #
# --------------------------------------------------------------------------- #

def test_crossstore_structure():
    structure = Article.viur_structure()
    author = structure["author"]
    assert (author["type"], author["module"], author["multiple"]) == (
        "relational.user", "user", False,
    )
    assert author["format"] == "$(dest.lastname), $(dest.firstname) ($(dest.name))"
    assert author["relskel"] == FAKE_RELSKEL
    assert (author["using"], author["emptyvalue"]) == (None, None)

    reviewers = structure["reviewers"]
    assert (reviewers["multiple"], reviewers["defaultvalue"]) == (True, [])
    assert reviewers["format"] == "$(dest.name)"


def test_fileref_shape():
    class WithUpload(ViURModel):
        upload: FileRef() | None = ViURField(default=None, sa_type=JSON)

    bone = WithUpload.viur_structure()["upload"]
    assert bone["type"] == "relational.tree.leaf.file.file"
    assert bone["module"] == "file"


def test_core_lookups_against_the_skeleton_registry(monkeypatch):
    """The real resolver/reader bodies, over a faked ``viur.core.skeleton``."""
    import sys
    from types import ModuleType, SimpleNamespace

    class RefCls:
        def structure(self):
            return {"key": {}, "shortkey": {}, "name": {}}

    seen = {}

    class SkelWithRead:
        def read(self, key):
            seen["read"] = key
            return key == "user/1"

        def dump(self, *, bones=()):
            return {bone: bone for bone in bones}

    fake = ModuleType("viur.core.skeleton")
    fake.RefSkel = SimpleNamespace(
        fromSkel=lambda kind, *keys: seen.update(fromSkel=(kind, keys)) or RefCls,
    )
    fake.skeletonByKind = lambda kind: SkelWithRead
    monkeypatch.setitem(sys.modules, "viur.core.skeleton", fake)

    marker = crossstore.SkeletonRefMarker("user", ("name",), "user", None, False)
    assert _orig_resolve_relskel(marker) == {"key": {}, "shortkey": {}, "name": {}}
    # key + shortkey are always included, like RelationalBone's refKeys
    assert seen["fromSkel"] == ("user", ("key", "shortkey", "name"))

    assert _orig_read_dest(marker, "user/1") == {
        "key": "key", "shortkey": "shortkey", "name": "name",
    }
    assert _orig_read_dest(marker, "user/404") is None

    class SkelLegacy:  # pre-``read()`` cores expose fromDB
        read = None

        def fromDB(self, key):
            return True

        def dump(self, *, bones=()):
            return {"legacy": True}

    fake.skeletonByKind = lambda kind: SkelLegacy
    assert _orig_read_dest(marker, "x") == {"legacy": True}


def test_type_suffix_and_extras():
    Special = SkeletonRef(
        "file", type_suffix="tree.leaf.file", extras={"valid_mime_types": None},
    )

    class WithFile(ViURModel):
        upload: Special | None = ViURField(default=None, sa_type=JSON)

    bone = WithFile.viur_structure()["upload"]
    assert bone["type"] == "relational.tree.leaf.file.file"
    assert bone["valid_mime_types"] is None


def test_class_definition_defers_registry_lookup(monkeypatch):
    # No skeleton registry at import time — class definition must not resolve.
    def _boom(marker):
        raise AssertionError("resolved too early")

    monkeypatch.setattr(crossstore, "resolve_relskel", _boom)

    class Deferred(ViURModel):
        ref: SkeletonRef("whatever") | None = ViURField(default=None, sa_type=JSON)

    assert Deferred.viur_crossstore()["ref"].kind == "whatever"  # no resolve yet


# --------------------------------------------------------------------------- #
# dump                                                                         #
# --------------------------------------------------------------------------- #

def test_dump_wraps_the_stored_snapshot():
    article = Article(
        id=1, title="x",
        author=KNOWN["user/1"],
        reviewers=[KNOWN["user/1"], KNOWN["user/2"]],
    )
    dump = article.viur_dump()
    assert dump["author"] == {"dest": KNOWN["user/1"], "rel": None}
    assert [d["dest"]["name"] for d in dump["reviewers"]] == [
        "alice@example.com", "bob@example.com",
    ]
    empty = Article(id=2).viur_dump()
    assert (empty["author"], empty["reviewers"]) == (None, [])


# --------------------------------------------------------------------------- #
# from_client — datastore read builds the snapshot                             #
# --------------------------------------------------------------------------- #

def test_from_client_reads_and_snapshots_the_target():
    instance, errors = Article.viur_from_client({"author": "user/1"})
    assert errors == []
    assert instance.author == KNOWN["user/1"]

    # dump shape roundtrips (edit merge) — snapshot is rebuilt from the key
    instance, errors = Article.viur_from_client(
        {"author": {"dest": {"key": "user/2", "name": "stale value"}}},
    )
    assert errors == []
    assert instance.author["name"] == "bob@example.com"  # refreshed


def test_dest_input_edge_shapes():
    from viur.models.base import _split_dest_input

    assert _split_dest_input("user/1") == ("user/1", None)
    assert _split_dest_input({"dest": "kein dict"}) == (None, None)
    assert _split_dest_input({"dest": {"key": "user/1"}}) == ("user/1", None)  # key-only: kein Fallback
    key, snap = _split_dest_input({"dest": {"key": "user/1", "name": "A"}})
    assert (key, snap) == ("user/1", {"key": "user/1", "name": "A"})


def test_from_client_rejects_unknown_keys_and_clears():
    instance, errors = Article.viur_from_client({"author": "user/999"})
    assert instance is not None  # best-effort form for the re-render
    assert [tuple(e.fieldPath) for e in errors] == [("author",)]

    instance, errors = Article.viur_from_client({"author": ""})
    assert errors == [] and instance.author is None


def test_from_client_multiple_lists():
    instance, errors = Article.viur_from_client({"reviewers": ["user/1", "user/2"]})
    assert errors == []
    assert [d["name"] for d in instance.reviewers] == [
        "alice@example.com", "bob@example.com",
    ]


# --------------------------------------------------------------------------- #
# SQLList roundtrip (JSON column)                                              #
# --------------------------------------------------------------------------- #

def test_crossstore_roundtrip_through_the_database(module):
    verb, created = module.add(
        title="x", author="user/1", reviewers=["user/2"], skey="csrf",
    )
    assert verb == "addSuccess"

    with db.get_session() as session:
        stored = session.exec(select(Article)).one()
    assert stored.author["name"] == "alice@example.com"

    # unsubmitted SINGLE reference survives the merge (re-snapshotted);
    # unsubmitted MULTIPLE clears — browsers send nothing for an empty
    # multi-selection, absent means empty
    verb, edited = module.edit(created.viur_key, title="y")
    assert verb == "editSuccess"
    assert edited.author["name"] == "alice@example.com"
    assert edited.viur_dump()["reviewers"] == []

    # submitted multiple replaces
    verb, edited = module.edit(created.viur_key, reviewers=["user/2"])
    assert verb == "editSuccess"
    assert edited.viur_dump()["reviewers"][0]["dest"]["name"] == "bob@example.com"

# --------------------------------------------------------------------------- #
# link-table-backed multiple references (SkeletonLink)                         #
# --------------------------------------------------------------------------- #

class ArticleReviewLink(SkeletonLink, table=True):
    __tablename__ = "viur_models_test_xstore_reviewlink"
    viur_kind = "user"
    viur_link_ref_keys = ("name",)

    article_id: int | None = Field(
        default=None, foreign_key="viur_models_test_xstore_paper.id", primary_key=True,
    )


class Paper(ViURModel, table=True):
    __tablename__ = "viur_models_test_xstore_paper"

    viur_relation_meta = {
        "reviews": {"descr": "Reviews",
                    "multiple": {"min": 0, "max": 2, "duplicates": False}},
    }

    title: str = ViURField(default="", required=False)
    reviews: list[ArticleReviewLink] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class PaperModule(SQLList):
    model = Paper

    def __init__(self):
        super().__init__("papers", "/papers")
        self.render = RecordingRender()

    def can(self, instance):
        return True


def test_link_backed_structure():
    bone = Paper.viur_structure()["reviews"]
    assert (bone["type"], bone["module"]) == ("relational.user", "user")
    assert bone["descr"] == "Reviews"
    assert bone["multiple"] == {"duplicates": False, "max": 2, "min": 0}
    assert bone["relskel"] == FAKE_RELSKEL
    assert bone["defaultvalue"] == []


def test_link_kind_classvar_is_required():
    class NamelessLink(SkeletonLink):
        pass

    with pytest.raises(TypeError, match="viur_kind"):
        NamelessLink.viur_marker()


def test_link_backed_roundtrip(module):
    engine = db.get_engine()
    paper_module = PaperModule()

    verb, created = paper_module.add(
        title="Paper", reviews=["user/1", "user/2"], skey="csrf",
    )
    assert verb == "addSuccess"

    _, viewed = paper_module.view(created.viur_key)
    dump = viewed.viur_dump()["reviews"]
    assert [d["dest"]["name"] for d in dump] == ["alice@example.com", "bob@example.com"]

    # link rows are real table rows
    with db.get_session() as session:
        rows = session.exec(select(ArticleReviewLink)).all()
    assert sorted(row.key for row in rows) == ["user/1", "user/2"]

    # replace list → orphaned rows are deleted (cascade)
    verb, _ = paper_module.edit(created.viur_key, reviews="user/2")
    assert verb == "editSuccess"
    with db.get_session() as session:
        rows = session.exec(select(ArticleReviewLink)).all()
    assert [row.key for row in rows] == ["user/2"]

    # unknown target rejected; constraints enforced (max 2)
    verb, form = paper_module.edit(created.viur_key, reviews=["user/999"])
    assert verb == "edit" and form.errors[0].errorMessage == "Unknown key"
    _, errors = Paper.viur_from_client({"reviews": ["user/1", "user/2", "user/1"]})
    messages = {error.errorMessage for error in errors}
    assert any("Duplicate" in m for m in messages)
    assert any("max 2" in m for m in messages)


# --------------------------------------------------------------------------- #
# stale targets: edits survive, refresh_crossstore repairs                     #
# --------------------------------------------------------------------------- #

def test_edit_survives_deleted_target(module):
    verb, created = module.add(title="x", author="user/1", skey="csrf")
    assert verb == "addSuccess"

    KNOWN_BACKUP = dict(KNOWN)
    try:
        del KNOWN["user/1"]  # target vanishes from the datastore

        # editing an UNRELATED field must not be blocked; the stale
        # snapshot is kept (the merge roundtrips the full dest shape)
        verb, edited = module.edit(created.viur_key, title="y")
        assert verb == "editSuccess"
        assert edited.author["name"] == "alice@example.com"  # stale but kept

        # actively setting a bare unknown key still rejects
        verb, form = module.edit(created.viur_key, author="user/1")
        assert verb == "edit"
        assert form.errors[0].errorMessage == "Unknown key"
    finally:
        KNOWN.update(KNOWN_BACKUP)


def test_refresh_crossstore_updates_and_clears(module):
    from viur.models import refresh_crossstore

    module.add(title="x", author="user/1", reviewers=["user/1", "user/2"], skey="csrf")

    KNOWN_BACKUP = dict(KNOWN)
    try:
        KNOWN["user/1"] = dict(KNOWN["user/1"], name="alice@renamed.example")
        del KNOWN["user/2"]

        # keep: rename propagates, the vanished target's snapshot stays
        stats = refresh_crossstore(Article, missing="keep")
        assert stats["refreshed"] >= 2 and stats["cleared"] == 0
        with db.get_session() as session:
            stored = session.exec(select(Article)).one()
        assert stored.author["name"] == "alice@renamed.example"
        assert [d["name"] for d in stored.reviewers] == [
            "alice@renamed.example", "bob@example.com",  # stale kept
        ]
        # second keep-run over the unchanged list is a no-op
        second = refresh_crossstore(Article, missing="keep")
        assert (second["refreshed"], second["cleared"]) == (0, 0)

        # set_null: the vanished target drops out of the list
        stats = refresh_crossstore(Article, missing="set_null")
        assert stats["cleared"] == 1
        with db.get_session() as session:
            stored = session.exec(select(Article)).one()
        assert [d["name"] for d in stored.reviewers] == ["alice@renamed.example"]

        # single ref set_null when its target vanishes too
        del KNOWN["user/1"]
        refresh_crossstore(Article, missing="keep")  # keep leaves it untouched
        with db.get_session() as session:
            assert session.exec(select(Article)).one().author is not None
        refresh_crossstore(Article, missing="set_null")
        with db.get_session() as session:
            stored = session.exec(select(Article)).one()
        assert stored.author is None and stored.reviewers == []

        # idempotent: a run over cleared/unchanged rows changes nothing
        assert refresh_crossstore(Article, missing="set_null") == {
            "checked": 0, "refreshed": 0, "cleared": 0,
        }
    finally:
        KNOWN.clear()
        KNOWN.update(KNOWN_BACKUP)


def test_refresh_crossstore_link_tables(module):
    from viur.models import refresh_crossstore
    from tests.test_using_relations import UEntry2, RatedFeedbackLink

    SQLModel.metadata.create_all(db.get_engine())  # tables from the late import

    import viur.models.crossstore as cs
    FEEDBACK = {"fb/1": {"key": "fb/1", "subject": "Alt"}}
    orig_read = cs.read_dest
    cs.read_dest = lambda marker, key: (
        FEEDBACK.get(key) if marker.kind == "feedback" else KNOWN.get(key)
    )
    try:
        with db.get_session() as session:
            entry = UEntry2(title="x")
            entry.feedback = [RatedFeedbackLink(key="fb/1", dest=dict(FEEDBACK["fb/1"]))]
            session.add(entry)

        FEEDBACK["fb/1"] = {"key": "fb/1", "subject": "Neu"}
        stats = refresh_crossstore(UEntry2)
        assert stats["refreshed"] == 1
        # unchanged second run does nothing
        assert refresh_crossstore(UEntry2)["refreshed"] == 0
        with db.get_session() as session:
            link = session.exec(select(RatedFeedbackLink)).one()
        assert link.dest["subject"] == "Neu"

        del FEEDBACK["fb/1"]
        # keep: the vanished target's link row stays
        assert refresh_crossstore(UEntry2, missing="keep")["cleared"] == 0
        with db.get_session() as session:
            assert len(session.exec(select(RatedFeedbackLink)).all()) == 1
        stats = refresh_crossstore(UEntry2, missing="set_null")
        assert stats["cleared"] == 1
        with db.get_session() as session:
            assert session.exec(select(RatedFeedbackLink)).all() == []
    finally:
        cs.read_dest = orig_read


def test_registries_dedupe():
    from viur.models.crossstore import LINK_REGISTRY, MODEL_REGISTRY, _register_link, _register_model

    _register_model(Article)
    before = len(MODEL_REGISTRY)
    _register_model(Article)
    assert len(MODEL_REGISTRY) == before

    from tests.test_using_relations import RatedFeedbackLink
    _register_link(RatedFeedbackLink)
    before = len(LINK_REGISTRY)
    _register_link(RatedFeedbackLink)
    assert len(LINK_REGISTRY) == before


def test_refresh_crossstore_validates_policy():
    from viur.models import refresh_crossstore

    with pytest.raises(ValueError, match="set_null"):
        refresh_crossstore(Article, missing="drop")
    # models without cross-store fields are skipped cheaply
    from tests.test_sqllist import Ticket
    assert refresh_crossstore(Ticket) == {"checked": 0, "refreshed": 0, "cleared": 0}


# --------------------------------------------------------------------------- #
# relations index + targeted refresh (viur-relations analogue)                 #
# --------------------------------------------------------------------------- #

def _index_rows(session):
    from viur.models import CrossStoreIndex
    return session.exec(select(CrossStoreIndex).order_by(
        CrossStoreIndex.field, CrossStoreIndex.target_key,
    )).all()


def test_index_is_maintained_on_writes(module):
    verb, created = module.add(
        title="x", author="user/1", reviewers=["user/1", "user/2"], skey="csrf",
    )
    with db.get_session() as session:
        rows = _index_rows(session)
    assert [(r.field, r.target_key) for r in rows] == [
        ("author", "user/1"), ("reviewers", "user/1"), ("reviewers", "user/2"),
    ]
    assert all(r.model_table == "viur_models_test_xstore_article" for r in rows)

    module.edit(created.viur_key, reviewers="user/2")  # resync on edit
    with db.get_session() as session:
        rows = _index_rows(session)
    assert [(r.field, r.target_key) for r in rows] == [
        ("author", "user/1"), ("reviewers", "user/2"),
    ]

    module.delete(created.viur_key)
    with db.get_session() as session:
        assert _index_rows(session) == []


def test_refresh_for_target_updates_only_matching_rows(module):
    from viur.models import refresh_for_target

    module.add(title="a", author="user/1", reviewers=["user/1", "user/2"], skey="csrf")
    module.add(title="b", author="user/2", skey="csrf")

    KNOWN_BACKUP = dict(KNOWN)
    try:
        # rename user/1: a.author + a.reviewers[0] refresh, user/2 entries pass through
        KNOWN["user/1"] = dict(KNOWN["user/1"], name="alice@renamed.example")
        stats = refresh_for_target("user/1")
        assert stats["refreshed"] == 2 and stats["cleared"] == 0

        with db.get_session() as session:
            a, b = session.exec(select(Article).order_by(Article.title)).all()
        assert a.author["name"] == "alice@renamed.example"
        assert a.reviewers[0]["name"] == "alice@renamed.example"
        assert a.reviewers[1]["name"] == "bob@example.com"   # untouched
        assert b.author["name"] == "bob@example.com"         # different target

        # multiple: rename user/2 -> its list entry AND b's single ref refresh
        KNOWN["user/2"] = dict(KNOWN["user/2"], name="bob@renamed.example")
        assert refresh_for_target("user/2")["refreshed"] == 2
        assert refresh_for_target("user/2")["refreshed"] == 0  # unchanged: no-op
        with db.get_session() as session:
            a, b = session.exec(select(Article).order_by(Article.title)).all()
        assert a.reviewers[1]["name"] == "bob@renamed.example"
        assert b.author["name"] == "bob@renamed.example"

        # deleted target: keep leaves it, set_null clears single + list entry
        del KNOWN["user/2"]
        assert refresh_for_target("user/2", missing="keep")["cleared"] == 0
        stats = refresh_for_target("user/2", missing="set_null")
        assert stats["cleared"] == 2
        with db.get_session() as session:
            a, b = session.exec(select(Article).order_by(Article.title)).all()
            index_left = {(r.field, r.target_key) for r in _index_rows(session)}
        assert [d["name"] for d in a.reviewers] == ["alice@renamed.example"]
        assert b.author is None
        assert ("reviewers", "user/2") not in index_left
        assert ("author", "user/2") not in index_left
    finally:
        KNOWN.clear()
        KNOWN.update(KNOWN_BACKUP)


def test_refresh_for_target_handles_stale_and_foreign_index_rows(module):
    from viur.models import CrossStoreIndex, refresh_for_target

    module.add(title="x", author="user/1", skey="csrf")
    with db.get_session() as session:
        # row vanished outside SQLList + unknown table + renamed field
        session.add(CrossStoreIndex(
            target_key="user/1", model_table="viur_models_test_xstore_article",
            row_id=999, field="author",
        ))
        session.add(CrossStoreIndex(
            target_key="user/1", model_table="no_such_table", row_id=1, field="x",
        ))
        session.add(CrossStoreIndex(
            target_key="user/1", model_table="viur_models_test_xstore_article",
            row_id=1, field="renamed_away",
        ))

    stats = refresh_for_target("user/1")
    assert stats["checked"] == 1  # only the real reference was checked
    with db.get_session() as session:
        rows = _index_rows(session)
    # the stale row/field entries were cleaned up, the foreign table kept
    assert {(r.model_table, r.field) for r in rows} == {
        ("viur_models_test_xstore_article", "author"), ("no_such_table", "x"),
    }


def test_refresh_for_target_link_tables_and_policy(module, monkeypatch):
    from viur.models import refresh_for_target
    from tests.test_using_relations import RatedFeedbackLink, UEntry2

    SQLModel.metadata.create_all(db.get_engine())

    FEEDBACK = {"fb/1": {"key": "fb/1", "subject": "Alt"}}
    monkeypatch.setattr(
        crossstore, "read_dest",
        lambda marker, key: FEEDBACK.get(key) if marker.kind == "feedback" else KNOWN.get(key),
    )

    with db.get_session() as session:
        entry = UEntry2(title="x")
        entry.feedback = [RatedFeedbackLink(key="fb/1", dest=dict(FEEDBACK["fb/1"]))]
        session.add(entry)

    FEEDBACK["fb/1"] = {"key": "fb/1", "subject": "Neu"}
    assert refresh_for_target("fb/1")["refreshed"] == 1
    assert refresh_for_target("fb/1")["refreshed"] == 0  # unchanged: no-op
    with db.get_session() as session:
        assert session.exec(select(RatedFeedbackLink)).one().dest["subject"] == "Neu"

    del FEEDBACK["fb/1"]
    assert refresh_for_target("fb/1", missing="keep")["cleared"] == 0
    assert refresh_for_target("fb/1", missing="set_null")["cleared"] == 1
    with db.get_session() as session:
        assert session.exec(select(RatedFeedbackLink)).all() == []

    with pytest.raises(ValueError, match="set_null"):
        refresh_for_target("fb/1", missing="drop")


# --------------------------------------------------------------------------- #
# automatic trigger: install_refresh_hooks                                     #
# --------------------------------------------------------------------------- #

def test_install_refresh_hooks(module, monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    calls = []

    class FakeSkeleton:
        @classmethod
        def postSavedHandler(cls, skel, key, dbObj):
            calls.append(("orig_saved", str(key)))

        @classmethod
        def postDeletedHandler(cls, skel, key):
            calls.append(("orig_deleted", str(key)))

    skeleton_mod = ModuleType("viur.core.skeleton")
    skeleton_mod.Skeleton = FakeSkeleton

    tasks_mod = ModuleType("viur.core.tasks")

    def CallDeferred(fn):
        def wrapper(*args, _countdown=None, **kwargs):
            return fn(*args, **kwargs)
        return wrapper

    tasks_mod.CallDeferred = CallDeferred

    monkeypatch.setitem(sys.modules, "viur.core.skeleton", skeleton_mod)
    monkeypatch.setitem(sys.modules, "viur.core.tasks", tasks_mod)
    monkeypatch.setattr(sys.modules["viur.core"], "tasks", tasks_mod, raising=False)

    with pytest.raises(ValueError, match="set_null"):
        crossstore.install_refresh_hooks(missing_on_delete="drop")

    crossstore.install_refresh_hooks(countdown=0)
    wrapped = FakeSkeleton.postSavedHandler.__func__
    crossstore.install_refresh_hooks()  # idempotent — no double wrap
    assert FakeSkeleton.postSavedHandler.__func__ is wrapped
    assert FakeSkeleton._viur_models_refresh_hooks is True

    module.add(title="x", author="user/1", skey="csrf")
    KNOWN_BACKUP = dict(KNOWN)
    try:
        # a write to a REFERENCED kind triggers the targeted refresh …
        KNOWN["user/1"] = dict(KNOWN["user/1"], name="alice@renamed.example")
        skel = SimpleNamespace(kindName="user")
        FakeSkeleton.postSavedHandler(skel, "user/1", None)
        assert ("orig_saved", "user/1") in calls  # original handler still ran
        with db.get_session() as session:
            stored = session.exec(select(Article)).one()
        assert stored.author["name"] == "alice@renamed.example"

        # … an unreferenced kind only runs the original handler
        FakeSkeleton.postSavedHandler(SimpleNamespace(kindName="unrelated"), "x/1", None)
        assert calls[-1] == ("orig_saved", "x/1")

        # deletes of unreferenced kinds only run the original handler
        FakeSkeleton.postDeletedHandler(SimpleNamespace(kindName="unrelated"), "x/2")
        assert calls[-1] == ("orig_deleted", "x/2")

        # delete propagates with the set_null default
        del KNOWN["user/1"]
        FakeSkeleton.postDeletedHandler(skel, "user/1")
        assert calls[-1] == ("orig_deleted", "user/1")
        with db.get_session() as session:
            assert session.exec(select(Article)).one().author is None
    finally:
        KNOWN.clear()
        KNOWN.update(KNOWN_BACKUP)
