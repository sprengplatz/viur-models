"""``Field`` — ``sqlmodel.Field()`` with the bone metadata in ``json_schema_extra["viur"]``."""
import typing as t

from pydantic_core import PydanticUndefined
from sqlmodel import Field as SQLModelField

VIUR_META_KEY = "viur"
"""Key of the bone metadata in ``FieldInfo.json_schema_extra``."""

ALLOWED_SCHEMA_EXTRA = frozenset({
    # FieldInfo kwargs without a sqlmodel.Field() parameter (SQLModel 0.0.39 / pydantic 2.13)
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
    # special-cased by sqlmodel.Field()
    "validation_alias",
    "serialization_alias",
})


def Field(
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
    """``sqlmodel.Field()`` plus bone metadata. Constraints pydantic/SQL express
    (``max_length``, ``ge``/``le``, nullability, defaults) are derived, not repeated.

    :param descr: Display name (default: title-cased field name).
    :param required: Override of the pydantic derivation.
    :param visible: Shown in client UIs.
    :param readonly: Read-only bone; forces ``required: false``.
    :param params: ``BaseBone.params``.
    :param values: ``{value: label}`` overrides for select fields.
    :param compute: ``BaseBone.compute`` info, emitted verbatim.
    :param languages: Language codes of a ``Language[X]`` field (JSON column).
    :param format: Display format of record/relational bones, emitted verbatim.
    :param schema_extra: Extra ``FieldInfo`` kwargs, see ``ALLOWED_SCHEMA_EXTRA``.
    :param kwargs: Passed to ``sqlmodel.Field`` unchanged.
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

    return SQLModelField(default, schema_extra=schema_extra, **kwargs)
