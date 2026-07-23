# Getting started

## Install

```bash
pip install viur-models
```

Requires Python ≥ 3.12 and viur-core ≥ 3.8, < 4.

## Import is side-effect free

Importing the package does **not** patch viur-core and does not register
any skeletons:

```python
import viur.models   # nothing happens yet
```

## Database engine via `conf.models`

viur-typisch wird die Engine über die viur-core-`conf` gesteuert. In der
`main.py` des Projekts das Namespace registrieren, Preset wählen, Engine
bauen:

```python
from viur.core import conf
import viur.models

viur.models.install_config()          # registriert conf.models (idempotent)
conf.models.engine = "sqlite"         # "memory" | "sqlite" | "postgres"
conf.models.sqlite_file = "viur_models.sqlite3"
viur.models.db.configure_from_conf()
```

Die drei Presets:

| Preset | Verbindung | Einsatz |
|---|---|---|
| `"memory"` | SQLite in-memory (`sqlite://`, eine geteilte Verbindung via `StaticPool`, damit alle Sessions dieselbe Datenbank sehen) | Tests, Demos |
| `"sqlite"` | SQLite-Datei aus `conf.models.sqlite_file` | lokale Entwicklung |
| `"postgres"` | DSN aus `conf.models.postgres_dsn`, z. B. `postgresql+pg8000://user:pw@host:5432/db` | Produktion |

!!! note "Postgres kennt kein in-memory"
    In-memory ist ein SQLite-Feature (`sqlite://`). Postgres läuft immer
    gegen einen Server-Prozess — ein „in-memory Postgres" wäre
    Infrastruktur (tmpfs, Testcontainer), keine Connection-URL. Für
    schnelle Tests ist deshalb `engine = "memory"` (SQLite) das Preset.

Für `"postgres"` muss das Treiber-Paket installiert sein (`pg8000` oder
`psycopg`). Zusätzliche `create_engine`-Argumente laufen über
`conf.models.engine_options` — auf App Engine z. B. der
Cloud-SQL-Connector:

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

Der Pool-Default für URL-Verbindungen bleibt `NullPool`
(serverless-freundlich); das `memory`-Preset überschreibt ihn mit
`StaticPool`. Beides lässt sich über `engine_options["poolclass"]`
übersteuern. Wer die conf nicht nutzen will, kann weiterhin direkt
`viur.models.db.configure(url_or_engine, **kwargs)` aufrufen.

## Development setup

Unit suite (mocked core, 100 % coverage gate):

```bash
pip install --no-deps -e .
pip install pytest pytest-cov 'coverage[toml]' viur-light-mock
pytest
```

Integration suite (real core, no coverage gate):

```bash
pip install "viur-core>=3.8,<4" rsa pytest
pip install --no-deps -e .
python -m pytest -c integration/pytest.ini integration
```

Docs preview:

```bash
pip install mkdocs-material 'mkdocstrings[python]' viur-light-mock
mkdocs serve
```
