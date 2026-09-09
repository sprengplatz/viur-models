"""Built-in field types vs. the REAL bones — one twin field per type.

Extends the parity coverage of ``test_structure_parity`` to the whole
``viur.models.types`` palette (Raw/Code/Color/Phone/Uri/Uid/SortIndex/
Json/Credential). ``sortindex`` (the position key) is excluded as usual.
"""
import json

from skeletons import TypesRefSkel

from sqlalchemy import JSON

from viur.models import (
    Code,
    Color,
    Credential,
    Json,
    Phone,
    Raw,
    SortIndex,
    Uid,
    Uri,
    Field,
    Model,
)


class TypesParityModel(Model):
    raw: Raw | None = Field(default=None, descr="Raw")
    code: Code | None = Field(default=None, descr="Code")
    color: Color | None = Field(default=None, descr="Color")
    phone: Phone | None = Field(default=None, descr="Phone", max_length=15)
    uri: Uri | None = Field(default=None, descr="Uri")
    uid: Uid | None = Field(default=None, descr="Uid")
    sortindex: SortIndex | None = Field(default=None, descr="Sortindex")
    data: Json | None = Field(default=None, descr="Data", sa_type=JSON)
    secret: Credential | None = Field(default=None, descr="Secret")


FIELDS = ["raw", "code", "color", "phone", "uri", "uid", "sortindex", "data", "secret"]
IGNORED = {"sortindex"}


def _normalize(structure: dict) -> dict:
    plain = json.loads(json.dumps(structure, default=lambda o: getattr(o, "value", str(o))))
    return {
        bone: {k: v for k, v in entries.items() if k not in IGNORED}
        for bone, entries in plain.items()
    }


def test_type_structures_match_the_real_bones():
    skel_structure = _normalize(TypesRefSkel().structure())
    model_structure = _normalize(TypesParityModel.viur_structure())
    for field in FIELDS:
        assert model_structure[field] == skel_structure[field], (
            f"structure drift on {field!r}:\n"
            f"  model: {model_structure[field]}\n"
            f"  skel:  {skel_structure[field]}"
        )