# BigQuery

`SQLList` bedient ein [`BigQueryModel`][viur.models.bigquery.BigQueryModel]
über dieselben Endpunkte und denselben Envelope wie jedes andere Model
(verifiziert gegen den BigQuery-Emulator). BigQuery ist ein OLAP-Warehouse;
drei Eigenschaften lassen sich nicht wegabstrahieren.

## Die drei Kompromisse

**Keine Transaktionen.** `session.rollback()` ist ein No-op; eine
gescheiterte Action lässt ihre früheren Statements angewendet zurück. Die
Engine warnt einmalig beim Konfigurieren.

**Latenz und DML-Kontingente.** Jede Action ist mindestens ein Query-Job
(Sekunden), mutierendes DML pro Tabelle ist auf wenige gleichzeitige Jobs
begrenzt — nur für Admin-Module mit wenig Verkehr.

**Kein Autoincrement, keine erzwungenen Keys.**
[`BigQueryModel`][viur.models.bigquery.BigQueryModel] erzeugt zeitlich
sortierte 26-Zeichen-String-IDs (ULID-Layout), `ORDER BY id` und
Keyset-Pagination funktionieren weiter. Eindeutigkeit und Fremdschlüssel
erzwingt allein die Anwendung.

## Einrichtung

```bash
pip install "spltz-viur-models[bigquery]"
```

```python
# deploy/models/stats.py
from viur.models.bigquery import BigQueryModel

class SalesRow(BigQueryModel, table=True):
    __tablename__ = "sales"
    __table_args__ = {"schema": "analytics"}    # schema == dataset
    name: str = Field(descr="Name", max_length=100)

# deploy/modules/sales.py — eine ganz normale SQLList
class Sales(SQLList):
    model = SalesRow
    json_version = 2

# deploy/main.py
conf.models.engine = "bigquery"
conf.models.bigquery_dsn = "bigquery://my-project/analytics"
viur.models.db.configure_from_conf()
```

Credentials: Application Default Credentials auf App Engine; ein
vorkonfigurierter Client über
`conf.models.engine_options = {"connect_args": {"client": client}}` (Tests,
Emulator).

## Was das Backend übernimmt

| Problem | Behandlung |
|---|---|
| kein Autoincrement | `BigQueryModel.id`: zeitlich sortierter String über [`new_id`][viur.models.bigquery.new_id] |
| aware Datetimes vs. naives `DATETIME` | die Systemfelder sind `TIMESTAMP(timezone=True)`-Spalten |
| `LIKE` hat keine `ESCAPE`-Klausel | `SQLList` baut Such- und `$lk`-Filter dialektabhängig (Backslash ist BigQuerys implizites Escape) |
| DML-Jobs melden keine getroffenen Zeilen | Rowcount-Prüfung beim Konfigurieren abgeschaltet — sonst wirft ein **funktionierendes** UPDATE `StaleDataError` |

Eigene zeitzonenbewusste Datetime-Felder musst Du explizit deklarieren —
BigQuerys `DATETIME` ist naiv und lehnt aware Werte ab:

```python
due: datetime | None = Field(default=None, sa_type=sa.TIMESTAMP(timezone=True))
```

## Migrationen: nur additiv

Echtes BigQuery unterstützt `ADD/DROP/RENAME COLUMN` und erweiternde
Typumwandlungen. Es gibt **keinen Batch-Modus**, und die zeilenweisen
`transform_column`-Operationen (multiple↔single, Sprachwechsel, …)
kollidieren mit den DML-Kontingenten — richte den
Bone-Übergangsgenerator nicht auf ein BigQuery-DSN. Das Schema mit
`SQLModel.metadata.create_all()` aufsetzen und Änderungen additiv halten.

## Änderungen prüfen

`integration/bq_emulator_check.py` fährt den CRUD-Pfad gegen den
[BigQuery-Emulator](https://github.com/goccy/bigquery-emulator) (Docker);
sein Docstring listet die Einschränkungen des Emulators (Reflection und
`ALTER TABLE` hängen dort).
