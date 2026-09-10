"""Named engines (``Model.viur_database``), one session per action, ``RecordJSON`` column type."""
import typing as t
from contextlib import contextmanager

from sqlalchemy import JSON
from sqlalchemy.pool import NullPool
from sqlalchemy.types import TypeDecorator
from sqlmodel import Session, SQLModel, create_engine

if t.TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine

DEFAULT = "default"

_engines: dict[str, "Engine"] = {}

#: A database name, a model class (``viur_database``) or ``None`` (default).
Target = t.Union[str, type, None]


def database_of(target: Target) -> str:
    if target is None:
        return DEFAULT
    if isinstance(target, str):
        return target
    return getattr(target, "viur_database", DEFAULT)


def configure(
    engine_or_url: "Engine | str", *, name: str = DEFAULT, **create_engine_kwargs: t.Any,
) -> "Engine":
    """Register the engine ``name`` from a URL (``NullPool`` default) or a ready engine. At boot."""
    if isinstance(engine_or_url, str):
        create_engine_kwargs.setdefault("poolclass", NullPool)
        engine = create_engine(engine_or_url, **create_engine_kwargs)
    else:
        engine = engine_or_url
    if engine.dialect.name == "bigquery":
        import logging

        from .bigquery import apply_engine_workarounds

        for note in apply_engine_workarounds(engine):
            logging.getLogger(__name__).warning("viur-models[bigquery]: %s", note)
    _engines[name] = engine
    return engine


def get_engine(target: Target = None) -> "Engine":
    name = database_of(target)
    if (engine := _engines.get(name)) is None:
        raise RuntimeError(
            f"viur.models.db has no engine {name!r} — call "
            f"viur.models.db.configure(<database url or engine>, name={name!r}) once "
            "at app boot (next to viur.actions.install())."
        )
    return engine


def engine_names() -> tuple[str, ...]:
    return tuple(_engines)


def tables_for(target: Target) -> list[t.Any]:
    """Tables of ``target``'s database: those of models bound to it plus every table
    without a ``viur_database`` owner (viur-models' internal tables, present in each database)."""
    name = database_of(target)
    owners = {
        mapper.local_table: mapper.class_
        for mapper in SQLModel._sa_registry.mappers
    }
    return [
        table for table in SQLModel.metadata.tables.values()
        if not hasattr(owners.get(table), "viur_database")
        or database_of(owners[table]) == name
    ]


def reset() -> None:
    """Dispose and drop every engine (test isolation)."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()


@contextmanager
def get_session(target: Target = None) -> t.Iterator[Session]:
    """Commit on success, rollback on error. ``expire_on_commit=False`` — instances stay readable after close."""
    session = Session(get_engine(target), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


class RecordJSON(TypeDecorator):
    """JSON column for nested records: ``record_cls`` instances (or lists) dump on write, validate on read."""

    impl = JSON
    cache_ok = True

    def __init__(self, record_cls: type[SQLModel]):
        super().__init__()
        self.record_cls = record_cls

    def process_bind_param(self, value: t.Any, dialect: t.Any) -> t.Any:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [self._to_plain(item) for item in value]
        return self._to_plain(value)

    def process_result_value(self, value: t.Any, dialect: t.Any) -> t.Any:
        if value is None:
            return None
        if isinstance(value, list):
            return [self.record_cls.model_validate(item) for item in value]
        return self.record_cls.model_validate(value)

    @staticmethod
    def _to_plain(value: t.Any) -> t.Any:
        return value.model_dump(mode="json") if isinstance(value, SQLModel) else value


ALEMBIC_VERSION_TABLE = "alembic_version"


def schema_revision(engine: "Engine | None" = None) -> str | None:
    """Stamped Alembic revision via plain SQL; ``None`` without an ``alembic_version`` table."""
    from sqlalchemy import inspect, text

    engine = engine or get_engine()
    with engine.connect() as connection:
        if not inspect(connection).has_table(ALEMBIC_VERSION_TABLE):
            return None
        row = connection.execute(
            text(f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE}"),  # noqa: S608
        ).first()
    return row[0] if row else None


def url_from_preset(
    engine: str | None, *, sqlite_file: str = "viur_models.sqlite3",
    postgres_dsn: str = "", bigquery_dsn: str = "",
) -> str:
    """Database URL for a ``conf.models`` preset."""
    if engine == "memory":
        return "sqlite://"
    if engine == "sqlite":
        return f"sqlite:///{sqlite_file}"
    if engine == "postgres":
        if not postgres_dsn:
            raise RuntimeError(
                'engine "postgres" needs a DSN (postgres_dsn, '
                'e.g. "postgresql+pg8000://user:pw@host:5432/db")'
            )
        return postgres_dsn
    if engine == "bigquery":
        if not bigquery_dsn:
            raise RuntimeError(
                'engine "bigquery" needs a DSN (bigquery_dsn, '
                'e.g. "bigquery://my-project/my_dataset") — see '
                "viur.models.bigquery for the backend's compromises"
            )
        return bigquery_dsn
    raise RuntimeError(
        'engine must be "memory", "sqlite", "postgres" or "bigquery" '
        f"(got {engine!r}) — set it in conf.models.databases before building the engine"
    )


def _settings(name: str) -> dict:
    from .config import install_config

    if (settings := install_config().databases.get(name)) is None:
        raise RuntimeError(f"conf.models.databases has no entry {name!r}")
    return settings


def url_from_conf(name: str = DEFAULT) -> str:
    """Database URL of a ``conf.models`` preset (``url`` entries pass through)."""
    settings = _settings(name)
    if url := settings.get("url"):
        return url
    return url_from_preset(
        settings.get("engine"),
        sqlite_file=settings.get("sqlite_file", "viur_models.sqlite3"),
        postgres_dsn=settings.get("postgres_dsn", ""),
        bigquery_dsn=settings.get("bigquery_dsn", ""),
    )


def preset_from_conf(name: str = DEFAULT) -> str | None:
    return _settings(name).get("engine")


def _configure_preset(name: str) -> "Engine":
    from sqlalchemy.pool import StaticPool

    settings = _settings(name)
    options = dict(settings.get("engine_options") or {})
    url = url_from_conf(name)
    if settings.get("engine") == "memory":
        options.setdefault("poolclass", StaticPool)  # one connection = one database
        options.setdefault("connect_args", {"check_same_thread": False})
    return configure(url, name=name, **options)


def configure_from_conf() -> "Engine":
    """One engine per ``conf.models.databases`` entry. Returns the default engine."""
    from .config import install_config

    engines = {name: _configure_preset(name) for name in install_config().databases}
    if DEFAULT not in engines:
        raise RuntimeError(f"conf.models.databases has no entry {DEFAULT!r}")
    return engines[DEFAULT]
