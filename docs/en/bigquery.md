# BigQuery

`SQLList` serves a [`BigQueryModel`][viur.models.bigquery.BigQueryModel]
over the same endpoints and envelope as any other model (verified against the
BigQuery emulator). BigQuery is an OLAP warehouse; three properties cannot be
abstracted away.

## The three compromises

**No transactions.** `session.rollback()` is a no-op; a failed action leaves
its earlier statements applied. The engine warns once at configure time.

**Latency and DML quotas.** Every action is at least one query job (seconds),
and mutating DML per table is limited to a few concurrent jobs — low-traffic
admin modules only.

**No autoincrement, no enforced keys.**
[`BigQueryModel`][viur.models.bigquery.BigQueryModel] generates time-ordered
26-char string ids (ULID layout), so `ORDER BY id` and keyset pagination keep
working. Uniqueness and foreign keys are enforced by the application alone.

## Setup

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

# deploy/modules/sales.py — an ordinary SQLList
class Sales(SQLList):
    model = SalesRow
    json_version = 2

# deploy/main.py
conf.models.engine = "bigquery"
conf.models.bigquery_dsn = "bigquery://my-project/analytics"
viur.models.db.configure_from_conf()
```

Credentials: Application Default Credentials on App Engine; a preconfigured
client via `conf.models.engine_options = {"connect_args": {"client": client}}`
(tests, emulator).

## What the backend handles

| Problem | Handling |
|---|---|
| no autoincrement | `BigQueryModel.id`: time-ordered string via [`new_id`][viur.models.bigquery.new_id] |
| aware datetimes vs. naive `DATETIME` | the system fields are `TIMESTAMP(timezone=True)` columns |
| `LIKE` has no `ESCAPE` clause | `SQLList` builds search/`$lk` filters dialect-aware (backslash is BigQuery's implicit escape) |
| DML jobs don't report matched rows | rowcount verification disabled at configure time — otherwise a **working** UPDATE raises `StaleDataError` |

Declare your own timezone-aware datetime fields explicitly — BigQuery's
`DATETIME` is naive and rejects aware values:

```python
due: datetime | None = Field(default=None, sa_type=sa.TIMESTAMP(timezone=True))
```

## Migrations: additive only

Real BigQuery supports `ADD/DROP/RENAME COLUMN` and widening type
coercions. There is **no batch mode**, and the row-wise
`transform_column` operations (multiple↔single, language changes, …)
collide with DML quotas — do not point the bone-transition generator at a
BigQuery DSN. Bootstrap the schema with `SQLModel.metadata.create_all()`
and keep changes additive.

## Verifying changes

`integration/bq_emulator_check.py` runs the CRUD path against the
[BigQuery emulator](https://github.com/goccy/bigquery-emulator) (Docker);
its docstring lists the emulator's own limitations (reflection and
`ALTER TABLE` hang there).
