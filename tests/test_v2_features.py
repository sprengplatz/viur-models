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
    Field,
    Model,
    set_default_languages,
)
from viur.models import types as viur_types

Position = Spatial(bounds_lat=(46.0, 56.0), bounds_lng=(4.0, 17.0))


class V2Model(Model):
    title: Language[str] = Field(
        default=None, languages=("de", "en"), sa_type=JSON, descr="Titel",
    )
    body: Language[Text] | None = Field(
        default=None, languages=("de", "en"), sa_type=JSON,
    )
    pos: Position | None = Field(default=None, sa_type=JSON, descr="Position")
    pwd: Password | None = Field(default=None, descr="Passwort")


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


def test_partial_dotted_language_keeps_the_other_languages():
    # SQLList.edit seeds the client data with the stored dump so unsubmitted
    # fields keep their value; a form posting only ``title.de`` must fill in
    # over that dict, not replace it — replacing dropped "en" entirely.
    stored = {"title": {"de": "Hallo", "en": "Hello"}}
    instance, errors = V2Model.viur_from_client(stored | {"title.de": "Servus"})
    assert errors == []
    assert instance.title == {"de": "Servus", "en": "Hello"}

    # an explicitly submitted language still wins, clearing included
    instance, errors = V2Model.viur_from_client(stored | {"title.en": ""})
    assert errors == []
    assert instance.title == {"de": "Hallo", "en": ""}


def test_language_default_languages_fallback():
    set_default_languages("de", "fr")
    try:
        class Defaulted(Model):
            name: Language[str] | None = Field(default=None, sa_type=JSON)

        assert Defaulted.viur_structure()["name"]["languages"] == ["de", "fr"]
    finally:
        set_default_languages()

    with pytest.raises(TypeError, match="set_default_languages"):
        class Missing(Model):
            name: Language[str] | None = Field(default=None, sa_type=JSON)

    with pytest.raises(TypeError, match="Language"):
        class NoWrapper(Model):
            name: str | None = Field(default=None, languages=("de",))


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

    class MarkerOnly(Model):
        items: KeyList | None = Field(default=None, sa_type=JSON)

    bone = MarkerOnly.viur_structure()["items"]
    assert (bone["type"], bone["foo"], bone["emptyvalue"]) == ("keylist", 1, None)


# --------------------------------------------------------------------------- #
# pydantic ecosystem types                                                     #
# --------------------------------------------------------------------------- #

class EcoModel(Model):
    mail: EmailStr | None = Field(default=None)
    site: AnyUrl | None = Field(default=None, sa_type=String)
    tint: PydanticColor | None = Field(default=None, sa_type=String)
    short: constr(max_length=12) | None = Field(default=None)


def test_exclusive_bounds_feed_the_structure():
    from pydantic import ByteSize, NegativeInt, PositiveInt, conint

    class Bounded(Model):
        count: PositiveInt | None = Field(default=None)       # Gt(0) -> min 1
        debt: NegativeInt | None = Field(default=None)        # Lt(0) -> max -1
        window: conint(gt=2, lt=10) | None = Field(default=None)
        size: ByteSize | None = Field(default=None)           # int subclass -> numeric
        rate: float | None = Field(default=None, schema_extra={"json_schema_extra": {}})

    structure = Bounded.viur_structure()
    assert structure["count"]["min"] == 1
    assert structure["debt"]["max"] == -1
    assert (structure["window"]["min"], structure["window"]["max"]) == (3, 9)
    assert structure["size"]["type"] == "numeric"
    # floats have no exact inclusive bound for gt/lt — int64 default stays
    from pydantic import confloat

    class FloatBounded(Model):
        factor: confloat(gt=0) | None = Field(default=None)

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