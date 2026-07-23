"""Using-relations (edge payload) — the ``RelationalBone(using=RelSkel)``
analogue via association objects: the link table's payload columns ARE the
using-skel; dumps carry ``{"dest": …, "rel": {…}}``.
"""
import pytest
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Relationship, SQLModel, create_engine, select

from viur.models import RelationLink, SkeletonLink, ViURField, ViURModel, db
from viur.models import crossstore
from viur.models.sqllist import SQLList

from tests.test_sqllist import RecordingRender


class UTag(ViURModel, table=True):
    __tablename__ = "viur_models_test_utag"
    name: str = ViURField(default="", required=False)


class UEntryTagLink(RelationLink, table=True):
    __tablename__ = "viur_models_test_utaglink"
    entry_id: int | None = Field(
        default=None, foreign_key="viur_models_test_uentry.id", primary_key=True,
    )
    tag_id: int | None = Field(
        default=None, foreign_key="viur_models_test_utag.id", primary_key=True,
    )
    tag: UTag = Relationship()
    weight: int = ViURField(default=0, ge=0, le=10, descr="Gewichtung")


class UEntry(ViURModel, table=True):
    __tablename__ = "viur_models_test_uentry"

    viur_relation_meta = {
        "tags": {"descr": "Schlagworte", "multiple": {"duplicates": False}},
    }

    title: str = ViURField(default="", required=False)
    tags: list[UEntryTagLink] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class UEntryModule(SQLList):
    model = UEntry

    def __init__(self):
        super().__init__("uentries", "/uentries")
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
    yield UEntryModule()
    db.reset()


def _tag(name):
    with db.get_session() as session:
        tag = UTag(name=name)
        session.add(tag)
    return tag


# --------------------------------------------------------------------------- #
# structure                                                                    #
# --------------------------------------------------------------------------- #

def test_using_structure():
    bone = UEntry.viur_structure()["tags"]
    assert (bone["type"], bone["descr"]) == (
        "relational.viur_models_test_utag", "Schlagworte",
    )
    assert bone["multiple"] == {"duplicates": False, "max": 0, "min": 0}
    using = bone["using"]
    assert set(using) == {"weight"}  # payload fields, not the FKs
    assert (using["weight"]["type"], using["weight"]["min"], using["weight"]["max"]) == (
        "numeric", 0, 10,
    )
    assert set(bone["relskel"]) == {"key", "shortkey", "name"}


def test_misconfigured_link_fails_fast():
    class LonelyLink(RelationLink, table=True):  # valid mapping, but no dest Relationship
        __tablename__ = "viur_models_test_lonelylink"
        parent_id: int | None = Field(
            default=None, foreign_key="viur_models_test_brokenusing.id", primary_key=True,
        )
        note: str = ViURField(default="", required=False, primary_key=True)

    class Broken(ViURModel, table=True):
        __tablename__ = "viur_models_test_brokenusing"
        links: list[LonelyLink] = Relationship()

    with pytest.raises(TypeError, match="exactly ONE to-one"):
        Broken.viur_structure()


# --------------------------------------------------------------------------- #
# dump                                                                         #
# --------------------------------------------------------------------------- #

def test_dump_carries_dest_and_rel(module):
    tag = _tag("wichtig")
    link = UEntryTagLink(tag_id=tag.id, tag=tag, weight=7)
    entry = UEntry(id=1, title="x", tags=[link])
    dumped = entry.viur_dump()["tags"]
    assert dumped == [{
        "dest": {"key": tag.viur_key, "name": "wichtig"},
        "rel": {"weight": 7},
    }]

    # unloaded dest falls back to the key-only dest from the FK
    bare = UEntryTagLink(tag_id=42, weight=1)
    assert UEntry(id=2, tags=[bare]).viur_dump()["tags"][0]["dest"] == {
        "key": UTag.viur_encode_key(42),
    }
    # no dest FK at all -> dest None
    empty = UEntryTagLink(weight=1)
    assert UEntry(id=3, tags=[empty]).viur_dump()["tags"][0]["dest"] is None


# --------------------------------------------------------------------------- #
# from_client                                                                  #
# --------------------------------------------------------------------------- #

def test_from_client_validates_payload(module):
    tag = _tag("t")
    instance, errors = UEntry.viur_from_client(
        {"tags": [{"dest": {"key": tag.viur_key}, "rel": {"weight": "7"}}]},
    )
    assert errors == []
    pending = instance.__dict__["_viur_pending_relations"]["tags"]
    assert (pending[0].tag_id, pending[0].weight) == (tag.id, 7)  # coerced

    # plain key -> payload defaults
    instance, _ = UEntry.viur_from_client({"tags": tag.viur_key})
    assert instance.__dict__["_viur_pending_relations"]["tags"][0].weight == 0

    # invalid payload -> bone wire path [tags, rel, weight]
    _, errors = UEntry.viur_from_client(
        {"tags": [{"dest": {"key": tag.viur_key}, "rel": {"weight": "99"}}]},
    )
    assert [tuple(e.fieldPath) for e in errors] == [("tags", "rel", "weight")]

    # malformed dest key
    _, errors = UEntry.viur_from_client({"tags": ["garbage"]})
    assert [tuple(e.fieldPath) for e in errors] == [("tags",)]

    # duplicates constraint works over link rows too
    _, errors = UEntry.viur_from_client({"tags": [tag.viur_key, tag.viur_key]})
    assert any("Duplicate" in e.errorMessage for e in errors)


# --------------------------------------------------------------------------- #
# SQLList roundtrip                                                            #
# --------------------------------------------------------------------------- #

def test_from_client_accepts_indexed_dotted_using_input(module):
    # vi/admin4 posts relations WITH using payload as
    # ``tags.<idx>.key=…`` + ``tags.<idx>.<payloadfield>=…``
    tag_a, tag_b = _tag("A"), _tag("B")
    instance, errors = UEntry.viur_from_client({
        "title": "x",
        "tags.0.key": tag_a.viur_key, "tags.0.weight": "7",
        "tags.1.key": tag_b.viur_key, "tags.1.weight": "",  # empty → default
    })
    assert errors == []
    links = instance.__dict__["_viur_pending_relations"]["tags"]
    assert [(link.tag_id, link.weight) for link in links] == [
        (tag_a.id, 7), (tag_b.id, 0),
    ]


def test_using_roundtrip(module):
    tag_a, tag_b = _tag("A"), _tag("B")
    verb, created = module.add(
        title="x", skey="csrf",
        tags=[
            {"dest": {"key": tag_a.viur_key}, "rel": {"weight": 3}},
            {"dest": {"key": tag_b.viur_key}, "rel": {"weight": 9}},
        ],
    )
    assert verb == "addSuccess"

    _, viewed = module.view(created.viur_key)
    dumped = viewed.viur_dump()["tags"]
    assert [(d["dest"]["name"], d["rel"]["weight"]) for d in dumped] == [
        ("A", 3), ("B", 9),
    ]

    # replace with new payload; orphans deleted
    verb, _ = module.edit(
        created.viur_key,
        tags=[{"dest": {"key": tag_b.viur_key}, "rel": {"weight": 1}}],
    )
    assert verb == "editSuccess"
    with db.get_session() as session:
        rows = session.exec(select(UEntryTagLink)).all()
    assert [(row.tag_id, row.weight) for row in rows] == [(tag_b.id, 1)]

    # unknown dest target rejected in the session check
    verb, form = module.edit(
        created.viur_key, tags=[UTag.viur_encode_key(999)],
    )
    assert verb == "edit"
    assert form.errors[0].errorMessage == "Unknown key"


# --------------------------------------------------------------------------- #
# SkeletonLink with payload (cross-store using)                                #
# --------------------------------------------------------------------------- #

class RatedFeedbackLink(SkeletonLink, table=True):
    __tablename__ = "viur_models_test_ratedfblink"
    viur_kind = "feedback"
    viur_link_ref_keys = ("subject",)

    entry_id: int | None = Field(
        default=None, foreign_key="viur_models_test_uentry2.id", primary_key=True,
    )
    note: str = ViURField(default="", required=False, descr="Notiz")
    priority: int = ViURField(default=0, ge=0, le=5, descr="Priorität")


class UEntry2(ViURModel, table=True):
    __tablename__ = "viur_models_test_uentry2"
    title: str = ViURField(default="", required=False)
    feedback: list[RatedFeedbackLink] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


def test_skeleton_link_with_payload(module, monkeypatch):
    monkeypatch.setattr(
        crossstore, "resolve_relskel",
        lambda marker: {"key": {}, "shortkey": {}, "subject": {}},
    )
    monkeypatch.setattr(
        crossstore, "read_dest",
        lambda marker, key: {"key": key, "subject": "Betreff"} if key == "fb/1" else None,
    )

    bone = UEntry2.viur_structure()["feedback"]
    assert set(bone["using"]) == {"note", "priority"}

    instance, errors = UEntry2.viur_from_client(
        {"feedback": [{"dest": {"key": "fb/1"}, "rel": {"note": "gelesen"}}]},
    )
    assert errors == []
    link = instance.__dict__["_viur_pending_relations"]["feedback"][0]
    assert (link.key, link.note, link.dest["subject"]) == ("fb/1", "gelesen", "Betreff")

    entry = UEntry2(id=1, feedback=[link])
    assert entry.viur_dump()["feedback"] == [{
        "dest": {"key": "fb/1", "subject": "Betreff"},
        "rel": {"note": "gelesen", "priority": 0},
    }]

    # payload validation errors keep the wire path on cross-store links too
    _, errors = UEntry2.viur_from_client(
        {"feedback": [{"dest": {"key": "fb/1"}, "rel": {"priority": "9"}}]},
    )
    assert [tuple(e.fieldPath) for e in errors] == [("feedback", "rel", "priority")]