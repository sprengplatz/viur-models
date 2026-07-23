"""Field types that carry their bone type in the annotation.

The bone type of a field is decided by its **Python type**, never by a
string parameter: ``str`` maps to ``"str"``, ``Email`` to ``"str.email"``,
``Text`` to ``"text"``, :class:`pydantic_extra_types.country.CountryAlpha2`
to ``"select.country"``. Where the pydantic ecosystem already has a
semantic type, viur-models maps it (see :data:`BONE_TYPE_REGISTRY`); where
none exists, a dedicated type is one ``Annotated`` alias away:

    Slug = t.Annotated[str, BoneType("str.slug")]

pydantic keeps unknown ``Annotated`` metadata objects in
``FieldInfo.metadata``, where the structure mapping picks the marker up;
validation and the SQL column type stay those of the underlying type.
"""
import dataclasses
import typing as t

import pydantic
from pydantic_extra_types.color import Color as PydanticColor
from pydantic_extra_types.country import CountryAlpha2


@dataclasses.dataclass(frozen=True)
class BoneType:
    """Annotation marker that sets the bone type of a field.

    :param name: The ``type`` string emitted in the structure
        (e.g. ``"str.email"``).
    :param extras: Additional structure keys for this bone type.
    :param replace: If ``True``, the base type's structure extras are
        dropped entirely (``emptyvalue``/``extras`` define the bone alone) —
        for bone types that are not a refinement of their Python type.
    :param emptyvalue: The ``emptyvalue`` to emit; only used with ``replace``.
    :param write_only: The value never appears in dumps (``viur_dump`` emits
        the ``emptyvalue`` instead) — for secrets like ``Password`` /
        ``Credential``, mirroring viur-core's read behavior.
    """

    name: str
    extras: dict | None = None
    replace: bool = False
    emptyvalue: t.Any = None
    write_only: bool = False


@dataclasses.dataclass(frozen=True)
class LanguageWrapper:
    """Annotation marker produced by ``Language[X]`` — carries the inner
    (per-language) value type."""

    inner: t.Any


class Language:
    """Wrapper type for multilingual fields — the type describes the data
    structure, like ``list[X]`` does::

        title: Language[str] = ViURField(languages=("de", "en"), sa_type=JSON, default=None)
        body: Language[Text] | None = ViURField(default=None, sa_type=JSON)

    The value is a ``{lang: value}`` dict (JSON column — pass
    ``sa_type=JSON``); the bone structure carries the inner type's shape
    plus the ``languages`` list (``StringBone(languages=…)`` analogue).
    The language list comes from ``ViURField(languages=…)`` or, when
    omitted, from :func:`set_default_languages`.
    """

    def __class_getitem__(cls, inner: t.Any) -> t.Any:
        return t.Annotated[dict[str, str | None], LanguageWrapper(inner)]


#: Fallback language list for ``Language[X]`` fields without an explicit
#: ``ViURField(languages=…)`` — set once at app boot.
DEFAULT_LANGUAGES: tuple[str, ...] | None = None


def set_default_languages(*codes: str) -> None:
    """Set the project-wide default language list (e.g. at app boot,
    mirroring ``conf.i18n.available_languages``)."""
    global DEFAULT_LANGUAGES
    DEFAULT_LANGUAGES = tuple(codes) or None


Text = t.Annotated[str, BoneType("text", extras={"valid_html": None}, replace=True, emptyvalue="")]
"""Multiline/rich-text field — emits ``TextBone``'s ``"text"`` structure."""

Email = t.Annotated[str, BoneType("str.email")]
"""E-mail field — emits ``EmailBone``'s ``"str.email"`` with ``str`` extras."""

Raw = t.Annotated[str, BoneType("raw", replace=True)]
"""Unprocessed string — emits ``RawBone``'s ``"raw"`` structure."""

Code = t.Annotated[str, BoneType("raw.code", replace=True, extras={"indexed": False})]
"""Source-code field — ``CodeBone``'s ``"raw.code"`` (JinjaBone/LogicsBone/
PythonBone share the type string; alias further names as needed). Like
``CodeBone``, not indexed."""

Color = t.Annotated[str, BoneType("color", replace=True)]
"""Color value — emits ``ColorBone``'s ``"color"`` structure."""

#: ``PhoneBone``'s validation regex, pinned against viur-core 3.9.
PHONE_TEST_PATTERN = r"^\+?(\d{1,3})[-\s]?(\d{1,4})[-\s]?(\d{1,4})[-\s]?(\d{1,9})$"

_PHONE_EXTRAS = {"test": PHONE_TEST_PATTERN, "default_country_code": None}
_URI_EXTRAS = {
    "accepted_protocols": None,
    "accepted_ports": None,
    "clean_get_params": False,
    "domain_allowed_list": None,
    "domain_disallowed_list": None,
    "local_path_allowed": False,
}

Phone = t.Annotated[str, BoneType("str.phone", extras=dict(_PHONE_EXTRAS))]
"""Phone number — ``PhoneBone``'s ``"str.phone"`` with its client-side test
regex. Pair with ``max_length=15`` for full structure parity. (For real
number validation use pydantic-extra-types' ``PhoneNumber`` — registered
below when the optional ``phonenumbers`` package is installed.)"""

Uri = t.Annotated[str, BoneType("uri", replace=True, extras=dict(_URI_EXTRAS))]
"""URI/URL field — emits ``UriBone``'s ``"uri"`` structure (default hints).
(For real URL validation use pydantic's ``AnyUrl``/``HttpUrl`` — registered
below; those need ``sa_type=String`` as they are not ``str`` subclasses.)"""

Uid = t.Annotated[str, BoneType(
    "uid",
    replace=True,
    extras={
        "fillchar": "*",
        "length": 13,
        "pattern": "*",
        # UidBone semantics: server-generated once, locked unique, kept on clone.
        "readonly": True,
        "unique": 1,  # UniqueLockMethod.SameValue
        "clone_behavior": {"strategy": "set_default"},
        "compute": {"method": "Once"},
    },
)]
"""Unique-id string — ``UidBone``'s ``"uid"`` structure (readonly, unique).
Server-side generation is the module's job (v1 does not auto-fill it)."""

SortIndex = t.Annotated[float, BoneType(
    "numeric.sortindex",
    # like SortIndexBone: a fresh index on clone, not a copy
    extras={"clone_behavior": {"strategy": "set_default"}},
)]
"""Sort-index number — ``SortIndexBone``'s ``"numeric.sortindex"`` with the
float numeric extras (precision 8, ``decimal: false``)."""

Json = t.Annotated[dict, BoneType(
    "raw.json",
    replace=True,
    extras={"schema": {}, "indexed": False},  # like JsonBone: not indexed
)]
"""Free-form JSON — ``JsonBone``'s ``"raw.json"``. The column type needs an
explicit ``sa_type`` (e.g. ``ViURField(default_factory=dict,
sa_type=sqlalchemy.JSON)``) — SQLModel cannot map ``dict`` on its own."""

Credential = t.Annotated[str, BoneType(
    "str.credential",
    extras={"maxlength": None, "minlength": None},
    write_only=True,
)]
"""Credential/secret string — ``CredentialBone``'s ``"str.credential"``.
Write-only: the stored value never appears in dumps (reads emit ``""``)."""

#: ``PasswordBone``'s complexity checks, pinned against viur-core 3.9
#: (regex, client-facing message, blocking).
PASSWORD_TESTS = [
    ["^.*[A-Z].*$", "The password entered has no capital letters.", False],
    ["^.*[a-z].*$", "The password entered has no lowercase letters.", False],
    ["^.*\\d.*$", "The password entered has no digits.", False],
    ["^.*\\W.*$", "The password entered has no special characters.", False],
    ["^.{8,}$", "The password is too short. It requires for at least 8 characters.", True],
]

Password = t.Annotated[str, BoneType(
    "password",
    replace=True,
    emptyvalue="",
    write_only=True,
    extras={
        "maxlength": 254,
        "minlength": None,
        "tests": PASSWORD_TESTS,
        "test_threshold": 4,
    },
)]
"""Password field — ``PasswordBone``'s ``"password"`` structure, write-only.

.. warning::
    **Hashing is not automatic.** Hash the incoming value in your module's
    ``onAdd``/``onEdit`` hooks (viur-core uses PBKDF2) — viur-models only
    guarantees the value never leaves the model in dumps.
"""


def Spatial(
    bounds_lat: tuple[float, float], bounds_lng: tuple[float, float],
) -> t.Any:
    """Build a spatial field type — ``SpatialBone``'s ``"spatial"``.

    Bounds are per-field, so this is a factory instead of a fixed alias::

        Position = Spatial(bounds_lat=(46.0, 56.0), bounds_lng=(4.0, 17.0))
        pos: Position | None = ViURField(default=None, sa_type=JSON)

    Values are ``(lat, lng)`` pairs (dumped as a JSON list, like the real
    bone); client input is accepted dotted (``pos.lat=…&pos.lng=…``) and as
    a two-element list.
    """
    return t.Annotated[tuple[float, float], BoneType(
        "spatial",
        replace=True,
        emptyvalue=[0.0, 0.0],
        extras={"boundslat": list(bounds_lat), "boundslng": list(bounds_lng)},
    )]


BONE_TYPE_REGISTRY: dict[type, BoneType] = {}
"""Python types (e.g. from pydantic-extra-types) mapped to bone types.

The structure mapping walks a field type's MRO against this registry, so
subclasses inherit their base type's bone mapping. An ``Annotated``
:class:`BoneType` marker on the field always wins over the registry.
"""


def register_bone_type(python_type: type, marker: BoneType) -> None:
    """Map a Python type to a bone type for the structure emission."""
    BONE_TYPE_REGISTRY[python_type] = marker


def _country_values() -> dict[str, str]:
    import pycountry

    return {country.alpha_2: country.name for country in pycountry.countries}


Country = CountryAlpha2
"""ISO-3166 alpha-2 country field — emits ``SelectCountryBone``'s
``"select.country"``; validation comes from pydantic-extra-types."""

register_bone_type(
    CountryAlpha2,
    BoneType("select.country", replace=True, extras={"values": _country_values()}),
)


# --------------------------------------------------------------------------- #
# pydantic ecosystem registrations                                            #
# --------------------------------------------------------------------------- #
# Where pydantic already ships a semantic type, it maps to the matching bone
# type via the registry — the validated type IS the declaration, no custom
# alias needed. Constrained types (``constr``/``conint``/``condecimal``) need
# no registration at all: their constraints land in ``FieldInfo.metadata``
# and feed the regular derivation (``maxlength``, ``min``/``max``, …).
#
# NOTE: ``AnyUrl``/``HttpUrl`` and ``Color`` are not ``str`` subclasses —
# such columns need an explicit ``sa_type`` (e.g. ``sqlalchemy.String``);
# dumps stringify them.

# EmailStr is no ``str`` subclass in pydantic ≥2.13 — the marker carries the
# full str-shape (like EmailBone: maxlength 254).
register_bone_type(pydantic.EmailStr, BoneType(
    "str.email", replace=True, emptyvalue="",
    extras={"maxlength": 254, "minlength": None},
))
register_bone_type(pydantic.AnyUrl, BoneType("uri", replace=True, extras=dict(_URI_EXTRAS)))
register_bone_type(PydanticColor, BoneType("color", replace=True))

try:
    from pydantic_extra_types.phone_numbers import PhoneNumber as PydanticPhoneNumber
except (ImportError, RuntimeError):  # pragma: no cover — optional ``phonenumbers``
    PydanticPhoneNumber = None
else:  # pragma: no cover — exercised only with ``phonenumbers`` installed
    register_bone_type(
        PydanticPhoneNumber, BoneType("str.phone", extras=dict(_PHONE_EXTRAS)),
    )
