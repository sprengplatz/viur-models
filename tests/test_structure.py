"""Structure mapping — pydantic/SQLModel fields → bone structure dicts."""
import datetime
import decimal
import enum
import typing as t

import pytest
from sqlmodel import Field, Relationship

from viur.models import BoneType, Email, Text, Field, Model

Slug = t.Annotated[str, BoneType("str.slug", extras={"pattern": "^[a-z-]+$"})]


class Kind(enum.Enum):
    PRAISE = "praise"
    HARSH_WORDS = "harsh_words"


class Sample(Model):
    name: str = Field(descr="Name", max_length=100, min_length=2)
    message: Text = Field(default="", required=False, descr="Message")
    rating: int = Field(ge=1, le=5, descr="Rating")
    price: decimal.Decimal | None = Field(default=None, decimal_places=2, descr="Price")
    factor: float = Field(default=1.5, descr="Factor")
    active: bool = Field(default=True, descr="Active")
    kind: Kind = Field(descr="Kind")
    mood: Kind = Field(default=Kind.PRAISE, descr="Mood")
    status: t.Literal["new", "done"] | None = Field(
        default=None, values={"new": "Neu", "done": "Fertig"},
    )
    due: datetime.date | None = Field(default=None, descr="Due")
    slot: datetime.time | None = Field(default=None, descr="Slot")
    secret: str = Field(default="", visible=False, readonly=True, required=True)
    unindexed: str = Field(default="", index=False, required=False)
    titled: str = Field(default="", title="Custom Title", required=False)
    email: Email = Field(default="", required=False)
    optional_mail: Email | None = Field(default=None)  # marker inside a union arm
    slug: Slug = Field(default="", required=False)
    note: t.Optional[str] = None  # plain pydantic field, no Field
    tagged: str = Field(default="", schema_extra={"json_schema_extra": {"foo": 1}})


STRUCTURE = Sample.viur_structure()

BASE_KEYS = {
    "descr", "type", "required", "params", "visible", "readonly", "unique",
    "languages", "emptyvalue", "indexed", "clone_behavior", "multiple", "sortindex",
}


def test_every_bone_carries_the_basebone_key_set():
    for name, bone in STRUCTURE.items():
        assert BASE_KEYS <= set(bone), f"{name} is missing base keys"
        assert bone["clone_behavior"] == {"strategy": "copy_value"}, name


def test_system_bones_and_ordering():
    assert list(STRUCTURE)[:3] == ["key", "creationdate", "changedate"]
    key = STRUCTURE["key"]
    assert (key["type"], key["visible"], key["readonly"]) == ("key", False, True)
    assert key["sortindex"] == 0
    assert "id" not in STRUCTURE
    created = STRUCTURE["creationdate"]
    assert (created["type"], created["readonly"], created["required"]) == ("date", True, False)
    assert STRUCTURE["name"]["sortindex"] == 3


def test_string_mapping():
    bone = STRUCTURE["name"]
    assert (bone["type"], bone["maxlength"], bone["minlength"]) == ("str", 100, 2)
    assert (bone["required"], bone["emptyvalue"]) == (True, "")
    # no constraints -> StringBone defaults
    plain = STRUCTURE["note"]
    assert (plain["maxlength"], plain["minlength"], plain["required"]) == (254, None, False)
    assert plain["descr"] == "Note"  # title-cased fallback
    assert "defaultvalue" not in plain  # None default is not emitted


def test_text_type_replaces_str_structure():
    bone = STRUCTURE["message"]
    assert (bone["type"], bone["valid_html"], bone["emptyvalue"]) == ("text", None, "")
    assert "maxlength" not in bone


def test_refining_types_keep_their_base_extras():
    email = STRUCTURE["email"]
    assert (email["type"], email["maxlength"]) == ("str.email", 254)  # str extras stay
    # Annotated marker survives inside a union arm (Email | None)
    optional = STRUCTURE["optional_mail"]
    assert (optional["type"], optional["required"]) == ("str.email", False)
    slug = STRUCTURE["slug"]
    assert (slug["type"], slug["pattern"], slug["maxlength"]) == ("str.slug", "^[a-z-]+$", 254)


def test_registry_maps_ecosystem_types():
    from viur.models import Country

    class Address(Model):
        country: Country | None = Field(default=None, descr="Country")

    bone = Address.viur_structure()["country"]
    assert (bone["type"], bone["emptyvalue"]) == ("select.country", None)
    assert bone["values"]["DE"] == "Germany"
    assert len(bone["values"]) > 200


def test_numeric_mappings():
    rating = STRUCTURE["rating"]
    assert (rating["type"], rating["min"], rating["max"]) == ("numeric", 1, 5)
    assert (rating["precision"], rating["decimal"], rating["emptyvalue"]) == (0, False, 0)
    price = STRUCTURE["price"]
    assert (price["precision"], price["decimal"]) == (2, True)
    assert (price["min"], price["max"]) == (-9223372036854775806, 9223372036854775807)
    factor = STRUCTURE["factor"]
    # floats keep decimal: False (like SortIndexBone); only Decimal mode flips it
    assert (factor["precision"], factor["decimal"], factor["defaultvalue"]) == (8, False, 1.5)


def test_bool_and_date_time_mappings():
    assert (STRUCTURE["active"]["type"], STRUCTURE["active"]["emptyvalue"]) == ("bool", False)
    assert STRUCTURE["active"]["defaultvalue"] is True
    due = STRUCTURE["due"]
    assert (due["type"], due["date"], due["time"], due["naive"]) == ("date", True, False, False)
    slot = STRUCTURE["slot"]
    assert (slot["date"], slot["time"]) == (False, True)
    created = STRUCTURE["creationdate"]
    assert (created["date"], created["time"]) == (True, True)


def test_select_mappings():
    kind = STRUCTURE["kind"]
    assert kind["type"] == "select"
    assert kind["values"] == {"praise": "Praise", "harsh_words": "Harsh Words"}
    assert (kind["required"], kind["emptyvalue"]) == (True, None)
    assert STRUCTURE["mood"]["defaultvalue"] == "praise"  # enum default -> value
    status = STRUCTURE["status"]
    assert status["values"] == {"new": "Neu", "done": "Fertig"}  # values override
    assert status["descr"] == "Status"


def test_flag_derivations():
    secret = STRUCTURE["secret"]
    # readOnly forces required off, BaseBone parity
    assert (secret["visible"], secret["readonly"], secret["required"]) == (False, True, False)
    assert STRUCTURE["unindexed"]["indexed"] is False
    assert STRUCTURE["titled"]["descr"] == "Custom Title"
    assert STRUCTURE["tagged"]["descr"] == "Tagged"  # json_schema_extra without viur slot


def test_structure_is_cached_per_class():
    # identity across calls — NOT against an import-time snapshot: SQLList
    # registration (other test files) legitimately drops structure caches
    first = Sample._viur_structure_shared()
    assert Sample._viur_structure_shared() is first
    # the PUBLIC accessor hands out a copy: equal, never the cache itself
    public = Sample.viur_structure()
    assert public == first and public is not first
    public["name"]["descr"] = "mutated by a caller"
    assert Sample._viur_structure_shared()["name"]["descr"] != "mutated by a caller"

    class SampleChild(Sample):
        extra: str = Field(default="", required=False)

    child = SampleChild.viur_structure()
    assert child is not first and "extra" in child and "extra" not in first


def test_unmappable_type_fails_at_class_definition():
    with pytest.raises(TypeError, match="no bone mapping"):
        class Broken(Model):
            blob: dict = Field(default=None)

    with pytest.raises(TypeError, match="no bone mapping"):
        class BrokenGeneric(Model):  # generic alias, not a plain type
            items: list[int] = Field(default=None)

    with pytest.raises(TypeError, match="Union type"):
        class BrokenUnion(Model):
            either: int | str = Field(default=0)


def test_relationships_require_table_models():
    class WithRelation(Model):  # no table=True — no mapper, no FK columns
        author: t.Optional["Sample"] = Relationship()

    with pytest.raises(NotImplementedError, match="table=True"):
        WithRelation.viur_structure()
