"""``ViURField`` — a thin wrapper around ``sqlmodel.Field()``.

Carries the ViUR bone parameters (``descr``, ``visible``, ``params``, …) as
metadata in ``FieldInfo.json_schema_extra["viur"]`` and passes everything
else unchanged to SQLModel. Models defined with it stay plain SQLModels —
Alembic, FastAPI and raw SQLAlchemy keep working; the ViUR semantics are
pure annotation, read later by :mod:`viur.models.structure`.

SQLModel ≥ 0.0.39 exposes extra pydantic ``FieldInfo`` kwargs only through
``schema_extra``, which is spread as ``**kwargs`` into the FieldInfo
constructor. Unknown keys would vanish silently on pydantic's
deprecated-extra path, so this wrapper validates them against a whitelist.
"""
import typing as t

from pydantic_core import PydanticUndefined
from sqlmodel import Field

VIUR_META_KEY = "viur"
"""Key under which the bone metadata lives in ``FieldInfo.json_schema_extra``."""

ALLOWED_SCHEMA_EXTRA = frozenset({
    # pydantic FieldInfo kwargs that sqlmodel.Field() does not expose
    # as explicit parameters (SQLModel 0.0.39 / pydantic 2.13):
    "json_schema_extra",
    "pattern",
    "strict",
    "frozen",
    "deprecated",
    "examples",
    "validate_default",
    "union_mode",
    "fail_fast",
    "allow_inf_nan",
    "coerce_numbers_to_str",
    "field_title_generator",
    "exclude_if",
    "alias_priority",
    "init",
    "init_var",
    "kw_only",
    # special-cased by sqlmodel.Field() itself:
    "validation_alias",
    "serialization_alias",
})


def ViURField(
    default: t.Any = PydanticUndefined,
    *,
    descr: str | None = None,
    required: bool | None = None,
    visible: bool = True,
    readonly: bool = False,
    params: dict | None = None,
    values: dict | None = None,
    compute: dict | None = None,
    languages: t.Sequence[str] | None = None,
    format: str | None = None,
    schema_extra: dict | None = None,
    **kwargs: t.Any,
) -> t.Any:
    """Define a model field with ViUR bone metadata.

    Constraints that pydantic/SQL already express (``max_length``, ``ge``/``le``,
    ``unique``, ``index``, nullability, defaults) are **not** duplicated here —
    the structure mapping derives them from the ``FieldInfo``. This wrapper
    only carries what SQL/pydantic cannot express:

    :param descr: Display name; defaults to the title-cased field name.
    :param required: Override; defaults to the pydantic derivation
        (non-``Optional`` type without default).
    :param visible: Whether the field appears in client UIs.
    :param readonly: Renders the field read-only (also forces
        ``required: false`` in the structure, mirroring ``BaseBone``).
    :param params: Free-form client hints (``BaseBone.params``).
    :param values: ``{value: label}`` overrides for select fields
        (``enum.Enum`` / ``typing.Literal`` annotations).
    :param compute: ``BaseBone.compute`` structure info (e.g.
        ``{"method": "Once"}``) — emitted verbatim; used by the system
        fields, full compute semantics are v2 (analysis/01 §9).
    :param languages: Language codes for a multilingual string field
        (``StringBone(languages=…)`` analogue). The field must be annotated
        ``dict[str, str] | None`` and stored as a JSON column
        (``sa_type=JSON``); values dump as ``{lang: value}`` and client
        input is accepted dotted (``name.de=…``) and as a dict.
    :param format: Display-format hint for record and relational bones
        (e.g. ``"$(street)"``) — emitted verbatim in the structure.
    :param schema_extra: Extra pydantic ``FieldInfo`` kwargs (whitelisted,
        see :data:`ALLOWED_SCHEMA_EXTRA`).
    :param kwargs: Passed to :func:`sqlmodel.Field` unchanged
        (``primary_key``, ``foreign_key``, ``max_length``, ``ge``/``le``,
        ``sa_column``, ``default_factory``, …).
    """
    viur_meta = {
        key: value
        for key, value in {
            "descr": descr,
            "required": required,
            "visible": visible,
            "readonly": readonly,
            "params": params,
            "values": values,
            "compute": compute,
            "languages": list(languages) if languages else None,
            "format": format,
        }.items()
        if value is not None
    }

    schema_extra = dict(schema_extra or {})
    if unknown := set(schema_extra) - ALLOWED_SCHEMA_EXTRA:
        raise TypeError(
            f"Unknown schema_extra key(s) {sorted(unknown)} — pydantic would "
            f"silently drop them. Allowed: {sorted(ALLOWED_SCHEMA_EXTRA)}"
        )
    schema_extra["json_schema_extra"] = {
        **(schema_extra.get("json_schema_extra") or {}),
        VIUR_META_KEY: viur_meta,
    }

    return Field(default, schema_extra=schema_extra, **kwargs)
