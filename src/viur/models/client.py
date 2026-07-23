"""fromClient error mapping — pydantic validation errors → viur-core's error type.

``skel.fromClient()`` reports problems as ``ReadFromClientError`` per field;
this module translates pydantic's ``ValidationError`` into the same shape
(analysis/01 §7), so the envelope render serializes both worlds over the
same code path — there is no second error serializer.

viur-core is imported lazily so that ``import viur.models`` (and pure model
definitions) never require the App Engine stack.
"""
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover
    from pydantic import ValidationError
    from viur.core.bones.base import ReadFromClientError


def map_validation_error(exc: "ValidationError") -> list["ReadFromClientError"]:
    """Translate a pydantic ``ValidationError`` into ``ReadFromClientError``\\ s.

    Mapping (analysis/01 §7, verified against the real core): pydantic's
    ``missing`` (field not submitted) → ``NotSet`` — exactly what bones
    report for unsubmitted required fields ("Field not submitted");
    everything else (type/constraint/custom-validator errors) → ``Invalid``.
    ``fieldPath`` mirrors pydantic's ``loc`` tuple, giving the same path
    style as bones produce. ``invalidatedFields`` stays ``None`` — the bone
    concept ``InvalidatesOther`` has no pydantic counterpart and is not
    emulated.
    """
    from viur.core.bones.base import ReadFromClientError, ReadFromClientErrorSeverity

    return [
        ReadFromClientError(
            severity=(
                ReadFromClientErrorSeverity.NotSet
                if error["type"] == "missing"
                else ReadFromClientErrorSeverity.Invalid
            ),
            errorMessage=error["msg"],
            fieldPath=[str(loc) for loc in error["loc"]],
        )
        for error in exc.errors()
    ]


def relation_error(field_name: str, message: str = "Invalid key") -> "ReadFromClientError":
    """An ``Invalid`` error for a relational field (bad or unknown key)."""
    from viur.core.bones.base import ReadFromClientError, ReadFromClientErrorSeverity

    return ReadFromClientError(
        severity=ReadFromClientErrorSeverity.Invalid,
        errorMessage=message,
        fieldPath=[field_name],
    )
