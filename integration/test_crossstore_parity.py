"""Cross-store structure parity — ``SkeletonRef`` vs. the REAL
``RelationalBone``, with the relskel resolved through the real skeleton
registry (``RefSkel.fromSkel``).

The dest-snapshot read (``read_dest``) needs a live datastore and is
unit-tested against a patched reader; here the structure side is pinned.
"""
import json

from skeletons import RelRefSkel

from sqlalchemy import JSON

from viur.models import SkeletonRef, Field, Model


class XStoreParityModel(Model):
    author: SkeletonRef("viur_models_test_parity", ref_keys=("name",)) | None = Field(
        default=None, sa_type=JSON, descr="Autor",
    )


IGNORED = {"sortindex", "tags", "mode", "decimal"}


def _normalize(entry: dict) -> dict:
    plain = json.loads(json.dumps(entry, default=lambda o: getattr(o, "value", str(o))))
    out = {k: v for k, v in plain.items() if k not in IGNORED}
    if isinstance(out.get("relskel"), dict):
        out["relskel"] = {
            name: {k: v for k, v in bone.items() if k not in IGNORED}
            for name, bone in out["relskel"].items()
        }
    return out


def test_crossstore_structure_matches_the_real_relational_bone():
    RelRefSkel.setSystemInitialized()
    theirs = _normalize(RelRefSkel().structure()["author"])
    ours = _normalize(XStoreParityModel.viur_structure()["author"])

    assert set(ours) == set(theirs), (
        f"key-set drift:\n  only model: {set(ours) - set(theirs)}"
        f"\n  only skel:  {set(theirs) - set(ours)}"
    )
    for key in sorted(theirs):
        assert ours[key] == theirs[key], (
            f"drift on {key!r}:\n  model: {ours[key]}\n  skel:  {theirs[key]}"
        )