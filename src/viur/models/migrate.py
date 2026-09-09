"""Alembic operations for bone-level data migrations; row-wise coercions per viur-core's rules."""
import json
import typing as t

import sqlalchemy as sa
from alembic import util as alembic_util
from alembic.operations import MigrateOperation, Operations

#: Rows per chunk in the row-wise transforms.
CHUNK_SIZE = 1000

#: Temporary column prefix for retypes.
TEMP_PREFIX = "_viur_mig_"

#: ``conf.bone_boolean_str2true`` default, viur-core 3.9.
BOOLEAN_TRUTHY = ("true", "yes", "1")


# --- bone rules -------------------------------------------------------------

def pick_from_multiple(values: list, keep: str = "first") -> t.Any:
    """``BaseBone.unserialize`` rule ``loadVal[0]``; ``keep="last"`` takes the last."""
    if not values:
        return None
    return values[0] if keep == "first" else values[-1]


def pick_language(value: t.Any, languages: t.Sequence[str], keep: str | None = None) -> t.Any:
    """Multilingual dict → one value (``BaseBone.unserialize``): ``keep`` if present (even
    ``None``), else the first non-``None`` value; a list picks its first item."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return value
    if not isinstance(value, dict):
        return value
    candidates = {
        lang: item for lang, item in value.items()
        if lang != "_viurLanguageWrapper_" and item is not True
    }
    if keep and keep in candidates:
        picked = candidates[keep]
    else:
        picked = next((item for item in candidates.values() if item is not None), None)
    if isinstance(picked, list) and picked:  # multiple+languages → first
        picked = picked[0]
    return picked


def wrap_language(value: t.Any, languages: t.Sequence[str]) -> dict:
    """Scalar → ``{lang: value}`` under ``languages[0]`` (``BaseBone.unserialize``), the rest ``None``."""
    result: dict[str, t.Any] = {lang: None for lang in languages}
    if value is not None and value != "" and languages:
        result[languages[0]] = value
    return result


def coerce_number(value: t.Any, precision: int) -> t.Any:
    """``NumericBone._convert_to_numeric``: ``precision > 0`` rounds, ``0`` truncates toward zero."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", ".", 1)
    try:
        if precision:
            return round(float(value), precision)
        return int(float(value))
    except (ValueError, TypeError):
        return None


def coerce_text(value: t.Any) -> t.Any:
    """``StringBone.type_coerce_single_value``: stringify, dates ISO, never truncate."""
    import datetime

    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if not value:
        return ""
    return str(value)


def coerce_bool(value: t.Any, truthy: t.Sequence[str] = BOOLEAN_TRUTHY) -> bool:
    """``utils.parse.bool`` with ``conf.bone_boolean_str2true``."""
    return str(value).strip().lower() in tuple(truthy)


# --- row-wise transform -----------------------------------------------------

def _chunks(connection: t.Any, table: str, pk: str) -> t.Iterator[list]:
    keys = [row[0] for row in connection.execute(
        sa.text(f'SELECT "{pk}" FROM "{table}" ORDER BY "{pk}"'),  # noqa: S608
    )]
    for start in range(0, len(keys), CHUNK_SIZE):
        yield keys[start:start + CHUNK_SIZE]


def ensure_schema_type(connection: t.Any, new_type: t.Any) -> bool:
    """Create a dialect-level type (Postgres ``Enum``) before its column; no-op elsewhere.
    Returns whether one was created."""
    create = getattr(new_type, "create", None)
    if not callable(create):
        return False
    create(connection, checkfirst=True)
    return True


def transform_column(
    operations: t.Any,
    table: str,
    column: str,
    *,
    new_type: t.Any,
    transform: t.Callable[[t.Any], t.Any],
    pk: str = "id",
    nullable: bool = True,
) -> int:
    """Retype ``column`` through a temporary column: add, copy through ``transform``, drop,
    rename, constrain — alike on SQLite (batch) and Postgres. Returns rows written."""
    connection = operations.get_bind()
    temp = f"{TEMP_PREFIX}{column}"

    ensure_schema_type(connection, new_type)

    with operations.batch_alter_table(table) as batch_op:
        batch_op.add_column(sa.Column(temp, new_type, nullable=True))

    written = 0
    for keys in _chunks(connection, table, pk):
        placeholders = ", ".join(f":k{index}" for index in range(len(keys)))
        params = {f"k{index}": key for index, key in enumerate(keys)}
        rows = connection.execute(
            sa.text(  # noqa: S608
                f'SELECT "{pk}", "{column}" FROM "{table}" '
                f'WHERE "{pk}" IN ({placeholders})'
            ),
            params,
        ).all()
        for key, value in rows:
            connection.execute(
                sa.text(  # noqa: S608
                    f'UPDATE "{table}" SET "{temp}" = :value WHERE "{pk}" = :key'
                ),
                {"value": _to_sql(transform(value)), "key": key},
            )
            written += 1

    # one batch block: on SQLite each block rebuilds the table
    with operations.batch_alter_table(table) as batch_op:
        batch_op.drop_column(column)
        # no existing_type: AutoString has no type.name
        batch_op.alter_column(temp, new_column_name=column, nullable=nullable)
    return written


def _to_sql(value: t.Any) -> t.Any:
    """Dicts/lists go into a column as JSON text; everything else as-is."""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


# --- operations -------------------------------------------------------------

class _ViURMigrateOp(MigrateOperation):
    """Base; ``kwargs`` are the rendered call's keyword arguments."""

    #: Name on ``op``.
    op_name: t.ClassVar[str] = ""

    def __init__(self, table: str, **kwargs: t.Any):
        self.table = table
        self.kwargs = kwargs


@Operations.register_operation("reduce_languages")
class ReduceLanguagesOp(_ViURMigrateOp):
    """``Language[X]`` → plain field (``pick_language``)."""

    op_name = "reduce_languages"

    @classmethod
    def reduce_languages(
        cls, operations: t.Any, table: str, column: str, *,
        new_type: t.Any, languages: t.Sequence[str] = (), keep: str | None = None,
        pk: str = "id", nullable: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, column=column, new_type=new_type, languages=list(languages),
            keep=keep, pk=pk, nullable=nullable,
        ))


@Operations.register_operation("expand_languages")
class ExpandLanguagesOp(_ViURMigrateOp):
    """Plain field → ``Language[X]`` (``wrap_language``)."""

    op_name = "expand_languages"

    @classmethod
    def expand_languages(
        cls, operations: t.Any, table: str, column: str, *,
        languages: t.Sequence[str], new_type: t.Any = None, pk: str = "id",
    ) -> t.Any:
        return operations.invoke(cls(
            table, column=column, languages=list(languages),
            new_type=new_type, pk=pk,
        ))


@Operations.register_operation("coerce_numeric")
class CoerceNumericOp(_ViURMigrateOp):
    """Numeric precision change (``coerce_number``)."""

    op_name = "coerce_numeric"

    @classmethod
    def coerce_numeric(
        cls, operations: t.Any, table: str, column: str, *,
        new_type: t.Any, precision: int, pk: str = "id", nullable: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, column=column, new_type=new_type, precision=precision,
            pk=pk, nullable=nullable,
        ))


@Operations.register_operation("coerce_text")
class CoerceTextOp(_ViURMigrateOp):
    """``str`` ↔ ``Text`` and other string retypes (``coerce_text``); Alembic does not detect a
    dropped ``max_length``."""

    op_name = "coerce_text"

    @classmethod
    def coerce_text(
        cls, operations: t.Any, table: str, column: str, *,
        new_type: t.Any, pk: str = "id", nullable: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, column=column, new_type=new_type, pk=pk, nullable=nullable,
        ))


@Operations.register_operation("coerce_bool")
class CoerceBoolOp(_ViURMigrateOp):
    """Anything → ``bool`` (``coerce_bool``)."""

    op_name = "coerce_bool"

    @classmethod
    def coerce_bool(
        cls, operations: t.Any, table: str, column: str, *,
        truthy: t.Sequence[str] = BOOLEAN_TRUTHY, pk: str = "id",
        nullable: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, column=column, truthy=list(truthy), pk=pk, nullable=nullable,
        ))


@Operations.register_operation("remap_values")
class RemapValuesOp(_ViURMigrateOp):
    """Explicit value mapping (``bool`` → ``select``); a mapping still holding ``Ellipsis`` refuses to run."""

    op_name = "remap_values"

    @classmethod
    def remap_values(
        cls, operations: t.Any, table: str, column: str, mapping: dict, *,
        new_type: t.Any, pk: str = "id", nullable: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, column=column, mapping=mapping, new_type=new_type,
            pk=pk, nullable=nullable,
        ))


@Operations.register_operation("fill_column")
class FillColumnOp(_ViURMigrateOp):
    """Fill ``NULL``s with ``value``, then optionally ``NOT NULL`` — the new-required-field case."""

    op_name = "fill_column"

    @classmethod
    def fill_column(
        cls, operations: t.Any, table: str, column: str, value: t.Any, *,
        nullable: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, column=column, value=value, nullable=nullable,
        ))


@Operations.register_operation("add_enum_values")
class AddEnumValuesOp(_ViURMigrateOp):
    """``ALTER TYPE … ADD VALUE IF NOT EXISTS`` for every label (Postgres only, no-op elsewhere).
    Removing a label is not covered."""

    op_name = "add_enum_values"

    @classmethod
    def add_enum_values(
        cls, operations: t.Any, type_name: str, labels: t.Sequence[str],
    ) -> t.Any:
        return operations.invoke(cls(type_name, labels=list(labels)))


@Operations.register_operation("collapse_multiple")
class CollapseMultipleOp(_ViURMigrateOp):
    """Multiple relation → single FK column (``pick_from_multiple``); the link table is dropped
    after the copy. ``target_type``: the FK column's type (default ``Integer``; ``BigQueryModel``
    needs a string type)."""

    op_name = "collapse_multiple"

    @classmethod
    def collapse_multiple(
        cls, operations: t.Any, table: str, *, link_table: str,
        target_column: str, link_parent_fk: str, link_dest_fk: str,
        foreign_table: str, foreign_column: str = "id",
        target_type: t.Any = None,
        keep: str = "first", pk: str = "id", drop_link_table: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, link_table=link_table, target_column=target_column,
            link_parent_fk=link_parent_fk, link_dest_fk=link_dest_fk,
            foreign_table=foreign_table, foreign_column=foreign_column,
            target_type=target_type,
            keep=keep, pk=pk, drop_link_table=drop_link_table,
        ))


@Operations.register_operation("expand_multiple")
class ExpandMultipleOp(_ViURMigrateOp):
    """Single FK column → link table, one row per value. ``payload_defaults`` fills NOT NULL
    payload columns; an ``Ellipsis`` stub refuses to run (as ``RemapValuesOp``), nullable
    payload stays ``NULL``."""

    op_name = "expand_multiple"

    @classmethod
    def expand_multiple(
        cls, operations: t.Any, table: str, *, link_table: str,
        source_column: str, link_parent_fk: str, link_dest_fk: str,
        payload_defaults: dict | None = None,
        pk: str = "id", drop_source_column: bool = True,
    ) -> t.Any:
        return operations.invoke(cls(
            table, link_table=link_table, source_column=source_column,
            link_parent_fk=link_parent_fk, link_dest_fk=link_dest_fk,
            payload_defaults=payload_defaults,
            pk=pk, drop_source_column=drop_source_column,
        ))


# --- implementations --------------------------------------------------------

@Operations.implementation_for(ReduceLanguagesOp)
def _reduce_languages(operations: t.Any, operation: ReduceLanguagesOp) -> None:
    kw = operation.kwargs
    transform_column(
        operations, operation.table, kw["column"],
        new_type=kw["new_type"],
        transform=lambda value: pick_language(value, kw["languages"], kw["keep"]),
        pk=kw["pk"], nullable=kw["nullable"],
    )


@Operations.implementation_for(ExpandLanguagesOp)
def _expand_languages(operations: t.Any, operation: ExpandLanguagesOp) -> None:
    kw = operation.kwargs
    transform_column(
        operations, operation.table, kw["column"],
        new_type=kw["new_type"] or sa.JSON(),
        transform=lambda value: wrap_language(value, kw["languages"]),
        pk=kw["pk"], nullable=True,
    )


@Operations.implementation_for(CoerceNumericOp)
def _coerce_numeric(operations: t.Any, operation: CoerceNumericOp) -> None:
    kw = operation.kwargs
    transform_column(
        operations, operation.table, kw["column"],
        new_type=kw["new_type"],
        transform=lambda value: coerce_number(value, kw["precision"]),
        pk=kw["pk"], nullable=kw["nullable"],
    )


@Operations.implementation_for(CoerceTextOp)
def _coerce_text(operations: t.Any, operation: CoerceTextOp) -> None:
    kw = operation.kwargs
    transform_column(
        operations, operation.table, kw["column"],
        new_type=kw["new_type"], transform=coerce_text,
        pk=kw["pk"], nullable=kw["nullable"],
    )


@Operations.implementation_for(CoerceBoolOp)
def _coerce_bool(operations: t.Any, operation: CoerceBoolOp) -> None:
    kw = operation.kwargs
    transform_column(
        operations, operation.table, kw["column"],
        new_type=sa.Boolean(),
        transform=lambda value: coerce_bool(value, kw["truthy"]),
        pk=kw["pk"], nullable=kw["nullable"],
    )


@Operations.implementation_for(RemapValuesOp)
def _remap_values(operations: t.Any, operation: RemapValuesOp) -> None:
    kw = operation.kwargs
    mapping = kw["mapping"]
    unfilled = [key for key, value in mapping.items() if value is Ellipsis]
    if unfilled:
        raise RuntimeError(
            f"remap_values({operation.table!r}, {kw['column']!r}): no target "
            f"value given for {unfilled!r}. viur-core has no rule for this "
            "transition (SelectBone matches an enum member by value, so a "
            "stored boolean matches nothing) — fill the mapping in this "
            "revision before running it."
        )

    def _map(value: t.Any) -> t.Any:
        if value in mapping:
            return mapping[value]
        # SQLite booleans are 0/1
        if isinstance(value, int) and not isinstance(value, bool):
            as_bool = bool(value)
            if as_bool in mapping:
                return mapping[as_bool]
        return mapping.get(None)

    transform_column(
        operations, operation.table, kw["column"],
        new_type=kw["new_type"], transform=_map,
        pk=kw["pk"], nullable=kw["nullable"],
    )


@Operations.implementation_for(FillColumnOp)
def _fill_column(operations: t.Any, operation: FillColumnOp) -> None:
    kw = operation.kwargs
    connection = operations.get_bind()
    connection.execute(
        sa.text(  # noqa: S608
            f'UPDATE "{operation.table}" SET "{kw["column"]}" = :value '
            f'WHERE "{kw["column"]}" IS NULL'
        ),
        {"value": _to_sql(kw["value"])},
    )
    if not kw["nullable"]:
        with operations.batch_alter_table(operation.table) as batch_op:
            batch_op.alter_column(kw["column"], nullable=False)


@Operations.implementation_for(AddEnumValuesOp)
def _add_enum_values(operations: t.Any, operation: AddEnumValuesOp) -> None:
    connection = operations.get_bind()
    if connection.dialect.name != "postgresql":
        return
    for label in operation.kwargs["labels"]:
        # DDL takes no bind parameters: literal with '' escaping
        escaped = str(label).replace("'", "''")
        connection.execute(sa.text(
            f"ALTER TYPE \"{operation.table}\" ADD VALUE IF NOT EXISTS '{escaped}'",  # noqa: S608
        ))


@Operations.implementation_for(CollapseMultipleOp)
def _collapse_multiple(operations: t.Any, operation: CollapseMultipleOp) -> None:
    kw = operation.kwargs
    connection = operations.get_bind()

    with operations.batch_alter_table(operation.table) as batch_op:
        batch_op.add_column(sa.Column(
            kw["target_column"], kw.get("target_type") or sa.Integer(), nullable=True,
        ))
        # SQLite's batch mode needs a named constraint
        batch_op.create_foreign_key(
            f"fk_{operation.table}_{kw['target_column']}_{kw['foreign_table']}",
            kw["foreign_table"], [kw["target_column"]], [kw["foreign_column"]],
        )

    rows = connection.execute(sa.text(  # noqa: S608
        f'SELECT "{kw["link_parent_fk"]}", "{kw["link_dest_fk"]}" '
        f'FROM "{kw["link_table"]}" ORDER BY "{kw["link_parent_fk"]}", "{kw["link_dest_fk"]}"'
    )).all()
    by_parent: dict[t.Any, list] = {}
    for parent, dest in rows:
        by_parent.setdefault(parent, []).append(dest)

    dropped = sum(1 for targets in by_parent.values() if len(targets) > 1)
    for parent, targets in by_parent.items():
        connection.execute(
            sa.text(  # noqa: S608
                f'UPDATE "{operation.table}" SET "{kw["target_column"]}" = :dest '
                f'WHERE "{kw["pk"]}" = :parent'
            ),
            {"dest": pick_from_multiple(targets, kw["keep"]), "parent": parent},
        )
    if dropped:
        alembic_util.msg(
            f"viur-models: {operation.table}.{kw['target_column']}: {dropped} row(s) "
            f"had several targets — kept the {kw['keep']} one, like the bone does"
        )

    if kw["drop_link_table"]:
        operations.drop_table(kw["link_table"])


@Operations.implementation_for(ExpandMultipleOp)
def _expand_multiple(operations: t.Any, operation: ExpandMultipleOp) -> None:
    kw = operation.kwargs
    connection = operations.get_bind()
    payload = dict(kw.get("payload_defaults") or {})
    if unfilled := [name for name, value in payload.items() if value is Ellipsis]:
        raise RuntimeError(
            f"expand_multiple({operation.table!r} -> {kw['link_table']!r}): no "
            f"value for the NOT NULL payload column(s) {unfilled!r}. The link "
            "rows are built from the single FK values, which carry no payload, "
            "and the model declares no default to fill in — set payload_defaults "
            "in this revision before running it."
        )

    rows = connection.execute(sa.text(  # noqa: S608
        f'SELECT "{kw["pk"]}", "{kw["source_column"]}" FROM "{operation.table}" '
        f'WHERE "{kw["source_column"]}" IS NOT NULL'
    )).all()
    columns = ", ".join(
        f'"{name}"' for name in (kw["link_parent_fk"], kw["link_dest_fk"], *payload)
    )
    placeholders = ", ".join((":parent", ":dest", *(f":p{i}" for i in range(len(payload)))))
    payload_params = {f"p{i}": _to_sql(value) for i, value in enumerate(payload.values())}
    for parent, dest in rows:
        connection.execute(
            sa.text(  # noqa: S608
                f'INSERT INTO "{kw["link_table"]}" ({columns}) VALUES ({placeholders})'
            ),
            {"parent": parent, "dest": dest, **payload_params},
        )

    if kw["drop_source_column"]:
        with operations.batch_alter_table(operation.table) as batch_op:
            batch_op.drop_column(kw["source_column"])


# --- autogenerate renderers -------------------------------------------------

def _render_type(value: t.Any, autogen_context: t.Any) -> str:
    """Render a SQL type the way Alembic renders column types."""
    from alembic.autogenerate import render as _render

    autogen_context.imports.add("import sqlalchemy as sa")
    return _render._repr_type(value, autogen_context)


def _render_dict(value: dict) -> str:
    """Dict literal; ``Ellipsis`` renders as ``...``."""
    items = ", ".join(
        f"{k!r}: {'...' if v is Ellipsis else repr(v)}" for k, v in value.items()
    )
    return f"{{{items}}}"


def _render_kwargs(operation: _ViURMigrateOp, autogen_context: t.Any) -> str:
    parts = []
    for key, value in operation.kwargs.items():
        if key in ("column", "value"):
            continue  # rendered positionally by _render_op
        if key in ("new_type", "target_type"):
            if value is None:
                continue
            parts.append(f"{key}={_render_type(value, autogen_context)}")
        elif key == "mapping":
            parts.append(_render_dict(value))  # positional, see remap_values
        elif key == "payload_defaults":
            if value:
                parts.append(f"{key}={_render_dict(value)}")
        else:
            parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _render_op(autogen_context: t.Any, operation: _ViURMigrateOp) -> str:
    autogen_context.imports.add("from viur.models import migrate  # noqa: F401")
    column = operation.kwargs.get("column")
    positional = [repr(operation.table)]
    if column is not None:
        positional.append(repr(column))
    if "value" in operation.kwargs:
        positional.append(repr(operation.kwargs["value"]))
    kwargs = _render_kwargs(operation, autogen_context)
    args = ", ".join(positional + ([kwargs] if kwargs else []))
    return f"op.{operation.op_name}({args})"


def _install_renderers() -> None:
    """Register the renderers."""
    from alembic.autogenerate import renderers

    for cls in (
        ReduceLanguagesOp, ExpandLanguagesOp, CoerceNumericOp, CoerceTextOp,
        CoerceBoolOp, RemapValuesOp, FillColumnOp, CollapseMultipleOp,
        ExpandMultipleOp, AddEnumValuesOp,
    ):
        renderers.dispatch_for(cls)(_render_op)


_install_renderers()
