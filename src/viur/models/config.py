"""The ``conf.models`` namespace."""
from __future__ import annotations


class ModelsConfig:
    """``conf.models.*`` namespace owned by viur-models."""

    def __init__(self) -> None:
        #: Databases by name (``Model.viur_database``, ``"default"`` required). Entry keys:
        #: ``engine`` (``"memory"`` | ``"sqlite"`` | ``"postgres"`` | ``"bigquery"``),
        #: ``sqlite_file``, ``postgres_dsn``, ``bigquery_dsn``, ``engine_options``, or ``url``.
        self.databases: dict[str, dict] = {}


def install_config() -> ModelsConfig:
    """Attach ``ModelsConfig`` to ``conf.models``; idempotent."""
    from viur.core import conf

    existing = getattr(conf, "models", None)
    if isinstance(existing, ModelsConfig):
        return existing
    models = ModelsConfig()
    conf.models = models
    return models
