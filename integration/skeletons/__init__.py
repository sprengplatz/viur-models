"""Test skeletons for the integration suite.

They live in a ``skeletons/`` folder so viur-core's ``skeleton_search_path``
accepts them (the conftest points ``project_base_path`` at the integration
directory, so this resolves under the default ``/skeletons/`` entry).

``ParityRefSkel`` is the skeleton twin of ``ParityModel`` in
``test_structure_parity.py`` — same fields, defined once as bones and once
as a Model. The parity test asserts both emit the same structure.
"""
from viur.core import bones
from viur.core.bones import (
    BooleanBone,
    ColorBone,
    CredentialBone,
    DateBone,
    EmailBone,
    JsonBone,
    NumericBone,
    PasswordBone,
    PhoneBone,
    RawBone,
    RelationalBone,
    SelectBone,
    SortIndexBone,
    SpatialBone,
    StringBone,
    UidBone,
    UriBone,
)
from viur.core.bones import RecordBone
from viur.core.bones.base import MultipleConstraints
from viur.core.skeleton import RelSkel, Skeleton


class ParityRefSkel(Skeleton):
    kindName = "viur_models_test_parity"

    name = StringBone(descr="Name", required=True, max_length=100)
    mail = EmailBone(descr="Mail")
    rating = NumericBone(descr="Rating", min=1, max=5)
    active = BooleanBone(descr="Active")
    due = DateBone(descr="Due")
    state = SelectBone(descr="State", values={"new": "New", "done": "Done"})


class TypesRefSkel(Skeleton):
    """Skeleton twin of ``TypesParityModel`` in ``test_types_parity.py`` —
    one bone per built-in field type of viur.models.types."""
    kindName = "viur_models_test_types"

    raw = RawBone(descr="Raw")
    if hasattr(bones, "CodeBone"):
        code = bones.CodeBone(descr="Code")
    color = ColorBone(descr="Color")
    phone = PhoneBone(descr="Phone")
    uri = UriBone(descr="Uri")
    uid = UidBone(descr="Uid")
    sortindex = SortIndexBone(descr="Sortindex")
    data = JsonBone(descr="Data")
    secret = CredentialBone(descr="Secret")


class RelRefSkel(Skeleton):
    """Skeleton twin of ``RelParityModel`` in ``test_relation_parity.py`` —
    one RelationalBone pointing at ``ParityRefSkel``'s kind. ``module`` is
    set to the kind, matching viur-models' derivation."""
    kindName = "viur_models_test_relref"

    author = RelationalBone(
        descr="Autor",
        kind="viur_models_test_parity",
        module="viur_models_test_parity",
    )
    fans = RelationalBone(
        descr="Fans",
        kind="viur_models_test_parity",
        module="viur_models_test_parity",
        multiple=True,
    )
    crew = RelationalBone(
        descr="Crew",
        kind="viur_models_test_parity",
        module="viur_models_test_parity",
        multiple=MultipleConstraints(min=1, max=2, duplicates=False),
    )


class AddressUsing(RelSkel):
    """Using-skel twin of the ``Address`` record model in
    ``test_record_parity.py``."""
    street = StringBone(descr="Straße", required=True, max_length=100)
    zip_code = NumericBone(descr="PLZ")


class RecordRefSkel(Skeleton):
    kindName = "viur_models_test_record"

    address = RecordBone(descr="Adresse", using=AddressUsing, format="$(street)")
    stops = RecordBone(
        descr="Stationen", using=AddressUsing, format="$(street)", multiple=True,
    )


class WeightUsing(RelSkel):
    """Using-skel twin of the ``EntryTagLink`` payload in
    ``test_using_parity.py``."""
    weight = NumericBone(descr="Gewichtung", min=0, max=10)


class UsingRefSkel(Skeleton):
    kindName = "viur_models_test_usingref"

    crew = RelationalBone(
        descr="Crew",
        kind="viur_models_test_parity",
        module="viur_models_test_parity",
        multiple=True,
        using=WeightUsing,
    )


class V2RefSkel(Skeleton):
    """Skeleton twin of ``V2ParityModel`` in ``test_v2_parity.py`` —
    languages, spatial and password bones."""
    kindName = "viur_models_test_v2"

    title = StringBone(descr="Titel", languages=["de", "en"])
    pos = SpatialBone(
        descr="Position",
        boundsLat=(46.0, 56.0), boundsLng=(4.0, 17.0), gridDimensions=(10, 10),
    )
    pwd = PasswordBone(descr="Passwort")
