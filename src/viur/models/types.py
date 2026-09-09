"""Field types carrying their bone type: ``Annotated[T, BoneType(...)]`` aliases,
``BONE_TYPE_REGISTRY`` for foreign types."""
import dataclasses
import typing as t

import pydantic
from pydantic_extra_types.color import Color as PydanticColor
from pydantic_extra_types.country import CountryAlpha2


@dataclasses.dataclass(frozen=True)
class BoneType:
    """Annotation marker setting a field's bone type.

    :param name: Structure ``type`` string.
    :param extras: Additional structure keys.
    :param replace: Drop the Python type's structure; ``emptyvalue``/``extras`` define the bone.
    :param emptyvalue: Emitted ``emptyvalue`` (with ``replace``).
    :param write_only: Dumps emit the ``emptyvalue`` instead of the value.
    """

    name: str
    extras: dict | None = None
    replace: bool = False
    emptyvalue: t.Any = None
    write_only: bool = False


@dataclasses.dataclass(frozen=True)
class LanguageWrapper:
    """Marker produced by ``Language[X]``; ``inner`` is the per-language type."""

    inner: t.Any


class Language:
    """``Language[X]``: ``{lang: value}`` dict in a JSON column; languages from
    ``Field(languages=…)`` or ``set_default_languages``."""

    def __class_getitem__(cls, inner: t.Any) -> t.Any:
        return t.Annotated[dict[str, str | None], LanguageWrapper(inner)]


#: Fallback for ``Language[X]`` fields without ``Field(languages=…)``.
DEFAULT_LANGUAGES: tuple[str, ...] | None = None


def set_default_languages(*codes: str) -> None:
    """Project-wide default language list."""
    global DEFAULT_LANGUAGES
    DEFAULT_LANGUAGES = tuple(codes) or None


Text = t.Annotated[str, BoneType("text", extras={"valid_html": None}, replace=True, emptyvalue="")]
"""``TextBone`` (``"text"``)."""

Email = t.Annotated[str, BoneType("str.email")]
"""``EmailBone`` (``"str.email"``)."""

Raw = t.Annotated[str, BoneType("raw", replace=True)]
"""``RawBone`` (``"raw"``)."""

Code = t.Annotated[str, BoneType("raw.code", replace=True, extras={"indexed": False})]
"""``CodeBone`` (``"raw.code"``), not indexed."""

Color = t.Annotated[str, BoneType("color", replace=True)]
"""``ColorBone`` (``"color"``)."""

#: ``PhoneBone`` regex, viur-core 3.9.
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
"""``PhoneBone`` (``"str.phone"``); ``max_length=15`` for parity. Real validation: pydantic ``PhoneNumber``."""

Uri = t.Annotated[str, BoneType("uri", replace=True, extras=dict(_URI_EXTRAS))]
"""``UriBone`` (``"uri"``), default hints. Real validation: pydantic ``AnyUrl`` with ``sa_type=String``."""

Uid = t.Annotated[str, BoneType(
    "uid",
    replace=True,
    extras={
        "fillchar": "*",
        "length": 13,
        "pattern": "*",
        "readonly": True,
        "unique": 1,  # UniqueLockMethod.SameValue
        "clone_behavior": {"strategy": "set_default"},
        "compute": {"method": "Once"},
    },
)]
"""``UidBone`` (``"uid"``): readonly, unique. The module supplies the value."""

SortIndex = t.Annotated[float, BoneType(
    "numeric.sortindex",
    extras={"clone_behavior": {"strategy": "set_default"}},
)]
"""``SortIndexBone`` (``"numeric.sortindex"``)."""

Json = t.Annotated[dict, BoneType(
    "raw.json",
    replace=True,
    extras={"schema": {}, "indexed": False},
)]
"""``JsonBone`` (``"raw.json"``); needs an explicit ``sa_type``."""

Credential = t.Annotated[str, BoneType(
    "str.credential",
    extras={"maxlength": None, "minlength": None},
    write_only=True,
)]
"""``CredentialBone`` (``"str.credential"``), write-only."""

#: ``PasswordBone`` tests (regex, message, blocking), viur-core 3.9.
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
"""``PasswordBone`` (``"password"``), write-only. Hashing is NOT automatic — do it in ``onAdd``/``onEdit``."""


def Spatial(
    bounds_lat: tuple[float, float], bounds_lng: tuple[float, float],
) -> t.Any:
    """``SpatialBone`` (``"spatial"``) type factory; values are ``(lat, lng)``, stored as JSON."""
    return t.Annotated[tuple[float, float], BoneType(
        "spatial",
        replace=True,
        emptyvalue=[0.0, 0.0],
        extras={"boundslat": list(bounds_lat), "boundslng": list(bounds_lng)},
    )]


BONE_TYPE_REGISTRY: dict[type, BoneType] = {}
"""Python type → ``BoneType``, matched along the MRO; an ``Annotated`` marker wins."""


def register_bone_type(python_type: type, marker: BoneType) -> None:
    """Map a Python type to a bone type."""
    BONE_TYPE_REGISTRY[python_type] = marker


def _country_values() -> dict[str, str]:
    import pycountry

    return {country.alpha_2: country.name for country in pycountry.countries}


Country = CountryAlpha2
"""``SelectCountryBone`` (``"select.country"``), validated by pydantic-extra-types."""

register_bone_type(
    CountryAlpha2,
    BoneType("select.country", replace=True, extras={"values": _country_values()}),
)


# pydantic ecosystem types. Constrained types (constr/conint/…) need no entry —
# their constraints sit in FieldInfo.metadata. AnyUrl/Color are no str
# subclasses: explicit sa_type, dumps stringify.
register_bone_type(pydantic.EmailStr, BoneType(
    "str.email", replace=True, emptyvalue="",
    extras={"maxlength": 254, "minlength": None},
))
register_bone_type(pydantic.AnyUrl, BoneType("uri", replace=True, extras=dict(_URI_EXTRAS)))
register_bone_type(PydanticColor, BoneType("color", replace=True))

try:
    from pydantic_extra_types.phone_numbers import PhoneNumber as PydanticPhoneNumber
except (ImportError, RuntimeError):  # pragma: no cover
    PydanticPhoneNumber = None
else:  # pragma: no cover
    register_bone_type(
        PydanticPhoneNumber, BoneType("str.phone", extras=dict(_PHONE_EXTRAS)),
    )
