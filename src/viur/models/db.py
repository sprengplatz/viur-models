"""Shared engine, one session per action, ``RecordJSON`` column type."""
import typing as t
from contextlib import contextmanager

from sqlalchemy import JSON
from sqlalchemy.pool import NullPool
from sqlalchemy.types import TypeDecorator
from sqlmodel import Session, SQLModel, create_engine

if t.TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine

_engine: "Engine | None" = None


def configure(engine_or_url: "Engine | str", **create_engine_kwargs: t.Any) -> "Engine":
    """Set the shared engine from a URL (``NullPool`` default) or a ready engine. Once, at boot."""
    global _engine
    if isinstance(engine_or_url, str):
        create_engine_kwargs.setdefault("poolclass", NullPool)
        _engine = create_engine(engine_or_url, **create_engine_kwargs)
    else:
        _engine = engine_or_url
    if _engine.dialect.name == "bigquery":
        import logging

        from .bigquery import apply_engine_workarounds

        for note in apply_engine_workarounds(_engine):
            logging.getLogger(__name__).warning("viur-models[bigquery]: %s", note)
    return _engine


def get_engine() -> "Engine":
    if _engine is None:
        raise RuntimeError(
            "viur.models.db is not configured — call "
            "viur.models.db.configure(<database url or engine>) once at app "
            "boot (next to viur.actions.install())."
        )
    return _engine


def reset() -> None:
    """Dispose and drop the engine (test isolation)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


@contextmanager
def get_session() -> t.Iterator[Session]:
    """Commit on success, rollback on error. ``expire_on_commit=False`` — instances stay readable after close."""
    session = Session(get_engine(), expire_on_commit=False)
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
                'engine "postgres" needs a DSN (conf.models.postgres_dsn, '
                'e.g. "postgresql+pg8000://user:pw@host:5432/db")'
            )
        return postgres_dsn
    if engine == "bigquery":
        if not bigquery_dsn:
            raise RuntimeError(
                'engine "bigquery" needs a DSN (conf.models.bigquery_dsn, '
                'e.g. "bigquery://my-project/my_dataset") — see '
                "viur.models.bigquery for the backend's compromises"
            )
        return bigquery_dsn
    raise RuntimeError(
        'engine must be "memory", "sqlite", "postgres" or "bigquery" '
        f"(got {engine!r}) — set conf.models.engine before building the engine"
    )


def url_from_conf() -> str:
    """The database URL of the current ``conf.models`` preset."""
    from .config import install_config

    cfg = install_config()
    return url_from_preset(
        cfg.engine, sqlite_file=cfg.sqlite_file, postgres_dsn=cfg.postgres_dsn,
        bigquery_dsn=getattr(cfg, "bigquery_dsn", ""),
    )


def configure_from_conf() -> "Engine":
    """Build the shared engine from the ``conf.models`` preset."""
    from sqlalchemy.pool import StaticPool

    from .config import install_config

    cfg = install_config()
    options = dict(cfg.engine_options)
    url = url_from_conf()

    if cfg.engine == "memory":
        options.setdefault("poolclass", StaticPool)  # one connection = one database
        options.setdefault("connect_args", {"check_same_thread": False})
    return configure(url, **options)
