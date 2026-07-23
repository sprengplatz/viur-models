"""v2 features — languages, write-only, spatial, multiple constraints,
pydantic ecosystem registrations."""
import typing as t

import pytest
from pydantic import AnyUrl, EmailStr, constr
from pydantic_extra_types.color import Color as PydanticColor
from sqlalchemy import JSON, String

from viur.models import (
    Language,
    Password,
    Spatial,
    Text,
    ViURField,
    ViURModel,
    set_default_languages,
)
from viur.models import types as viur_types

Position = Spatial(bounds_lat=(46.0, 56.0), bounds_lng=(4.0, 17.0))


class V2Model(ViURModel):
    title: Language[str] = ViURField(
        default=None, languages=("de", "en"), sa_type=JSON, descr="Titel",
    )
    body: Language[Text] | None = ViURField(
        default=None, languages=("de", "en"), sa_type=JSON,
    )
    pos: Position | None = ViURField(default=None, sa_type=JSON, descr="Position")
    pwd: Password | None = ViURField(default=None, descr="Passwort")


STRUCTURE = V2Model.viur_structure()


# --------------------------------------------------------------------------- #
# Language[X]                                                                  #
# --------------------------------------------------------------------------- #

def test_language_wrapper_emits_inner_bone_with_languages():
    title = STRUCTURE["title"]
    assert (title["type"], title["languages"]) == ("str", ["de", "en"])
    assert (title["maxlength"], title["emptyvalue"]) == (254, "")
    body = STRUCTURE["body"]  # inner marker (Text) drives the bone type
    assert (body["type"], body["languages"]) == ("text", ["de", "en"])


def test_language_dump_normalizes_to_declared_languages():
    entry = V2Model(title={"de": "Hallo"})
    assert entry.viur_dump()["title"] == {"de": "Hallo", "en": None}
    assert V2Model().viur_dump()["title"] == {"de": None, "en": None}


def test_language_from_client_accepts_dict_and_dotted():
    instance, errors = V2Model.viur_from_client(
        {"title": {"de": "Hallo", "en": "Hello", "fr": "Bonjour"}},
    )
    assert errors == []
    assert instance.title == {"de": "Hallo", "en": "Hello"}  # fr filtered

    instance, errors = V2Model.viur_from_client({"title.de": "Neu", "title.en": "New"})
    assert errors == []
    assert instance.title == {"de": "Neu", "en": "New"}


def test_language_default_languages_fallback():
    set_default_languages("de", "fr")
    try:
        class Defaulted(ViURModel):
            name: Language[str] | None = ViURField(default=None, sa_type=JSON)

        assert Defaulted.viur_structure()["name"]["languages"] == ["de", "fr"]
    finally:
        set_default_languages()

    with pytest.raises(TypeError, match="set_default_languages"):
        class Missing(ViURModel):
            name: Language[str] | None = ViURField(default=None, sa_type=JSON)

    with pytest.raises(TypeError, match="Language"):
        class NoWrapper(ViURModel):
            name: str | None = ViURField(default=None, languages=("de",))


# --------------------------------------------------------------------------- #
# write-only (Password / Credential)                                          #
# --------------------------------------------------------------------------- #

def test_password_structure_and_write_only_dump():
    pwd = STRUCTURE["pwd"]
    assert (pwd["type"], pwd["test_threshold"]) == ("password", 4)
    assert len(pwd["tests"]) == 5

    entry = V2Model(pwd="Sup3r$ecret")
    assert entry.pwd == "Sup3r$ecret"          # stored/usable in hooks
    assert entry.viur_dump()["pwd"] == ""      # never leaves the model
    assert V2Model.viur_write_only() == {"pwd"}


def test_write_only_accepts_client_input():
    instance, errors = V2Model.viur_from_client({"pwd": "Sup3r$ecret"})
    assert errors == [] and instance.pwd == "Sup3r$ecret"


def test_write_only_empty_submission_is_ignored():
    # PasswordBone semantics: "" never overwrites — the field is dropped
    instance, errors = V2Model.viur_from_client({"pwd": ""})
    assert errors == [] and instance.pwd is None


def test_spatial_empty_submission_clears():
    instance, errors = V2Model.viur_from_client({"pos.lat": "", "pos.lng": ""})
    assert errors == [] and instance.pos is None
    instance, errors = V2Model.viur_from_client({"pos": ""})
    assert errors == [] and instance.pos is None


def test_spatial_accepts_dict_input():
    # JSON clients send {"lat": …, "lng": …} instead of dotted params
    instance, errors = V2Model.viur_from_client({"pos": {"lat": "50.1", "lng": "8.6"}})
    assert errors == [] and instance.pos == (50.1, 8.6)
    instance, errors = V2Model.viur_from_client({"pos": {"lat": "", "lng": ""}})
    assert errors == [] and instance.pos is None


# --------------------------------------------------------------------------- #
# Spatial                                                                      #
# --------------------------------------------------------------------------- #

def test_spatial_structure_and_dump():
    pos = STRUCTURE["pos"]
    assert (pos["type"], pos["emptyvalue"]) == ("spatial", [0.0, 0.0])
    assert (pos["boundslat"], pos["boundslng"]) == ([46.0, 56.0], [4.0, 17.0])

    entry = V2Model(pos=(48.1, 11.5))
    assert entry.viur_dump()["pos"] == [48.1, 11.5]  # JSON-shape, like the real bone


def test_spatial_from_client_accepts_dotted_and_list():
    instance, errors = V2Model.viur_from_client({"pos.lat": "48.2", "pos.lng": "11.6"})
    assert errors == [] and instance.pos == (48.2, 11.6)

    instance, errors = V2Model.viur_from_client({"pos": [47.0, 10.0]})
    assert errors == [] and instance.pos == (47.0, 10.0)

    # dotted sub-keys on fields that are neither language nor spatial are ignored
    instance, errors = V2Model.viur_from_client({"pwd.sub": "x"})
    assert errors == [] and instance.pwd is None


def test_non_replace_marker_carries_unmappable_types():
    KeyList = t.Annotated[list[str], viur_types.BoneType("keylist", extras={"foo": 1})]

    class MarkerOnly(ViURModel):
        items: KeyList | None = ViURField(default=None, sa_type=JSON)

    bone = MarkerOnly.viur_structure()["items"]
    assert (bone["type"], bone["foo"], bone["emptyvalue"]) == ("keylist", 1, None)


# --------------------------------------------------------------------------- #
# pydantic ecosystem types                                                     #
# --------------------------------------------------------------------------- #

class EcoModel(ViURModel):
    mail: EmailStr | None = ViURField(default=None)
    site: AnyUrl | None = ViURField(default=None, sa_type=String)
    tint: PydanticColor | None = ViURField(default=None, sa_type=String)
    short: constr(max_length=12) | None = ViURField(default=None)


def test_exclusive_bounds_feed_the_structure():
    from pydantic import ByteSize, NegativeInt, PositiveInt, conint

    class Bounded(ViURModel):
        count: PositiveInt | None = ViURField(default=None)       # Gt(0) -> min 1
        debt: NegativeInt | None = ViURField(default=None)        # Lt(0) -> max -1
        window: conint(gt=2, lt=10) | None = ViURField(default=None)
        size: ByteSize | None = ViURField(default=None)           # int subclass -> numeric
        rate: float | None = ViURField(default=None, schema_extra={"json_schema_extra": {}})

    structure = Bounded.viur_structure()
    assert structure["count"]["min"] == 1
    assert structure["debt"]["max"] == -1
    assert (structure["window"]["min"], structure["window"]["max"]) == (3, 9)
    assert structure["size"]["type"] == "numeric"
    # floats have no exact inclusive bound for gt/lt — int64 default stays
    from pydantic import confloat

    class FloatBounded(ViURModel):
        factor: confloat(gt=0) | None = ViURField(default=None)

    assert FloatBounded.viur_structure()["factor"]["min"] == -9223372036854775806


def test_ecosystem_types_map_via_registry():
    structure = EcoModel.viur_structure()
    assert structure["mail"]["type"] == "str.email"
    assert structure["site"]["type"] == "uri"
    assert structure["tint"]["type"] == "color"
    # constr needs no registration — its constraints feed the derivation
    assert (structure["short"]["type"], structure["short"]["maxlength"]) == ("str", 12)


def test_ecosystem_values_validate_and_stringify_in_dumps():
    instance, errors = EcoModel.viur_from_client(
        {"mail": "a@b.example", "site": "https://viur.dev", "tint": "#ff0000"},
    )
    assert errors == []
    dump = instance.viur_dump()
    assert dump["mail"] == "a@b.example"
    assert dump["site"].startswith("https://viur.dev")
    assert isinstance(dump["tint"], str)

    _, errors = EcoModel.viur_from_client({"site": "kein url"})
    assert [tuple(e.fieldPath) for e in errors] == [("site",)]