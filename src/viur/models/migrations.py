"""Alembic integration behind a project's ``env.py``: URL resolution, ``import_models``, ``run``,
revision generation (``process_revision_directives``). Extra ``spltz-viur-models[migrations]``."""
import importlib
import pkgutil
import typing as t

from alembic import context
from sqlalchemy import UniqueConstraint, engine_from_config, pool
from sqlmodel import SQLModel

if t.TYPE_CHECKING:  # pragma: no cover
    from alembic.config import Config
    from sqlalchemy.engine import Connection, Engine

#: Env var with an explicit database URL.
DSN_ENV_VAR = "VIUR_MODELS_DSN"

#: viur-models' own tables (migrated like any other).
INTERNAL_TABLES = frozenset({"viur_models_relations"})


def target_metadata() -> t.Any:
    """``SQLModel.metadata`` — the whole schema once the models are imported."""
    return SQLModel.metadata


def import_models(*packages: str) -> list[str]:
    """Import each package and its non-private submodules (one level) to fill ``SQLModel.metadata``.
    Returns the imported module names."""
    imported = []
    for package_name in packages:
        package = importlib.import_module(package_name)
        imported.append(package_name)
        for info in pkgutil.iter_modules(getattr(package, "__path__", [])):
            if info.name.startswith("_"):
                continue
            importlib.import_module(f"{package_name}.{info.name}")
            imported.append(f"{package_name}.{info.name}")
    return imported


def _models_conf() -> t.Any:
    """``conf.models``, or ``None`` (viur-core absent or namespace not installed)."""
    try:
        from viur.core import conf
    except ImportError:  # pragma: no cover
        return None
    return getattr(conf, "models", None)


def resolve_url(
    config: "Config | None" = None,
    *,
    x_args: dict | None = None,
    fallback_url: str | None = None,
) -> str:
    """Database URL for this run, most explicit first: ``-x url=…`` (``x_args``),
    ``$VIUR_MODELS_DSN``, the configured engine, the ``conf.models`` preset, ``fallback_url``,
    ``sqlalchemy.url`` from ``alembic.ini``. ``-x db=<name>`` selects a non-default database
    (engine or ``conf.models.databases`` entry only). ``RuntimeError`` when none applies."""
    import os

    from . import db

    database = _database(x_args)
    if x_args and (url := (x_args.get("url") or "").strip()):
        return url
    if database == db.DEFAULT and (url := os.environ.get(DSN_ENV_VAR, "").strip()):
        return url

    try:
        engine = db.get_engine(database)
    except RuntimeError:
        pass
    else:
        return str(engine.url.render_as_string(hide_password=False))

    # a set but misconfigured preset must surface: no try/except here
    cfg = _models_conf()
    if database != db.DEFAULT:
        if cfg is None:
            raise RuntimeError(f"No engine {database!r} and conf.models is not installed")
        return db.url_from_conf(database)
    if cfg is not None and db.DEFAULT in cfg.databases:
        return db.url_from_conf()

    if fallback_url and fallback_url.strip():
        return fallback_url.strip()

    if config is not None:
        if url := (config.get_main_option("sqlalchemy.url") or "").strip():
            return url

    raise RuntimeError(
        "No database URL for the migration. Tried, in order: -x url=…, "
        f"${DSN_ENV_VAR}, the configured engine (viur.models.db.configure), "
        "conf.models, the env.py fallback, and sqlalchemy.url in alembic.ini."
    )


def _database(x_args: dict | None) -> str:
    from . import db

    return ((x_args or {}).get("db") or "").strip() or db.DEFAULT


def include_object(
    obj: t.Any, name: str | None, type_: str, reflected: bool, compare_to: t.Any,
) -> bool:
    """Autogenerate filter: everything but ``alembic_version``. Wrap it to exclude foreign tables."""
    return not (type_ == "table" and name == "alembic_version")


def include_object_for(database: str) -> t.Callable:
    """``include_object`` restricted to the tables of ``database`` (``db.tables_for``)."""
    from . import db

    names = {table.name for table in db.tables_for(database)}

    def _include(obj: t.Any, name: str | None, type_: str, reflected: bool, compare_to: t.Any) -> bool:
        if type_ == "table" and name not in names:
            return False
        return include_object(obj, name, type_, reflected, compare_to)

    return _include


def render_item(type_: str, obj: t.Any, autogen_context: t.Any) -> t.Any:
    """Render a ``TypeDecorator`` as its ``impl`` type (``RecordJSON`` cannot round-trip);
    SQLModel's decorators and dialect-specific impls are left to Alembic."""
    from sqlalchemy.types import TypeDecorator, TypeEngine

    if type_ != "type" or not isinstance(obj, TypeDecorator):
        return False
    if type(obj).__module__.startswith("sqlmodel."):
        return False
    impl = obj.impl if isinstance(obj.impl, TypeEngine) else obj.impl()
    if not type(impl).__module__.startswith("sqlalchemy.sql.sqltypes"):
        return False  # dialect-specific impl (JSONB, …): no sa. name
    autogen_context.imports.add("import sqlalchemy as sa")
    return f"sa.{impl!r}"


def _configure_kwargs(url: str, database: str = "default", **overrides: t.Any) -> dict:
    """Shared ``context.configure`` kwargs: ``compare_type`` on, ``render_as_batch`` on SQLite."""
    kwargs = {
        "target_metadata": target_metadata(),
        "include_object": include_object_for(database),
        "render_item": render_item,
        "process_revision_directives": process_revision_directives,
        "compare_type": True,
        "render_as_batch": url.startswith("sqlite"),
    }
    kwargs.update(overrides)
    return kwargs


def run_offline(url: str, database: str = "default", **overrides: t.Any) -> None:
    """``--sql`` mode."""
    context.configure(url=url, literal_binds=True,
                      dialect_opts={"paramstyle": "named"},
                      **_configure_kwargs(url, database, **overrides))
    with context.begin_transaction():
        context.run_migrations()


def run_online(url: str, config: "Config", database: str = "default", **overrides: t.Any) -> None:
    """Connect and run the migrations in one transaction."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    engine: "Engine" = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    connection: "Connection"
    with engine.connect() as connection:
        context.configure(connection=connection, **_configure_kwargs(url, database, **overrides))
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


def run(*, fallback_url: str | None = None, **overrides: t.Any) -> str:
    """``env.py`` entry point: resolve the URL (``-x db=<name>`` picks the database), dispatch
    offline/online; ``overrides`` go to ``context.configure``. Returns the URL."""
    config = context.config
    x_args = context.get_x_argument(as_dictionary=True)
    url = resolve_url(config, x_args=x_args, fallback_url=fallback_url)
    database = _database(x_args)
    if context.is_offline_mode():
        run_offline(url, database, **overrides)
    else:
        run_online(url, config, database, **overrides)
    return url


# --- bone-level transition generator ----------------------------------------

def _column_type(table: str, column: str) -> t.Any:
    """Target SQL type of a column."""
    sa_table = target_metadata().tables.get(table)
    if sa_table is None or column not in sa_table.columns:
        return None
    return sa_table.columns[column].type


def _column_nullable(table: str, column: str) -> bool:
    """Nullability from the SQLAlchemy column (not the bone's ``required``)."""
    sa_table = target_metadata().tables.get(table)
    if sa_table is None or column not in sa_table.columns:
        return True
    return bool(sa_table.columns[column].nullable)


def _iter_table_ops(upgrade_ops: t.Any, table: str) -> t.Iterator[t.Any]:
    """``ModifyTableOps`` groups of one table."""
    from alembic.operations import ops as alembic_ops

    for op in upgrade_ops.ops:
        if isinstance(op, alembic_ops.ModifyTableOps) and op.table_name == table:
            yield op


def _drop_column_ops(upgrade_ops: t.Any, table: str, columns: set, kinds: tuple) -> None:
    """Remove per-column operations a semantic operation supersedes."""
    for group in _iter_table_ops(upgrade_ops, table):
        group.ops = [
            op for op in group.ops
            if not (isinstance(op, kinds) and getattr(op, "column_name", None) in columns
                    or isinstance(op, kinds)
                    and getattr(getattr(op, "column", None), "name", None) in columns)
        ]
    upgrade_ops.ops = [
        op for op in upgrade_ops.ops
        if not (hasattr(op, "ops") and not op.ops)  # drop now-empty groups
    ]


def _name_constraints(upgrade_ops: t.Any) -> list[str]:
    """Name unnamed autogenerated unique/FK constraints (``uq_<table>_<cols>``,
    ``fk_<table>_<cols>_<referent>``); SQLite's batch mode refuses unnamed ones. Returns the names."""
    from alembic.operations import ops as alembic_ops

    assigned = []

    def _visit(op_list: list) -> None:
        for op in op_list:
            if isinstance(op, alembic_ops.ModifyTableOps):
                _visit(op.ops)
            elif isinstance(op, alembic_ops.CreateUniqueConstraintOp) \
                    and not op.constraint_name:
                op.constraint_name = "uq_{}_{}".format(
                    op.table_name, "_".join(op.columns))
                assigned.append(op.constraint_name)
            elif isinstance(op, alembic_ops.CreateForeignKeyOp) \
                    and not op.constraint_name:
                op.constraint_name = "fk_{}_{}_{}".format(
                    op.source_table, "_".join(op.local_cols), op.referent_table)
                assigned.append(op.constraint_name)

    _visit(upgrade_ops.ops)
    return assigned


def _column_unique(table: str, column: str) -> bool:
    """UNIQUE from the metadata (the structure always emits ``unique: False``)."""
    sa_table = target_metadata().tables.get(table)
    if sa_table is None or column not in sa_table.columns:
        return False
    sa_column = sa_table.columns[column]
    if sa_column.unique:
        return True
    return any(
        set(constraint.columns) == {sa_column}
        for constraint in sa_table.constraints
        if isinstance(constraint, UniqueConstraint)
    )


def _drop_fk_ops(upgrade_ops: t.Any, table: str, columns: set) -> None:
    """Remove autogenerated FK creations for ``columns`` (``collapse_multiple`` creates a named one)."""
    from alembic.operations import ops as alembic_ops

    for group in _iter_table_ops(upgrade_ops, table):
        group.ops = [
            op for op in group.ops
            if not (isinstance(op, alembic_ops.CreateForeignKeyOp)
                    and set(op.local_cols or ()) & columns)
        ]


def _drop_table_op(upgrade_ops: t.Any, table: str) -> None:
    from alembic.operations import ops as alembic_ops

    upgrade_ops.ops = [
        op for op in upgrade_ops.ops
        if not (isinstance(op, alembic_ops.DropTableOp) and op.table_name == table)
    ]


def _make_nullable(upgrade_ops: t.Any, table: str, column: str) -> bool:
    """Relax a freshly added NOT NULL column to nullable (add → fill → constrain). Returns whether found."""
    from alembic.operations import ops as alembic_ops

    found = False
    for group in _iter_table_ops(upgrade_ops, table):
        for op in group.ops:
            if isinstance(op, alembic_ops.AddColumnOp) and op.column.name == column \
                    and not op.column.nullable:
                op.column.nullable = True
                found = True
    return found


def _default_fill_value(bone: dict | None) -> t.Any:
    """Fill value of a new required column: the bone's ``defaultvalue``, else ``emptyvalue``."""
    if not bone:
        return None
    if "defaultvalue" in bone and bone["defaultvalue"] is not None:
        return bone["defaultvalue"]
    return bone.get("emptyvalue")


def _generate_transition_ops(upgrade_ops: t.Any, transitions: list) -> list[str]:
    """Rewrite ``upgrade_ops`` per transition. Returns notes for the log."""
    from alembic.operations import ops as alembic_ops

    from . import migrate

    notes = []
    add_kinds = (alembic_ops.AddColumnOp,)
    alter_kinds = (alembic_ops.AlterColumnOp,)
    drop_kinds = (alembic_ops.DropColumnOp,)

    for transition in transitions:
        table, field = transition.table, transition.field
        detail = transition.detail

        if transition.kind == "multiple_collapsed":
            target_column = detail.get("target_column")
            if not (target_column and detail.get("link_table")):
                continue
            # Alembic would drop the link table first
            _drop_table_op(upgrade_ops, detail["link_table"])
            _drop_fk_ops(upgrade_ops, table, {target_column})
            _drop_column_ops(upgrade_ops, table, {target_column}, add_kinds)
            upgrade_ops.ops.append(migrate.CollapseMultipleOp(
                table, link_table=detail["link_table"],
                target_column=target_column,
                link_parent_fk=detail["link_parent_fk"],
                link_dest_fk=detail["link_dest_fk"],
                foreign_table=detail["target_table"], foreign_column="id",
                target_type=_column_type(table, target_column),
                keep="first", pk="id", drop_link_table=True,
            ))
            notes.append(f"{table}.{field}: multiple -> single (keeps the first target)")

        elif transition.kind == "multiple_expanded":
            source_column = detail.get("source_column")
            if not (source_column and detail.get("link_table")):
                continue
            link_table = detail["link_table"]
            # the CREATE TABLE stays; the column drop moves into the operation
            _drop_column_ops(upgrade_ops, table, {source_column}, drop_kinds)
            # NOT NULL payload columns: model default or an Ellipsis stub; nullable stays NULL
            payload_defaults = {}
            for name, bone in ((transition.new or {}).get("using") or {}).items():
                if _column_nullable(link_table, name):
                    continue
                value = _default_fill_value(bone)
                payload_defaults[name] = ... if value is None else value
            upgrade_ops.ops.append(migrate.ExpandMultipleOp(
                table, link_table=link_table,
                source_column=source_column,
                link_parent_fk=detail["link_parent_fk"],
                link_dest_fk=detail["link_dest_fk"],
                payload_defaults=payload_defaults or None,
                pk="id", drop_source_column=True,
            ))
            notes.append(f"{table}.{field}: single -> multiple (one link row per value)")
            if filled := {k: v for k, v in payload_defaults.items() if v is not Ellipsis}:
                notes.append(
                    f"{link_table}: NOT NULL payload column(s) filled from the "
                    f"model's defaults: {filled!r}"
                )
            if unfilled := [k for k, v in payload_defaults.items() if v is Ellipsis]:
                notes.append(
                    f"WARNING {link_table}: NOT NULL payload column(s) {unfilled!r} "
                    "have no default to fill in — the revision will refuse to run "
                    "until payload_defaults is completed"
                )

        elif transition.kind == "languages_reduced":
            _drop_column_ops(upgrade_ops, table, {field}, alter_kinds)
            upgrade_ops.ops.append(migrate.ReduceLanguagesOp(
                table, column=field, new_type=_column_type(table, field),
                languages=detail["languages"], keep=detail["languages"][0]
                if detail["languages"] else None,
                pk="id", nullable=_column_nullable(table, field),
            ))
            notes.append(
                f"{table}.{field}: multilingual -> single value "
                f"(keeps {detail['languages'][0] if detail['languages'] else '?'})"
            )

        elif transition.kind == "languages_expanded":
            _drop_column_ops(upgrade_ops, table, {field}, alter_kinds)
            upgrade_ops.ops.append(migrate.ExpandLanguagesOp(
                table, column=field, languages=detail["languages"],
                new_type=_column_type(table, field), pk="id",
            ))
            notes.append(
                f"{table}.{field}: single value -> multilingual "
                f"(into {detail['languages'][0]}, like languages[0])"
            )

        elif transition.kind == "type_changed":
            source, target = detail["from"], detail["to"]
            _drop_column_ops(upgrade_ops, table, {field}, alter_kinds)
            nullable = _column_nullable(table, field)
            if target == "bool":
                upgrade_ops.ops.append(migrate.CoerceBoolOp(
                    table, column=field, truthy=list(migrate.BOOLEAN_TRUTHY),
                    pk="id", nullable=nullable,
                ))
                notes.append(f"{table}.{field}: {source} -> bool (parse.bool rules)")
            elif source == "bool":
                column_type = _column_type(table, field)
                # the stored labels are enum member NAMES, not the bone values
                choices = list(getattr(column_type, "enums", None) or [])
                upgrade_ops.ops.append(migrate.RemapValuesOp(
                    table, column=field, mapping={True: ..., False: ...},
                    new_type=column_type, pk="id", nullable=nullable,
                ))
                notes.append(
                    f"{table}.{field}: bool -> {target} — MAPPING REQUIRED, the "
                    f"revision will refuse to run until it is filled. Use the "
                    f"STORED labels, not the client values: "
                    f"{choices or 'see the model'}"
                )
            else:
                upgrade_ops.ops.append(migrate.CoerceTextOp(
                    table, column=field, new_type=_column_type(table, field),
                    pk="id", nullable=nullable,
                ))
                notes.append(f"{table}.{field}: {source} -> {target}")

        elif transition.kind == "select_values_changed":
            column_type = _column_type(table, field)
            type_name = getattr(column_type, "name", None)
            labels = list(getattr(column_type, "enums", None) or [])
            if type_name and labels:
                upgrade_ops.ops.append(
                    migrate.AddEnumValuesOp(type_name, labels=labels))
                if detail["added"]:
                    notes.append(
                        f"{table}.{field}: new option(s) {detail['added']} — "
                        f"ALTER TYPE on Postgres, no-op on SQLite"
                    )
            if detail["removed"]:
                notes.append(
                    f"WARNING {table}.{field}: option(s) {detail['removed']} removed. "
                    f"Rows still holding them are not rewritten, and Postgres cannot "
                    f"drop an enum value — recreating the type is project-specific."
                )

        elif transition.kind == "precision_changed":
            _drop_column_ops(upgrade_ops, table, {field}, alter_kinds)
            upgrade_ops.ops.append(migrate.CoerceNumericOp(
                table, column=field, new_type=_column_type(table, field),
                precision=detail["precision"], pk="id",
                nullable=_column_nullable(table, field),
            ))
            notes.append(
                f"{table}.{field}: precision -> {detail['precision']}"
                f"{' (rounds)' if detail['precision'] else ' (truncates, like the bone)'}"
            )

        elif transition.kind in ("field_added", "using_field_added"):
            target_table = detail.get("link_table") or table
            column = detail.get("using_field") or field
            if detail.get("relation"):
                continue  # relations have no plain column to fill
            if _make_nullable(upgrade_ops, target_table, column):
                fill_value = _default_fill_value(transition.new)
                upgrade_ops.ops.append(migrate.FillColumnOp(
                    target_table, column=column,
                    value=fill_value, nullable=False,
                ))
                notes.append(
                    f"{target_table}.{column}: new required field, filled with "
                    f"{fill_value!r} from the model's default"
                )
                if _column_unique(target_table, column):
                    notes.append(
                        f"WARNING {target_table}.{column} is UNIQUE — filling every "
                        f"row with {fill_value!r} violates it as soon as the table "
                        f"holds more than one row. Replace the fill_column value "
                        f"with per-row values before running this revision."
                    )

    return notes


def _is_dry_run(context: t.Any) -> bool:
    """``alembic check`` (full autogenerate, script discarded — no snapshot for it), detected via
    ``command_args``: ``head="head"`` and no message."""
    revision_context = (getattr(context, "opts", None) or {}).get("revision_context")
    args = getattr(revision_context, "command_args", None) or {}
    return args.get("head") == "head" and args.get("message") is None


def _known_revisions(config: t.Any) -> set:
    """Revision ids in the script directory."""
    from alembic.script import ScriptDirectory

    try:
        directory = ScriptDirectory.from_config(config)
        return {script.revision for script in directory.walk_revisions()}
    except Exception:
        return set()   # empty or unreadable script directory


def _parent_revision(revision: t.Any) -> str | None:
    """Base revision from the hook argument: ``()`` for an empty directory, a tuple otherwise (first wins)."""
    if isinstance(revision, (tuple, list)):
        return str(revision[0]) if revision else None
    return str(revision) if revision else None


def process_revision_directives(context: t.Any, revision: t.Any, directives: list) -> None:
    """Alembic hook: correct the DDL diff for bone-level transitions, write the structure snapshot."""
    from alembic import util as alembic_util

    from . import schema

    if not directives:
        return
    script = directives[0]
    if script.upgrade_ops is None:
        return

    config = context.config if hasattr(context, "config") else None
    location = config.get_main_option("script_location") if config else None
    if not location:
        return
    directory = schema.snapshot_dir(location)

    current = schema.snapshot()
    previous = schema.load(directory, _parent_revision(revision))
    transitions = schema.diff(previous, current)

    for name in _name_constraints(script.upgrade_ops):
        alembic_util.msg(f"viur-models: named the constraint {name} (SQLite needs it)")

    if transitions:
        for note in _generate_transition_ops(script.upgrade_ops, transitions):
            alembic_util.msg(f"viur-models: {note}")

    if _is_dry_run(context):
        return  # alembic check

    schema.save(directory, script.rev_id, current)
    schema.prune(directory, _known_revisions(config) | {script.rev_id})
