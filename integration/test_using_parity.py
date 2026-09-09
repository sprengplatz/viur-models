"""Using-relation parity — association-object edge payload vs. the REAL
``RelationalBone(using=RelSkel)``.

The link table's payload columns are the using-skel; this pins that the
emitted ``using`` structure matches the real bone's. ``sortindex`` is
excluded at every level.
"""
import json

from skeletons import UsingRefSkel

from sqlmodel import Field, Relationship

from viur.models import RelationLink, Field, Model

from test_relation_parity import RelTarget


class CrewLink(RelationLink, table=True):
    __tablename__ = "viur_models_test_usingcrew_link"
    parent_id: int | None = Field(
        default=None, foreign_key="viur_models_test_usingcrew.id", primary_key=True,
    )
    target_id: int | None = Field(
        default=None, foreign_key="viur_models_test_parity.id", primary_key=True,
    )
    target: RelTarget = Relationship()
    weight: int | None = Field(default=None, ge=0, le=10, descr="Gewichtung")


class UsingParityModel(Model, table=True):
    __tablename__ = "viur_models_test_usingcrew"

    viur_relation_meta = {"crew": {"descr": "Crew"}}

    crew: list[CrewLink] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


IGNORED = {"sortindex"}


def _normalize(entry: dict) -> dict:
    plain = json.loads(json.dumps(entry, default=lambda o: getattr(o, "value", str(o))))
    out = {k: v for k, v in plain.items() if k not in IGNORED}
    for nested in ("relskel", "using"):
        if isinstance(out.get(nested), dict):
            out[nested] = {
                name: {k: v for k, v in bone.items() if k not in IGNORED}
                for name, bone in out[nested].items()
            }
    return out


def test_using_relation_structure_matches():
    UsingRefSkel.setSystemInitialized()
    theirs = _normalize(UsingRefSkel().structure()["crew"])
    ours = _normalize(UsingParityModel.viur_structure()["crew"])

    assert set(ours) == set(theirs), (
        f"key-set drift:\n  only model: {set(ours) - set(theirs)}"
        f"\n  only skel:  {set(theirs) - set(ours)}"
    )
    for key in sorted(theirs):
        assert ours[key] == theirs[key], (
            f"drift on {key!r}:\n  model: {ours[key]}\n  skel:  {theirs[key]}"
        )