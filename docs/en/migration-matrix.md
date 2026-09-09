# Migration matrix

What happens when you change a SQL model: what migrates automatically,
where data is reduced, and the cases that need manual work.

All commands from the directory holding `alembic.ini`:

```bash
# 1. change the model, then:
alembic revision --autogenerate -m "tags single"
# 2. read the generated file — autogenerate spells out every decision
alembic upgrade head
alembic check          # models == schema?
```

## The five classes

| Class | Meaning |
|---|---|
| **Fully automatic** | Generate, read, apply. No data is lost. |
| **Automatic, reduced** | Runs unattended but discards data — and reports how many rows are affected. |
| **One line from you** | The generator writes a stub; the revision refuses to run until it is filled in. |
| **Manual** | Not detected, or not generated. Correct the revision by hand. |
| **No revision** | No schema and no data involvement. Autogenerate reports "no changes". |

---

## Fully automatic (14 cases)

Nothing to do but read the revision.

| Change | Generates | Note |
|---|---|---|
| New optional field | `add_column` | Alembic standard. |
| New required field | `add_column` (nullable) + `fill_column` | The fill value comes from the model itself — `default`, otherwise `emptyvalue`. Without the three-step, it fails with `Cannot add a NOT NULL column`. |
| Field removed | `drop_column` | The loss is intended. |
| New model | `create_table` | Including FKs and composite PKs, in dependency order. |
| Index added or removed | `create_index` / `drop_index` | From `Field(index=…)`. |
| Unique constraint added | `create_unique_constraint` | The constraint is named automatically — SQLite rejects unnamed ones in batch mode. On populated tables, mind the pitfall below. |
| New field on the link model (`using`) | `add_column` + `fill_column` | On the link table, otherwise like any new field. |
| `str` ↔ `Text` | `coerce_text` | **Alembic alone does not see this change at all.** `Text` drops `max_length`, and Alembic reads "no length" as "no opinion" — the column would silently keep its old limit. |
| `max_length` increased | `alter_column` | Lossless widening. |
| `int` → `float` | `coerce_numeric` | Rounds to the new precision (`round(float(v), p)`). |
| single → multiple | `expand_multiple` | Lossless: one link row per existing value. Also across a rename `tag` → `tags`. `NOT NULL` payload columns of an association link are filled from the model default (`payload_defaults`; `Ellipsis` stub if there is none). |
| monolingual → multilingual | `expand_languages` | The value lands under `languages[0]`; the other languages stay empty. |
| Enum/select option added | `add_enum_values` | **Postgres needs this** (`ALTER TYPE … ADD VALUE`, otherwise `invalid input value for enum` at runtime); on SQLite the column is an unconstrained VARCHAR and the op is a no-op. |
| Select → `bool` | `coerce_bool` | The textual rule from `parse.bool`: `"yes"` becomes `True`, `"active"` becomes `False`. |

---

## Automatic, but reduced (4 cases)

Runs unattended. Which value survives is the same choice viur-core makes on
read — and it is visible in the revision, so it can be corrected.

| Change | Generates | What survives |
|---|---|---|
| multiple → single | `collapse_multiple` | The **first** entry, like `loadVal[0]` in the bone. Reported when applied: `3 row(s) had several targets`. Also across a rename `tags` → `tag`. |
| multilingual → monolingual | `reduce_languages` | One language, visible as `keep='de'` in the revision. Defaults to the first declared language. |
| `float` → `int` | `coerce_numeric` | **Truncates, does not round** — `3.7` becomes `3`. That is the bone rule. |
| `max_length` decreased | `alter_column` | Existing values are **not** truncated — no more than the bones do. Postgres may then reject the change on commit; SQLite will not. |

---

## One line from you (1 case)

The only transition viur-core has no usable rule for:
`SelectBone.singleValueUnserialize` looks for an enum member with a matching
`value`, and a stored `True` matches nothing. In the schemaless datastore
that goes unnoticed; a typed column cannot hold it.

| Change | Generates | What you do |
|---|---|---|
| `bool` → select | `remap_values` with `{True: ..., False: ...}` | Fill in the dict — **with the stored labels** (enum member *names* such as `YES`) that the autogenerate output shows, not the client values (`yes`): the column stores the name, the wire format the value. The revision **refuses to run** while `Ellipsis` is in there, rather than quietly writing something plausible. |

---

## Manual (5 cases)

Nothing is guessed here. For the first two there is nothing to detect; for
the last three the column stays technically valid, only its content no
longer fits.

| Change | Autogenerate | What you do |
|---|---|---|
| Column renamed | drop + add — loses data | Correct it by hand to `alter_column(…, new_column_name=…)`. No tool detects renames; only the *relation* switch multiple↔single is paired up. |
| Table renamed | drop + create | Correct it by hand to `op.rename_table(…)`. |
| Record field added or removed (`Record`) | nothing | The record lives in a JSON column that does not change. The stored JSON objects keep their old shape. If that matters, write your own `op.execute(…)` migration. |
| Enum option removed | warning, no op | Rows holding the old value stay, and Postgres cannot drop an enum value (the type would have to be rebuilt). Rewrite them with `op.remap_values(…)`; the generator warns. |
| Relation target changed | FK switch, no data migration | The FK values point at ids of the old target table. There is no derivable mapping — rewriting is project-specific. |

---

## No revision needed (3 cases)

Autogenerate reports "no changes in schema detected" — correctly.

| Change | Why |
|---|---|
| `descr`, `visible`, `readonly`, `params`, `format` | Pure client metadata. They change the bone structure but neither schema nor data — the admin sees them on the next request. |
| `@computed_field` added or removed | Has no column. The value is computed on dump. |
| `viur_ref_keys` changed | Affects the `dest` snapshots of cross-store references, not the schema. Refresh with `refresh_crossstore(Model)` or `refresh_for_target(key)`. |

---

## Three rules that surprise

All three are viur-core's rules.

**Languages are asymmetric.** Going *down* uses
`conf.i18n.default_language`, going *up* uses `languages[0]` — the first
**declared** language. Those are not the same. And when the wanted language
is present as a key, its value wins even when it is empty.

**`float` → `int` truncates.** `int(float(3.7))` is `3`, not `4`. Only
`precision > 0` rounds. Both are `NumericBone._convert_to_numeric`.

**Bones never truncate.** `max_length` is validated in `fromClient` only,
never on read. A reduced limit therefore leaves long existing values in
place — `coerce_text` does the same.

---

## Pitfalls

**Required field + UNIQUE on a populated table.** One fill value for every
row violates `UNIQUE` as soon as more than one row exists. The generator
cannot invent unique values, but it **warns beforehand** instead of failing
on commit. Replace the `fill_column` value with per-row values.

**Aborted migration on SQLite.** Every batch block rebuilds the table
through `_alembic_tmp_<table>`. If a migration fails halfway, that table
stays behind and blocks the next attempt with
`table _alembic_tmp_x already exists` — drop it by hand. Inherent to
Alembic's batch mode.

**Snapshots belong in the repository.** `migrations/structures/<revision>.json`
holds the bone structure per revision — the basis for detection. Without a
snapshot, no transition is detected, silently and without an error.
Introducing them later is harmless: a missing snapshot yields an empty diff.

**Never migrate on instance start.** `main.py` no longer creates a schema,
it only reports the revision. With several App Engine instances, several
migrations would otherwise run against the same database at once.

---

Rules checked against viur-core 3.9 · Alembic ≥ 1.13 · verified on SQLite
**and** Postgres 16. In depth: [migrations.md](migrations.md) · runbook:
`migrations/README`.
