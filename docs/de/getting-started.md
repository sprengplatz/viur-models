# Erste Schritte

## Installation

```bash
pip install spltz-viur-models
```

Benötigt Python ≥ 3.12 und viur-core ≥ 3.8, < 4.

## Beispiel: Model und Modul

Ein Model ist die Tabelle, ein `SQLList`-Modul bedient sie. Beides liegt im
Distributionsordner neben den Skeletons — SQL-Module und Datastore-Module
existieren nebeneinander:

```
deploy/
  models/
    entry.py         # Model-Klassen: die Tabellen (statt skeletons/*.py)
  modules/
    entry.py         # SQLList-Module: die Endpunkte (statt List-Prototyp)
  skeletons/         # Datastore-Skeletons wie bisher
  main.py            # install() vor, setup() nach core.setup()
```

### Datenmodell

=== "Skeleton"

    ```python
    # skeletons/entry.py
    from viur.core.bones import SelectBone, StringBone
    from viur.core.skeleton import Skeleton

    class EntrySkel(Skeleton):
        kindName = "entry"

        name = StringBone(descr="Name", required=True, max_length=100)
        kind = SelectBone(descr="Art", values={"praise": "Lob", "complaint": "Beschwerde"})
    ```

=== "Model"

    ```python
    # models/entry.py
    import enum
    from viur.models import Field, Model

    class EntryKind(enum.Enum):                          # select: Member-Namen werden Labels
        PRAISE = "praise"
        COMPLAINT = "complaint"

    class Entry(Model, table=True):
        __tablename__ = "entry"                          # = kindName

        name: str = Field(descr="Name", max_length=100)          # required: kein None, kein Default
        kind: EntryKind | None = Field(default=None, descr="Art")  # optional: | None + default=None
    ```

`id`, `creationdate` und `changedate` kommen aus `Model` (ausgegeben als
`key`-Bone und readonly Compute-Daten). Die vollständige Feldpalette steht in
`deploy/models/example.py`, jede Bone einzeln in der
[Bone-Referenz](bones.md).

### Modul

=== "Skeleton"

    ```python
    # modules/entry.py
    from viur.core.prototypes import List

    class Entry(List):
        kindName = "entry"

        def canAdd(self):
            return True

        def onAdded(self, skel):
            ...
    ```

=== "Model"

    ```python
    # modules/entry.py
    from viur.models.sqllist import SQLList
    from models.entry import Entry as EntryModel

    class Entry(SQLList):
        model = EntryModel

        def canAdd(self, instance):          # can<X>: fail-closed, wenn nicht überschrieben
            return True

        def thenAdd(self, instance):         # then<X>: nach dem Commit (onAdded-Pendant)
            ...
    ```

`SQLList` bedient `list`/`view`/`add`/`edit`/`delete`/`structure` über Envelope
v2; der Name, unter dem das Modul gemountet ist, wird das `module` der
relationalen Bones, die auf dieses Model zeigen. Für den Admin ist es ein
`list`-Handler wie jeder andere.

Wie der Core liefert `SQLList` nur, was der Client anfragt: Schickt er
`X-VIUR-BONELIST: name,kind` (der Admin tut das, wenn `adminInfo` `"bonelist"`
setzt), fragen `list`/`view` nur diese Spalten und Relationen ab (`load_only`,
`selectinload` nur für angefragte Relationen), und Dump wie Structure enthalten
nur diese Bones. `key` ist immer dabei, `Model.viur_bones_always` benennt
weitere Pflicht-Bones (das `"*"`-Subskel-Pendant). Der Core unserialisiert eine
Bone erst beim Zugriff — hier wird sie gar nicht erst gelesen.

### Was entspricht was

| viur-core | viur-models |
|---|---|
| `Skeleton`, `kindName` | `Model`, `__tablename__` |
| Bone-Klasse `StringBone(...)` | Python-Typ + `Field(...)` |
| `required=True` | Typ ohne `None`, ohne Default |
| `multiple=True` | `list[...]` + Link-Tabelle (`Relationship(link_model=…)`) |
| `RelationalBone(kind=…)` | FK-Feld + `Relationship()` — **eine** Bone im API |
| `RelationalBone(using=RelSkel)` | Association-Object von `RelationLink` |
| `RecordBone(using=RelSkel)` | `Record` + `RecordJSON`-Spalte |
| `UserBone`, `FileBone`, `RelationalBone` auf ein Skeleton | `UserRef()`, `FileRef()`, `SkeletonRef(kind)` — Cross-Store |
| `key`, `creationdate`, `changedate` | automatisch aus `Model` |
| `List` | `SQLList` |
| `viewSkel()` / `addSkel()` … | `viewSkel()` / `addSkel()` … liefern die Model-Klasse |
| `canAdd()` / `onAdd(skel)` / `onAdded(skel)` | `canAdd(instance)` / `onAdd(instance)` / `thenAdd(instance)` |
| `listFilter(query)` | `sqlFilter(stmt)` |
| `skel.fromClient(data)` | `Model.viur_from_client(data)` |
| `skel.fromDB()` / `skel.toDB()` | SQLAlchemy-Session (`db.get_session()`), von `SQLList` verwaltet |
| `skel.structure()` / `skel.dump()` | identisch — `structure()` / `dump()` |
| Datastore-Key | opaker `viur_key` (Tabelle + Primärschlüssel, base64) |
| `db.Query`, Cursor | SQLAlchemy `select`; `list` versteht Filter, `orderby`, `cursor` wie core |
| Schema-Änderung: Bone ändern, fertig | Alembic-Revision — siehe [Migrationen](migrations.md) |

## App-Boot: `install()` und `setup()`

Zwei Aufrufe in der `main.py` des Projekts, links und rechts von
`core.setup()`:

```python
from viur import core
import viur.models

# vor core.setup(): conf.models, Engine, Cross-Store-Refresh-Hooks
viur.models.install(
    engine="sqlite",                  # "memory" | "sqlite" | "postgres" | "bigquery"
    sqlite_file="viur_models.sqlite3",
)

import modules, render
app = core.setup(modules, render)

# nach core.setup() — Modelle importiert: Schema berichten ("memory": create_all)
viur.models.setup(migrations=PROJECT_ROOT)
```

Das optionale `migrations=` erzeugt auf dem Dev-Server das Alembic-Scaffold
(`alembic.ini` + `migrations/`), falls es fehlt, und bringt die Datenbank
gleich auf Stand — siehe [Migrationen](migrations.md). Ohne das
Argument berichtet `setup()` nur den Schema-Zustand.

`install()` ignoriert jedes Argument, das `None` bleibt — bereits gesetzte
`conf.models`-Werte überleben einen Teilaufruf also. `refresh_hooks=False`
schaltet die Hooks ab (für Anwendungen ohne Cross-Store-Referenzen);
zusätzliche Keyword-Argumente (`missing_on_delete`, `countdown`) reicht
`install()` an
[`install_refresh_hooks`][viur.models.install_refresh_hooks] durch.

Die einzelnen Schritte bleiben verfügbar — `install()` bündelt sie nur:

```python
viur.models.install_config()          # registriert conf.models (idempotent)
conf.models.engine = "sqlite"
conf.models.sqlite_file = "viur_models.sqlite3"
viur.models.db.configure_from_conf()
viur.models.install_refresh_hooks()
```

## Die Presets in `conf.models`

| Preset | Verbindung | Einsatz |
|---|---|---|
| `"memory"` | SQLite in-memory (`sqlite://`, eine geteilte Verbindung via `StaticPool`, damit alle Sessions dieselbe Datenbank sehen) | Tests, Demos |
| `"sqlite"` | SQLite-Datei aus `conf.models.sqlite_file` | lokale Entwicklung |
| `"postgres"` | DSN aus `conf.models.postgres_dsn`, z. B. `postgresql+pg8000://user:pw@host:5432/db` | Produktion |
| `"bigquery"` | DSN aus `conf.models.bigquery_dsn` — siehe [BigQuery](bigquery.md) | analytische Daten |

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

Der Pool-Default für URL-Verbindungen ist `NullPool`; das `memory`-Preset
überschreibt ihn mit `StaticPool`. Beides lässt sich über
`engine_options["poolclass"]` übersteuern. Ohne die conf ruft man
`viur.models.db.configure(url_or_engine, **kwargs)` direkt auf.

## Entwicklungsumgebung

Unit-Suite — **Overlay-Modus** von viur-light-mock (echter viur-core,
In-Memory-Datastore), 100-%-Coverage-Gate.

```bash
pip install --no-deps -e .
pip install pytest pytest-cov 'coverage[toml]' 'spltz-viur-light-mock>=0.3,<1.0' 'viur-core>=3.8,<3.9' 'spltz-viur-actions>=0.4,<1.0' sqlmodel pydantic-extra-types pycountry email-validator 'alembic>=1.13'
pytest
```

Integrations-Suite — echter Core ohne Mock (echte Skeleton-Registry):

```bash
pip install "viur-core>=3.8,<3.9" rsa pytest 'spltz-viur-actions>=0.4,<1.0' sqlmodel pydantic-extra-types pycountry email-validator 'alembic>=1.13'
pip install --no-deps -e .
python -m pytest -c integration/pytest.ini integration
```

Doku — die Overlay-Installation von oben plus:

```bash
pip install mkdocs-material 'mkdocstrings[python]' mkdocs-static-i18n
mkdocs serve
```

Die verifizierten Install-Zeilen stehen in `.github/workflows/`.
