# Migrationen

`create_all()` setzt ein Schema auf und ändert nie eines — geänderte Spalten,
entfernte Felder, neue Indizes und geänderte Typen werden stillschweigend
ignoriert. viur-models bindet deshalb [Alembic](https://alembic.sqlalchemy.org/)
ein:

```bash
pip install "spltz-viur-models[migrations]"
```

## Projektlayout

Das Scaffold wird **erzeugt** — `migrations=` an
[`viur.models.setup`][viur.models.setup] übergeben, und der Dev-Server
schreibt beim Boot, was fehlt, und bringt die Datenbank auf Stand:

```python
# deploy/main.py, nach core.setup()
viur.models.setup(migrations=models_db.PROJECT_ROOT)
```

Das ersetzt ein einmaliges `alembic init`: Fehlende Scaffold-Dateien werden
neu erzeugt, bestehende nie überschrieben
([`viur.models.scaffold`][viur.models.scaffold]). Das Scaffold liegt im
**Projekt-Root**, neben dem Distributionsordner:

```
myproject/
  alembic.ini
  migrations/
    env.py              # ~4 Zeilen, siehe unten
    script.py.mako
    versions/           # die Revisionen — einchecken, das ist Code
  deploy/               # der Distributionsordner = was deployt wird
    models_db.py        # die DB-Settings — framework-frei, von BEIDEN Seiten gelesen
    models/             # die Modelle
    main.py
```

!!! note "Warum außerhalb von `deploy/`"
    Revisionen wendet ein Deploy-Schritt oder die Hand an, nie die laufende
    Anwendung — die über [`viur.models.setup`][viur.models.setup] nur ihren
    Schema-Zustand berichtet. Ein Scaffold innerhalb von `deploy/`
    funktioniert ebenso, mit `prepend_sys_path = %(here)s`.

## Was erzeugt wird

### `alembic.ini`

Zwei Einträge sind relevant, der Rest kann bleiben.

```ini
[alembic]
script_location = %(here)s/migrations

# Legt den Distributionsordner auf sys.path, damit env.py das
# ``models``-Paket und ``models_db`` importieren kann. Liegt das Scaffold
# in deploy/, ist das schlicht %(here)s.
prepend_sys_path = %(here)s/deploy

# Absichtlich leer — resolve_url() unten entscheidet.
sqlalchemy.url =
```

### `env.py`

Die Projektdatei sagt, *welche* Modelle zu migrieren sind und *wo* die
Datenbank standardmäßig liegt; alles andere ist `viur.models.migrations`:

```python
from viur.models import migrations
import models_db

# Füllt SQLModel.metadata. Ohne das sieht Autogenerate ein leeres Schema
# und erzeugt eine Migration, die JEDE TABELLE LÖSCHT.
migrations.import_models("models")

migrations.run(fallback_url=models_db.url())
```

!!! warning "`env.py` darf viur-core nicht brauchen"
    `alembic` läuft aus einer Shell ohne App-Engine-Stack — daher die
    Settings in einem einfachen Modul (`models_db.py`), nicht hinter
    `conf.models`.

### `script.py.mako`

Trägt `import sqlmodel`: Autogenerate rendert SQLModels Spaltentypen mit
vollem Pfad (`sqlmodel.sql.sqltypes.AutoString(length=50)`).

## Woher die Datenbank-URL kommt

[`resolve_url`][viur.models.migrations.resolve_url], das Explizitere zuerst:

| # | Quelle | Einsatz |
|---|---|---|
| 1 | `alembic -x url=…` | einmaliger Override |
| 2 | `$VIUR_MODELS_DSN` | CI, Deploy-Schritte |
| 3 | die in diesem Prozess konfigurierte Engine | Migration aus einer gebooteten App oder einer Test-Fixture |
| 4 | das `conf.models`-Preset | nach einem App-Boot |
| 5 | `fallback_url` aus `env.py` | der Projekt-Default |
| 6 | `sqlalchemy.url` in `alembic.ini` | Fixierung auf eine Datenbank |

Ein *fehlkonfiguriertes* Preset (`postgres` ohne DSN) wirft, statt
durchzufallen.

`alembic -x db=<name>` migriert eine weitere Datenbank aus
`conf.models.databases`: URL aus deren Engine bzw. Eintrag (1 gilt weiterhin,
2, 5 und 6 nicht), Autogenerate nur über die Tabellen ihrer Models. Jede
Datenbank braucht ein eigenes Alembic-Verzeichnis.

## Die erste Revision

Ein frisch erzeugtes Scaffold bekommt eine autogenerierte Revision, sofort
angewendet:

| Ausgangspunkt | Was passiert |
|---|---|
| Leere Datenbank | Die Revision legt jede Tabelle an; `upgrade head` wendet sie an. |
| Datenbank, die die Tabellen schon hat (ein früheres `create_all()`) | Die Revision wird gegen eine *Wegwerf-Leerdatenbank* autogeneriert, beschreibt also weiterhin das volle Schema — und die echte Datenbank wird **gestempelt**, nicht migriert. Nichts wird gelöscht, nichts neu angelegt. |

Gegen die befüllte Datenbank autogeneriert ergäbe eine **leere** Revision,
die das Schema nie wieder aufbauen könnte — daher die Wegwerf-Datenbank.
`initial_revision=False` überlässt Dir die erste Revision.

## Täglicher Gebrauch

Aus dem Verzeichnis ausführen, in dem `alembic.ini` liegt — im obigen Layout
der Projekt-Root:

```bash
alembic revision --autogenerate -m "add slug to entry"
alembic upgrade head
alembic current
alembic downgrade -1
alembic check          # stimmen Modelle und Schema noch überein?
```

!!! tip "Die erzeugte Revision immer lesen"
    Eine **Spalten**-Umbenennung ist für Alembic Drop + Add — von Hand auf
    `op.alter_column(..., new_column_name=…)` korrigieren. (Eine
    *Relations*-Umbenennung beim Wechsel multiple↔single **wird** gepaart,
    siehe unten.)

`alembic check` ist das CI-Gate: Es schlägt fehl, wenn ein Model geändert
wurde, ohne dass es eine passende Revision gibt.

## Bone-Übergänge werden für Dich erzeugt

Im Skeleton-Projekt wird `multiple=True → False` beim Lesen konvertiert
(`BaseBone.unserialize` nimmt `loadVal[0]`); in SQL ist es eine
Link-Tabelle, die zu einer FK-Spalte wird — Daten, die Alembic zuerst löschen
würde. viur-models erkennt diese Übergänge und erzeugt die Datenmigration
nach **viur-cores eigenen Regeln**:

| Änderung | Erzeugt | Regel und ihre Herkunft |
|---|---|---|
| neues Feld | `fill_column` (nur wenn Pflicht) | `getDefaultValue`, aus dem Default des Models |
| Feld entfernt | schlichtes `drop_column` | der Verlust ist gewollt |
| neues Feld im Link-Model (`using`) | `fill_column` auf der Link-Tabelle | wie ein neues Feld |
| `str` ↔ `Text` | `coerce_text` | `StringBone.type_coerce_single_value`, **kürzt nie** |
| numerische Precision | `coerce_numeric` | `NumericBone._convert_to_numeric` |
| multiple ↔ single | `collapse_multiple` / `expand_multiple` | `loadVal[0]` — *„nimm den ersten"*; `expand_multiple` füllt `NOT NULL`-Payload-Spalten aus den Model-Defaults (`Ellipsis`-Stub, wenn keiner) |
| mehrsprachig ↔ einsprachig | `reduce_languages` / `expand_languages` | siehe die Asymmetrie unten |
| `select` → `bool` | `coerce_bool` | `parse.bool` mit `conf.bone_boolean_str2true` |
| `bool` → `select` | `remap_values`-**Stub** | viur-core hat keine Regel — siehe unten |

Die erzeugte Revision enthält die Datenmigration bereits:

```python
def upgrade() -> None:
    with op.batch_alter_table("post") as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String(60), nullable=True))

    op.coerce_text("post", "body", new_type=sa.String(), nullable=False)
    op.fill_column("post", "slug", "", nullable=False)
    op.reduce_languages("post", "title", new_type=sa.String(200),
                        languages=["de", "en"], keep="de", nullable=True)
    op.collapse_multiple("post", link_table="post_tag", target_column="tag_id",
                         link_parent_fk="post_id", link_dest_fk="tag_id",
                         foreign_table="tag", target_type=sa.Integer(), keep="first")
```

Autogenerate erklärt jede Entscheidung auf stdout; die Zeilenzahl einer
Reduktion meldet erst der Lauf der Revision:

```
viur-models: post.title: multilingual -> single value (keeps de)
viur-models: post.tags -> tag: multiple -> single (keeps the first target)
viur-models: post.tag_id: 3 row(s) had several targets — kept the first one, like the bone does
```

!!! note "Die beiden Sprachrichtungen sind asymmetrisch"
    Beim Reduzieren bleibt die erste deklarierte Sprache des Feldes
    (Stellvertreter für `conf.i18n.default_language`, das eine Migration
    nicht lesen kann); beim Erweitern landet der Wert unter `languages[0]`.
    Die Asymmetrie ist viur-cores eigene (`BaseBone.unserialize`).

!!! warning "`bool` → `select` braucht eine Zeile von Dir"
    `SelectBone.singleValueUnserialize` sucht über `value`; ein gespeichertes
    `True` trifft nichts. Der Generator schreibt einen Stub mit den
    gespeicherten Labels, und die Revision **verweigert den Start**, bis er
    gefüllt ist:

    ```python
    op.remap_values("post", "flag", {True: ..., False: ...},
                    new_type=sa.Enum("YES", "NO", name="kind"))
    ```

    `select` → `bool` läuft automatisch (`parse.bool`).

Eine Umbenennung beim Wechsel (`tags` → `tag`) wird über die Zieltabelle
gepaart; nur eine mehrdeutige Paarung (mehrere Relationen auf dasselbe Ziel)
fällt auf Add/Remove zurück.

### Wie die Erkennung funktioniert

Alembic sieht nur DDL: `str` → `Text` lässt `max_length` weg, was sein
Typvergleich als „keine Meinung" liest (nichts gemeldet); `multiple` →
single sind zwei zusammenhanglose Operationen. Die Erkennung läuft deshalb
auf **Structure-Snapshots** (`structure_for_model()` pro Tabellen-Model, bei
jedem Autogenerate nach `migrations/structures/<revision>.json` geschrieben),
verglichen mit dem Snapshot der Eltern-Revision.

Die Snapshots einchecken. Ein fehlender ergibt einen leeren Diff — sie
nachträglich einzuführen ist gefahrlos. Basis für eine bestehende Revision:

```python
from viur.models import migrations, schema

migrations.import_models("models")
schema.save(schema.snapshot_dir("migrations"), "<head revision>", schema.snapshot())
```

## Eigene Spaltentypen

[`render_item`][viur.models.migrations.render_item] rendert einen
`TypeDecorator` als sein `impl` — [`RecordJSON`][viur.models.RecordJSON] wird
`sa.JSON()`: Eine Revision ist ein eingefrorener Schnappschuss und darf keine
Modellklasse importieren. SQLModels eigene Decorator (`AutoString` & Co.)
bleiben Alembic überlassen.

## Was wann läuft

Die Anwendung erzeugt und migriert das Schema nie.
[`viur.models.setup`][viur.models.setup] berichtet die Revision über
[`schema_revision`][viur.models.db.schema_revision] (reines SQL, kein
Alembic-Import) und ruft `create_all()` nur beim `memory`-Preset.

!!! danger "Niemals beim Instanz-Start migrieren"
    Mehrere App-Engine-Instanzen migrierten gleichzeitig dieselbe Datenbank.
    Migrationen sind ein einmaliger Schritt vor dem Rollout.

## Eine bestehende Datenbank übernehmen

Eine mit `create_all()` aufgesetzte Datenbank hat keine
`alembic_version`-Tabelle. [`setup(migrations=…)`](#die-erste-revision)
übernimmt sie beim nächsten Dev-Server-Boot ohne DDL. Von Hand — eine
Revision, die das Schema beschreibt, muss vorher existieren, `stamp`
markiert nur:

```bash
alembic stamp head
alembic check      # bestätigt, dass es wirklich passt
```

## SQLite

Für SQLite-URLs ist der Batch-Modus an (`with op.batch_alter_table(...)` in
den Revisionen) — SQLite kann die meisten Dinge nicht per `ALTER` ändern.
