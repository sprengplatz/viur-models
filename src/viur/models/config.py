"""Namespaced config under ``conf.models`` — the viur-typical way to steer
the database engine (same idiom as ``conf.actions`` in viur-actions).

Set the preset in your project's main.py, then let the engine build from
conf::

    from viur.core import conf
    import viur.models

    viur.models.install_config()
    conf.models.engine = "sqlite"                      # "memory" | "sqlite" | "postgres"
    conf.models.sqlite_file = "viur_models.sqlite3"
    viur.models.db.configure_from_conf()

The three presets:

- ``"memory"`` — SQLite in-memory (one shared connection via ``StaticPool``,
  so every session sees the same database). Tests/demos. **Postgres has no
  in-memory mode** — that is a SQLite feature; an "in-memory Postgres" is
  infrastructure (tmpfs/testcontainer), not a connection URL.
- ``"sqlite"`` — a SQLite file (``conf.models.sqlite_file``). Local dev.
- ``"postgres"`` — ``conf.models.postgres_dsn``, e.g.
  ``postgresql+pg8000://user:pw@host:5432/db`` (the driver package —
  ``pg8000`` or ``psycopg`` — must be installed). ``NullPool`` by default
  (App Engine); Cloud SQL wires through
  ``conf.models.engine_options = {"creator": connector_fn}``.

viur-core's ``conf`` runs in strict mode in production (attribute access
only, the attribute must exist) — plugins therefore register their own
sub-namespace, which :func:`install_config` does.
"""
from __future__ import annotations

import typing as t


class ModelsConfig:
    """``conf.models.*`` namespace owned by viur-models."""

    #: Engine preset: ``"memory"``, ``"sqlite"`` or ``"postgres"``.
    engine: t.Any = None

    #: SQLite file path for the ``"sqlite"`` preset.
    sqlite_file: str = "viur_models.sqlite3"

    #: Full DSN for the ``"postgres"`` preset
    #: (``postgresql+pg8000://user:pw@host:5432/db``).
    postgres_dsn: str = ""

    def __init__(self) -> None:
        #: Extra ``create_engine`` kwargs (e.g. Cloud SQL ``creator=…``,
        #: ``echo=True``, pool tuning). Instance attribute — never shared.
        self.engine_options: dict = {}


def install_config() -> ModelsConfig:
    """Attach a fresh :class:`ModelsConfig` to ``conf.models``.

    Idempotent: an existing instance is returned so previously-set values
    survive. The viur-core import happens lazily, keeping plain
    ``import viur.models`` core-free.
    """
    from viur.core import conf

    existing = getattr(conf, "models", None)
    if isinstance(existing, ModelsConfig):
        return existing
    models = ModelsConfig()
    conf.models = models
    return models
