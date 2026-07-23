"""v2 features vs. the REAL bones — languages, spatial, password,
multiple constraints.

``sortindex`` is excluded as usual; password parity covers the full
structure including the pinned complexity ``tests``.
"""
import json

from skeletons import RelRefSkel, V2RefSkel

from sqlalchemy import JSON
from sqlmodel import Field, Relationship, SQLModel

from viur.models import Language, Password, Spatial, ViURField, ViURModel

from test_relation_parity import RelTarget

Position = Spatial(bounds_lat=(46.0, 56.0), bounds_lng=(4.0, 17.0))


class V2ParityModel(ViURModel):
    title: Language[str] | None = ViURField(
        default=None, languages=("de", "en"), sa_type=JSON, descr="Titel",
    )
    pos: Position | None = ViURField(default=None, sa_type=JSON, descr="Position")
    pwd: Password | None = ViURField(default=None, descr="Passwort")


class CrewLink(SQLModel, table=True):
    __tablename__ = "viur_models_test_crewparity_link"
    parent_id: int | None = Field(
        default=None, foreign_key="viur_models_test_crewparity.id", primary_key=True,
    )
    target_id: int | None = Field(
        default=None, foreign_key="viur_models_test_parity.id", primary_key=True,
    )


class CrewParityModel(ViURModel, table=True):
    __tablename__ = "viur_models_test_crewparity"

    viur_relation_meta = {
        "crew": {"descr": "Crew", "multiple": {"min": 1, "max": 2, "duplicates": False}},
    }

    crew: list[RelTarget] = Relationship(link_model=CrewLink)


IGNORED = {"sortindex"}


def _normalize(entry: dict) -> dict:
    plain = json.loads(json.dumps(entry, default=lambda o: getattr(o, "value", str(o))))
    out = {k: v for k, v in plain.items() if k not in IGNORED}
    if isinstance(out.get("relskel"), dict):
        out["relskel"] = {
            name: {k: v for k, v in bone.items() if k not in IGNORED}
            for name, bone in out["relskel"].items()
        }
    return out


def test_language_spatial_password_structures_match():
    V2RefSkel.setSystemInitialized()
    skel_structure = V2RefSkel().structure()
    model_structure = V2ParityModel.viur_structure()
    for field in ("title", "pos", "pwd"):
        ours, theirs = _normalize(model_structure[field]), _normalize(skel_structure[field])
        assert ours == theirs, (
            f"structure drift on {field!r}:\n  model: {ours}\n  skel:  {theirs}"
        )


def test_language_and_spatial_dumps_match():
    skel = V2RefSkel()
    skel["title"] = {"de": "Hallo", "en": "Hello"}
    skel["pos"] = (48.1, 11.5)
    skel_dump = json.loads(json.dumps(skel.dump(), default=str))

    model = V2ParityModel(title={"de": "Hallo", "en": "Hello"}, pos=(48.1, 11.5))
    model_dump = json.loads(json.dumps(model.viur_dump(), default=str))

    assert model_dump["title"] == skel_dump["title"]
    assert model_dump["pos"] == skel_dump["pos"]


def test_multiple_constraints_structure_matches():
    RelRefSkel.setSystemInitialized()
    theirs = _normalize(RelRefSkel().structure()["crew"])
    ours = _normalize(CrewParityModel.viur_structure()["crew"])
    assert ours == theirs, f"drift:\n  model: {ours}\n  skel:  {theirs}"