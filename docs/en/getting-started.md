# Getting started

## Install

```bash
pip install spltz-viur-models
```

Requires Python ≥ 3.12 and viur-core ≥ 3.8, < 4.

## Import is side-effect free

Importing the package does **not** patch viur-core and does not register
any skeletons:

```python
import viur.models   # nothing happens yet
```

## App boot: `install()` and `setup()`

Two calls in the project's `main.py`, one on either side of `core.setup()`:

```python
from viur import core
import viur.models

# before core.setup(): conf.models, engine, cross-store refresh hooks
viur.models.install(
    engine="sqlite",                  # "memory" | "sqlite" | "postgres" | "bigquery"
    sqlite_file="viur_models.sqlite3",
)

import modules, render
app = core.setup(modules, render)

# after core.setup() — models imported: report the schema ("memory": create_all)
viur.models.setup(migrations=PROJECT_ROOT)
```

The optional `migrations=` generates the Alembic scaffold (`alembic.ini` +
`migrations/`) on the dev server when it is missing and brings the database
up to date — see [migrations.md](migrations.md). Without the argument,
`setup()` only reports the schema state.

`install()` ignores every argument left at `None`, so values already set in
`conf.models` survive a partial call. `refresh_hooks=False` turns the hooks
off (for an application without cross-store references); additional keyword
arguments (`missing_on_delete`, `countdown`) are passed through to
[`install_refresh_hooks`][viur.models.install_refresh_hooks].

The individual steps remain available — `install()` only bundles them:

```python
viur.models.install_config()          # registers conf.models (idempotent)
conf.models.engine = "sqlite"
conf.models.sqlite_file = "viur_models.sqlite3"
viur.models.db.configure_from_conf()
viur.models.install_refresh_hooks()
```

## The `conf.models` presets

| Preset | Connection | Use |
|---|---|---|
| `"memory"` | SQLite in-memory (`sqlite://`, one shared connection via `StaticPool`, so every session sees the same database) | tests, demos |
| `"sqlite"` | SQLite file from `conf.models.sqlite_file` | local development |
| `"postgres"` | DSN from `conf.models.postgres_dsn`, e.g. `postgresql+pg8000://user:pw@host:5432/db` | production |
| `"bigquery"` | DSN from `conf.models.bigquery_dsn` — see [BigQuery](bigquery.md) | analytical data |

`"postgres"` needs its driver package installed (`pg8000` or `psycopg`).
Extra `create_engine` arguments go through `conf.models.engine_options` —
on App Engine, the Cloud SQL connector for instance:

```python
from google.cloud.sql.connector import Connector

connector = Connector()
conf.models.engine = "postgres"
conf.models.postgres_dsn = "postgresql+pg8000://"
conf.models.engine_options = {
    "creator": lambda: connector.connect(
        "project:region:instance", "pg8000",
        user="app", password="…", db="app",
    ),
}
viur.models.db.configure_from_conf()
```

The pool default for URL connections is `NullPool`; the `memory` preset
overrides it with `StaticPool`. Both can be overridden through
`engine_options["poolclass"]`. Without the conf, call
`viur.models.db.configure(url_or_engine, **kwargs)` directly.

## Development setup

Unit suite — viur-light-mock **overlay mode** (real viur-core, in-memory
datastore), 100 % coverage gate.

```bash
pip install --no-deps -e .
pip install pytest pytest-cov 'coverage[toml]' 'spltz-viur-light-mock>=0.3,<1.0' 'viur-core>=3.8,<3.9' 'spltz-viur-actions>=0.4,<1.0' sqlmodel pydantic-extra-types pycountry email-validator 'alembic>=1.13'
pytest
```

Integration suite — real core without the mock (real skeleton registry):

```bash
pip install "viur-core>=3.8,<3.9" rsa pytest 'spltz-viur-actions>=0.4,<1.0' sqlmodel pydantic-extra-types pycountry email-validator 'alembic>=1.13'
pip install --no-deps -e .
python -m pytest -c integration/pytest.ini integration
```

Docs — the overlay install above plus:

```bash
pip install mkdocs-material 'mkdocstrings[python]' mkdocs-static-i18n
mkdocs serve
```

`.github/workflows/` holds the verified install lines.
