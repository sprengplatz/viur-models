"""pydantic ``ValidationError`` → ``ReadFromClientError``."""
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover
    from pydantic import ValidationError
    from viur.core.bones.base import ReadFromClientError


def map_validation_error(exc: "ValidationError") -> list["ReadFromClientError"]:
    """``missing`` → ``NotSet``, anything else → ``Invalid``; ``fieldPath`` is pydantic's ``loc``."""
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
