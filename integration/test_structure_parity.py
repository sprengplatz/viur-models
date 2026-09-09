"""Structure parity — Model emission vs. the REAL viur-core bones.

The core promise of viur-models (analysis/01 §1): clients cannot tell a
SQL-backed module from a skeleton-backed one. These tests hold the emitted
structure of a Model against the structure of its skeleton twin
(``skeletons.ParityRefSkel``), field by field.

``sortindex`` is excluded — the skeleton counts its own system bones (incl.
``viurCurrentSeoKeys`` etc.), so absolute positions differ by design.
"""
import json
import typing as t
from datetime import datetime

from skeletons import ParityRefSkel

from viur.models import Email, Field, Model


class ParityModel(Model):
    name: str = Field(descr="Name", max_length=100)
    mail: Email | None = Field(default=None, descr="Mail")
    rating: int | None = Field(default=None, ge=1, le=5, descr="Rating")
    active: bool | None = Field(default=None, descr="Active")
    due: datetime | None = Field(default=None, descr="Due")
    state: t.Literal["new", "done"] | None = Field(
        default=None, descr="State", values={"new": "New", "done": "Done"},
    )


FIELDS = ["name", "mail", "rating", "active", "due", "state"]
IGNORED = {"sortindex"}


def _normalize(structure: dict) -> dict:
    """JSON-normalize (enums -> values), drop position-dependent keys."""
    plain = json.loads(json.dumps(structure, default=lambda o: getattr(o, "value", str(o))))
    return {
        bone: {k: v for k, v in entries.items() if k not in IGNORED}
        for bone, entries in plain.items()
    }


def test_business_fields_match_the_real_bones():
    skel_structure = _normalize(ParityRefSkel().structure())
    model_structure = _normalize(ParityModel.viur_structure())
    for field in FIELDS:
        assert model_structure[field] == skel_structure[field], (
            f"structure drift on {field!r}:\n"
            f"  model: {model_structure[field]}\n"
            f"  skel:  {skel_structure[field]}"
        )


def test_system_bones_match_the_real_skeleton():
    skel_structure = _normalize(ParityRefSkel().structure())
    model_structure = _normalize(ParityModel.viur_structure())
    for bone in ("key", "creationdate", "changedate"):
        assert model_structure[bone] == skel_structure[bone], (
            f"system bone drift on {bone!r}:\n"
            f"  model: {model_structure[bone]}\n"
            f"  skel:  {skel_structure[bone]}"
        )
