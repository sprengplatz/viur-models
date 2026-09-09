"""The ``conf.models`` namespace and its engine presets."""
from __future__ import annotations

import typing as t


class ModelsConfig:
    """``conf.models.*`` namespace owned by viur-models."""

    #: Engine preset: ``"memory"``, ``"sqlite"``, ``"postgres"`` or ``"bigquery"``.
    engine: t.Any = None

    #: SQLite file path for the ``"sqlite"`` preset.
    sqlite_file: str = "viur_models.sqlite3"

    #: DSN for ``"postgres"`` (``postgresql+pg8000://user:pw@host:5432/db``).
    postgres_dsn: str = ""

    #: DSN for ``"bigquery"`` (``bigquery://project/dataset``), ADC credentials.
    bigquery_dsn: str = ""

    def __init__(self) -> None:
        #: Extra ``create_engine`` kwargs (instance attribute).
        self.engine_options: dict = {}


def install_config() -> ModelsConfig:
    """Attach ``ModelsConfig`` to ``conf.models``; idempotent."""
    from viur.core import conf

    existing = getattr(conf, "models", None)
    if isinstance(existing, ModelsConfig):
        return existing
    models = ModelsConfig()
    conf.models = models
    return models
