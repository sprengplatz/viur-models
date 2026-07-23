"""Session lifecycle — one shared engine, one session per action call.

Configured **once** at app boot (next to ``viur.actions.install()``):

    import viur.models.db
    viur.models.db.configure("postgresql+pg8000://…")

On App Engine Standard the default pool class is ``NullPool`` — instances
scale to zero, a persistent pool would leak connections. Cloud SQL is wired
through the ``cloud-sql-python-connector`` via the ``creator=`` kwarg:

    from google.cloud.sql.connector import Connector
    connector = Connector()
    viur.models.db.configure(
        "postgresql+pg8000://",
        creator=lambda: connector.connect("project:region:instance", "pg8000", …),
    )

There is deliberately **no** implicit fallback engine: using
:func:`get_session` before :func:`configure` fails fast with instructions.
"""
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
    """Set the shared engine. Call once at app boot.

    Accepts a database URL (an engine is created, ``poolclass`` defaults to
    ``NullPool`` for App Engine) or a ready-made engine (used as-is — tests
    pass their SQLite engine here).
    """
    global _engine
    if isinstance(engine_or_url, str):
        create_engine_kwargs.setdefault("poolclass", NullPool)
        _engine = create_engine(engine_or_url, **create_engine_kwargs)
    else:
        _engine = engine_or_url
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
    """Dispose and drop the configured engine (test isolation)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


@contextmanager
def get_session() -> t.Iterator[Session]:
    """One session per action call: commit on success, rollback on error.

    ``expire_on_commit=False`` keeps instances readable after the session
    closes — the render serializes them outside the ``with`` block.
    """
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
    """JSON column for nested record models — the storage glue for plain
    pydantic nesting: model instances serialize to JSON dicts on write and
    validate back into the record class on read (lists for multiple)::

        address: Address | None = ViURField(default=None, sa_type=RecordJSON(Address))
        stops: list[Address] = ViURField(default_factory=list, sa_type=RecordJSON(Address))
    """

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


def configure_from_conf() -> "Engine":
    """Build the shared engine from the ``conf.models`` preset — see
    :mod:`viur.models.config` for the three presets (``memory`` /
    ``sqlite`` / ``postgres``) and their settings."""
    from sqlalchemy.pool import StaticPool

    from .config import install_config

    cfg = install_config()
    options = dict(cfg.engine_options)

    if cfg.engine == "memory":
        # one shared connection, so every session sees the same database
        options.setdefault("poolclass", StaticPool)
        options.setdefault("connect_args", {"check_same_thread": False})
        return configure("sqlite://", **options)
    if cfg.engine == "sqlite":
        return configure(f"sqlite:///{cfg.sqlite_file}", **options)
    if cfg.engine == "postgres":
        if not cfg.postgres_dsn:
            raise RuntimeError(
                'conf.models.engine = "postgres" needs conf.models.postgres_dsn '
                '(e.g. "postgresql+pg8000://user:pw@host:5432/db")'
            )
        return configure(cfg.postgres_dsn, **options)
    raise RuntimeError(
        'conf.models.engine must be "memory", "sqlite" or "postgres" '
        f"(got {cfg.engine!r}) — set it before viur.models.db.configure_from_conf()"
    )
