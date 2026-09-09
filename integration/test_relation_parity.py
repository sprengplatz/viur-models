"""Relational structure parity — Model FK relations vs. the REAL
``RelationalBone``.

The model twin's target table carries the same kind string as the
skeleton twin's ``kind``, so the ``relational.<kind>`` type, ``module``
and ``relskel`` shapes are directly comparable. ``sortindex`` is excluded
at both levels (positions differ by design, see ``test_structure_parity``).
"""
import json

from skeletons import RelRefSkel

from sqlmodel import Relationship, SQLModel

from viur.models import Field, Model


class RelTarget(Model, table=True):
    __tablename__ = "viur_models_test_parity"  # same kind as ParityRefSkel
    name: str = Field(descr="Name", max_length=100)


class RelParityFanLink(SQLModel, table=True):
    __tablename__ = "viur_models_test_relparity_fans"
    parent_id: int | None = Field(
        default=None, foreign_key="viur_models_test_relparity.id", primary_key=True,
    )
    target_id: int | None = Field(
        default=None, foreign_key="viur_models_test_parity.id", primary_key=True,
    )


class RelParityModel(Model, table=True):
    __tablename__ = "viur_models_test_relparity"

    viur_relation_meta = {"fans": {"descr": "Fans"}}

    author_id: int | None = Field(
        default=None, foreign_key="viur_models_test_parity.id", descr="Autor",
    )
    author: RelTarget | None = Relationship()
    fans: list[RelTarget] = Relationship(link_model=RelParityFanLink)


IGNORED = {"sortindex"}


def _normalize(bone: dict) -> dict:
    plain = json.loads(json.dumps(bone, default=lambda o: getattr(o, "value", str(o))))
    out = {k: v for k, v in plain.items() if k not in IGNORED}
    if isinstance(out.get("relskel"), dict):
        out["relskel"] = {
            name: {k: v for k, v in entry.items() if k not in IGNORED}
            for name, entry in out["relskel"].items()
        }
    return out


import pytest


@pytest.mark.parametrize("bone_name", ["author", "fans"])
def test_relational_bone_structure_matches(bone_name):
    # Build the bone's refSkel cache — viur-core does this at boot via
    # setSystemInitialized(); this suite runs without setup().
    RelRefSkel.setSystemInitialized()
    skel_bone = _normalize(RelRefSkel().structure()[bone_name])
    model_bone = _normalize(RelParityModel.viur_structure()[bone_name])

    assert set(model_bone) == set(skel_bone), (
        f"key-set drift:\n  only model: {set(model_bone) - set(skel_bone)}"
        f"\n  only skel:  {set(skel_bone) - set(model_bone)}"
    )
    for key in sorted(skel_bone):
        assert model_bone[key] == skel_bone[key], (
            f"drift on {key!r}:\n  model: {model_bone[key]}\n  skel:  {skel_bone[key]}"
        )