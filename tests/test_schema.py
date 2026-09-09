"""Schema snapshots and bone-level diffing.

The diff is what makes the automation reliable, so it is tested on hand-built
snapshots (explicit about what changed) as well as on real snapshots taken
from models.
"""
import json

import pytest
from sqlmodel import Field, Relationship, SQLModel

from sqlalchemy import JSON

from viur.models import Language, Text, UserRef, Field, Model, schema


# --------------------------------------------------------------------------- #
# models for the snapshot tests                                               #
# --------------------------------------------------------------------------- #

class SchemaTag(Model, table=True):
    __tablename__ = "schema_tag"
    name: str = Field(descr="Name", max_length=30)


class SchemaPostTag(SQLModel, table=True):
    __tablename__ = "schema_post_tag"
    post_id: int | None = Field(default=None, foreign_key="schema_post.id", primary_key=True)
    tag_id: int | None = Field(default=None, foreign_key="schema_tag.id", primary_key=True)


class SchemaPost(Model, table=True):
    __tablename__ = "schema_post"
    title: str = Field(descr="Titel", max_length=100)
    tags: list[SchemaTag] = Relationship(link_model=SchemaPostTag)


class SchemaSingle(Model, table=True):
    __tablename__ = "schema_single"
    tag_id: int | None = Field(default=None, foreign_key="schema_tag.id")
    tag: SchemaTag | None = Relationship()


# --------------------------------------------------------------------------- #
# snapshot                                                                    #
# --------------------------------------------------------------------------- #

def test_describe_records_the_physical_relation_shape():
    """The diff needs more than "multiple": it needs the link table and
    which of its columns point where."""
    described = schema.describe(SchemaPost)

    assert described["table"] == "schema_post"
    shape = described["relations"]["tags"]
    assert shape["multiple"] is True
    assert shape["target_table"] == "schema_tag"
    assert shape["link_table"] == "schema_post_tag"
    assert shape["link_parent_fk"] == "post_id"
    assert shape["link_dest_fk"] == "tag_id"


def test_describe_records_a_to_one_relation():
    shape = schema.describe(SchemaSingle)["relations"]["tag"]
    assert shape["multiple"] is False
    assert shape["fk"] == "tag_id"
    assert "link_table" not in shape   # no link table on a to-one relation


class SchemaRefPost(Model, table=True):
    """Carries a CROSS-STORE reference: its relskel would need the datastore
    skeleton registry, i.e. a booted app."""

    __tablename__ = "schema_ref_post"
    owner: UserRef() | None = Field(default=None, sa_type=JSON)


def test_describe_skips_the_crossstore_relskel_lookup():
    """A snapshot must be buildable without a booted app, so the datastore
    lookup behind a cross-store ``relskel`` is not performed — the diff
    ignores relskel anyway."""
    structure = schema.describe(SchemaRefPost)["structure"]
    assert structure["owner"]["relskel"] == {}


def test_describe_still_builds_relskel_for_sql_relations():
    """A plain SQL relation's relskel comes from the target MODEL, needs no
    datastore, and stays populated."""
    structure = schema.describe(SchemaPost)["structure"]
    assert "name" in structure["tags"]["relskel"]


def test_snapshot_covers_the_table_models_and_is_json_serializable():
    data = schema.snapshot()
    assert "schema_post" in data and "schema_tag" in data
    json.dumps(data, default=str)  # must survive the round-trip to disk


def test_snapshot_is_sorted_by_table():
    keys = list(schema.snapshot())
    assert keys == sorted(keys)


# --------------------------------------------------------------------------- #
# storage                                                                     #
# --------------------------------------------------------------------------- #

def test_save_and_load_round_trip(tmp_path):
    directory = schema.snapshot_dir(tmp_path)
    written = schema.save(directory, "abc123", {"t": {"structure": {}}})
    assert written.name == "abc123.json"
    assert schema.load(directory, "abc123") == {"t": {"structure": {}}}


@pytest.mark.parametrize("revision", [None, "", "unknown"])
def test_load_returns_empty_for_a_missing_snapshot(tmp_path, revision):
    """An empty snapshot makes diff() report only additions — the right
    behavior for the first revision and for adopting snapshots later."""
    assert schema.load(tmp_path, revision) == {}


def test_load_tolerates_a_corrupt_snapshot(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    assert schema.load(tmp_path, "bad") == {}


# --------------------------------------------------------------------------- #
# diff — hand-built snapshots                                                 #
# --------------------------------------------------------------------------- #

def _bone(**overrides):
    bone = {
        "type": "str", "multiple": False, "languages": None, "required": False,
        "emptyvalue": "", "readonly": False, "sortindex": 0,
    }
    bone.update(overrides)
    return bone


def _snap(structure, relations=None, table="t"):
    return {table: {
        "table": table, "structure": structure, "relations": relations or {},
    }}


def _kinds(transitions):
    return sorted((item.kind, item.field) for item in transitions)


def test_diff_reports_nothing_for_an_unchanged_snapshot():
    snap = _snap({"a": _bone()})
    assert schema.diff(snap, snap) == []


def test_diff_ignores_sortindex_and_relskel_noise():
    old = _snap({"a": _bone(sortindex=1, relskel={"x": 1})})
    new = _snap({"a": _bone(sortindex=9, relskel={"y": 2})})
    assert schema.diff(old, new) == []


def test_diff_skips_a_brand_new_table():
    """A new table is a plain CREATE TABLE — nothing to reshape."""
    assert schema.diff({}, _snap({"a": _bone()})) == []


def test_diff_detects_added_and_removed_fields():
    old = _snap({"a": _bone(), "gone": _bone()})
    new = _snap({"a": _bone(), "fresh": _bone()})
    assert _kinds(schema.diff(old, new)) == [
        ("field_added", "fresh"), ("field_removed", "gone"),
    ]


def test_diff_detects_a_language_reduction_and_expansion():
    plain = _snap({"a": _bone()})
    multilingual = _snap({"a": _bone(languages=["de", "en"], defaultvalue=None)})

    reduced = schema.diff(multilingual, plain)
    assert [(item.kind, item.detail["languages"]) for item in reduced] == [
        ("languages_reduced", ["de", "en"]),
    ]
    expanded = schema.diff(plain, multilingual)
    assert [(item.kind, item.detail["languages"]) for item in expanded] == [
        ("languages_expanded", ["de", "en"]),
    ]


def test_diff_detects_a_type_change():
    old = _snap({"a": _bone(type="bool", emptyvalue=False)})
    new = _snap({"a": _bone(type="select", emptyvalue=None, values={"y": "Y"})})
    transition = schema.diff(old, new)[0]
    assert transition.kind == "type_changed"
    assert transition.detail == {"from": "bool", "to": "select"}


def test_diff_detects_a_precision_change():
    old = _snap({"a": _bone(type="numeric", precision=0, decimal=False)})
    new = _snap({"a": _bone(type="numeric", precision=2, decimal=False)})
    transition = schema.diff(old, new)[0]
    assert transition.kind == "precision_changed"
    assert transition.detail["precision"] == 2


def test_diff_detects_using_field_changes():
    relations = {"tags": {
        "multiple": True, "target_table": "tag", "fk": None,
        "link_table": "post_tag", "using_fields": ["weight"],
    }}
    grown = dict(relations["tags"], using_fields=["weight", "note"])
    old = _snap({"tags": _bone(type="relational.tag", multiple=True)}, relations)
    new = _snap({"tags": _bone(type="relational.tag", multiple=True)}, {"tags": grown})

    added = schema.diff(old, new)
    assert [(item.kind, item.detail["using_field"]) for item in added] == [
        ("using_field_added", "note"),
    ]
    removed = schema.diff(new, old)
    assert [(item.kind, item.detail["using_field"]) for item in removed] == [
        ("using_field_removed", "note"),
    ]


def test_diff_detects_using_field_change_when_the_bone_also_changed():
    """The using payload is checked even when the relation bone itself
    changed in some unrelated way."""
    base = {
        "multiple": True, "target_table": "tag", "fk": None,
        "link_table": "post_tag", "using_fields": [],
    }
    old = _snap({"tags": _bone(type="relational.tag", multiple=True, descr="A")},
                {"tags": dict(base)})
    new = _snap({"tags": _bone(type="relational.tag", multiple=True, descr="B")},
                {"tags": dict(base, using_fields=["w"])})
    assert ("using_field_added", "tags") in _kinds(schema.diff(old, new))


# --------------------------------------------------------------------------- #
# diff — the multiple↔single switch, including the rename                     #
# --------------------------------------------------------------------------- #

MULTI_RELATION = {
    "multiple": True, "target_table": "tag", "fk": None,
    "link_table": "post_tag", "link_parent_fk": "post_id",
    "link_dest_fk": "tag_id", "using_fields": [],
}
SINGLE_RELATION = {
    "multiple": False, "target_table": "tag", "fk": "tag_id",
    "using_fields": [],
}


def test_diff_detects_a_collapse_that_keeps_the_field_name():
    old = _snap({"tag": _bone(type="relational.tag", multiple=True)},
                {"tag": dict(MULTI_RELATION)})
    new = _snap({"tag": _bone(type="relational.tag", multiple=False)},
                {"tag": dict(SINGLE_RELATION)})

    transition = schema.diff(old, new)[0]
    assert transition.kind == "multiple_collapsed"
    assert transition.detail["link_table"] == "post_tag"
    assert transition.detail["target_column"] == "tag_id"


def test_diff_pairs_a_collapse_across_a_rename():
    """The normal way to write it: the multiple relation is plural, its
    single counterpart singular. Field-by-field that looks like a removal
    plus an addition — and the link between them is what the data
    migration needs."""
    old = _snap({"tags": _bone(type="relational.tag", multiple=True)},
                {"tags": dict(MULTI_RELATION)})
    new = _snap({"tag": _bone(type="relational.tag", multiple=False)},
                {"tag": dict(SINGLE_RELATION)})

    transitions = schema.diff(old, new)
    assert len(transitions) == 1
    assert transitions[0].kind == "multiple_collapsed"
    assert transitions[0].field == "tags -> tag"
    assert transitions[0].detail["link_parent_fk"] == "post_id"
    assert transitions[0].detail["target_column"] == "tag_id"


def test_diff_pairs_an_expansion_across_a_rename():
    old = _snap({"tag": _bone(type="relational.tag", multiple=False)},
                {"tag": dict(SINGLE_RELATION)})
    new = _snap({"tags": _bone(type="relational.tag", multiple=True)},
                {"tags": dict(MULTI_RELATION)})

    transitions = schema.diff(old, new)
    assert len(transitions) == 1
    assert transitions[0].kind == "multiple_expanded"
    assert transitions[0].detail["source_column"] == "tag_id"
    assert transitions[0].detail["link_table"] == "post_tag"


def test_diff_leaves_an_ambiguous_pairing_alone():
    """Two added single relations to the same target: which one the rows
    should move into is not derivable, so the plain add/remove stands
    instead of a guess."""
    old = _snap({"tags": _bone(type="relational.tag", multiple=True)},
                {"tags": dict(MULTI_RELATION)})
    new = _snap(
        {
            "primary": _bone(type="relational.tag", multiple=False),
            "secondary": _bone(type="relational.tag", multiple=False),
        },
        {
            "primary": dict(SINGLE_RELATION),
            "secondary": dict(SINGLE_RELATION, fk="other_id"),
        },
    )
    assert _kinds(schema.diff(old, new)) == [
        ("field_added", "primary"), ("field_added", "secondary"),
        ("field_removed", "tags"),
    ]


def test_diff_does_not_pair_relations_to_different_targets():
    old = _snap({"tags": _bone(type="relational.tag", multiple=True)},
                {"tags": dict(MULTI_RELATION)})
    new = _snap({"owner": _bone(type="relational.user", multiple=False)},
                {"owner": dict(SINGLE_RELATION, target_table="user")})
    assert _kinds(schema.diff(old, new)) == [
        ("field_added", "owner"), ("field_removed", "tags"),
    ]


def test_diff_needs_both_sides_to_pair():
    """Only a removal (no matching addition) stays a removal."""
    old = _snap({"tags": _bone(type="relational.tag", multiple=True)},
                {"tags": dict(MULTI_RELATION)})
    new = _snap({})
    assert _kinds(schema.diff(old, new)) == [("field_removed", "tags")]


# --------------------------------------------------------------------------- #
# diff — on real snapshots                                                    #
# --------------------------------------------------------------------------- #

def test_real_snapshots_diff_a_multiple_to_single_switch():
    """End-to-end on actual models: the plural link-table relation and the
    singular FK relation are recognized as the same relation."""
    old = {"schema_post": schema.describe(SchemaPost)}
    new = {"schema_post": dict(
        schema.describe(SchemaSingle), table="schema_post")}

    transitions = schema.diff(old, new)
    collapses = [item for item in transitions if item.kind == "multiple_collapsed"]
    assert len(collapses) == 1
    assert collapses[0].detail["link_table"] == "schema_post_tag"
    assert collapses[0].detail["target_column"] == "tag_id"


class SchemaLangPost(Model, table=True):
    """Same fields as :class:`SchemaTextPost` but multilingual/typed
    differently — the two describe() results are diffed against each other."""

    __tablename__ = "schema_lang_post"
    body: Language[str] | None = Field(
        default=None, languages=("de", "en"), sa_type=__import__("sqlalchemy").JSON,
    )
    count: int = Field(default=0)


class SchemaTextPost(Model, table=True):
    __tablename__ = "schema_text_post"
    body: Text = Field(default="", required=False)
    count: float = Field(default=0.0)


def test_real_snapshots_diff_language_and_type_changes():
    old = {"p": dict(schema.describe(SchemaLangPost), table="p")}
    new = {"p": dict(schema.describe(SchemaTextPost), table="p")}

    kinds = dict((item.field, item.kind) for item in schema.diff(old, new))
    assert kinds["body"] == "languages_reduced"
    # int and float are BOTH the "numeric" bone — only the precision differs
    # (0 -> 8), so this is a precision change, not a type change
    assert kinds["count"] == "precision_changed"


def test_diff_detects_an_expansion_that_keeps_the_field_name():
    old = _snap({"tag": _bone(type="relational.tag", multiple=False)},
                {"tag": dict(SINGLE_RELATION)})
    new = _snap({"tag": _bone(type="relational.tag", multiple=True)},
                {"tag": dict(MULTI_RELATION)})

    transition = schema.diff(old, new)[0]
    assert transition.kind == "multiple_expanded"
    assert transition.detail["source_column"] == "tag_id"
    assert transition.detail["link_table"] == "post_tag"


def test_prune_removes_orphaned_snapshots(tmp_path):
    """Self-healing housekeeping: an aborted revision run or a deleted
    revision file leaves a snapshot nothing will ever read."""
    directory = schema.snapshot_dir(tmp_path)
    for revision in ("keep1", "keep2", "orphan"):
        schema.save(directory, revision, {})

    removed = schema.prune(directory, {"keep1", "keep2"})

    assert removed == ["orphan"]
    assert sorted(p.stem for p in directory.glob("*.json")) == ["keep1", "keep2"]


def test_prune_tolerates_a_missing_directory(tmp_path):
    assert schema.prune(tmp_path / "nope", {"x"}) == []


def test_diff_detects_changed_select_values():
    old = _snap({"kind": _bone(type="select", emptyvalue=None,
                               values={"yes": "Yes", "no": "No"})})
    new = _snap({"kind": _bone(type="select", emptyvalue=None,
                               values={"yes": "Yes", "no": "No", "maybe": "Maybe"})})

    transition = schema.diff(old, new)[0]
    assert transition.kind == "select_values_changed"
    assert transition.detail == {"added": ["maybe"], "removed": []}

    reverse = schema.diff(new, old)[0]
    assert reverse.detail == {"added": [], "removed": ["maybe"]}


def test_unchanged_select_values_with_other_bone_change_fall_through():
    """A select whose values did not change but whose descr did is not a
    values transition."""
    old = _snap({"kind": _bone(type="select", descr="A", values={"y": "Y"})})
    new = _snap({"kind": _bone(type="select", descr="B", values={"y": "Y"})})
    assert [t.kind for t in schema.diff(old, new)] == []
