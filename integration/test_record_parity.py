"""RecordBone parity — nested (non-table) models vs. the REAL RecordBone.

Records are plain pydantic nesting; this pins that the structure entry
(``type: record`` + ``using``) and the dump shape (the plain values dict)
match the real bone. ``sortindex`` is excluded at both levels.
"""
import json

from skeletons import RecordRefSkel

from sqlmodel import SQLModel

from viur.models import Field, Model


class Address(SQLModel):
    street: str = Field(descr="Straße", max_length=100)
    zip_code: int | None = Field(default=None, descr="PLZ")


class RecordParityModel(Model):
    address: Address | None = Field(default=None, descr="Adresse", format="$(street)")
    stops: list[Address] = Field(
        default_factory=list, required=False, descr="Stationen", format="$(street)",
    )


IGNORED = {"sortindex", "tags", "mode", "decimal"}


def _normalize(entry: dict) -> dict:
    plain = json.loads(json.dumps(entry, default=lambda o: getattr(o, "value", str(o))))
    out = {k: v for k, v in plain.items() if k not in IGNORED}
    if isinstance(out.get("using"), dict):
        out["using"] = {
            name: {k: v for k, v in bone.items() if k not in IGNORED}
            for name, bone in out["using"].items()
        }
    return out


def test_record_structures_match():
    skel_structure = RecordRefSkel().structure()
    model_structure = RecordParityModel.viur_structure()
    for field in ("address", "stops"):
        ours = _normalize(model_structure[field])
        theirs = _normalize(skel_structure[field])
        assert ours == theirs, (
            f"structure drift on {field!r}:\n  model: {ours}\n  skel:  {theirs}"
        )


def test_record_dumps_match():
    skel = RecordRefSkel()
    assert skel.fromClient({"address.street": "Hauptweg 1", "address.zip_code": "12345"})
    skel_dump = json.loads(json.dumps(skel.dump()["address"], default=str))

    model, errors = RecordParityModel.viur_from_client(
        {"address.street": "Hauptweg 1", "address.zip_code": "12345"},
    )
    assert errors == []
    assert model.viur_dump()["address"] == skel_dump