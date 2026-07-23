# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Duplicate entries in multiple relations reject instead of silently
  collapsing.** Submitting the same target twice (e.g. one marker two
  times) passed validation, but the SQL link table cannot represent the
  duplicate — its composite primary key swallowed one entry without any
  feedback. Duplicates are now forbidden **by default** (core parity:
  `MultipleConstraints.duplicates` defaults to `False`), with or without
  declared constraints — the client gets "Duplicate entries are not
  allowed" on the bone. `viur_relation_meta = {"rel": {"multiple":
  {"duplicates": True}}}` still opts in explicitly, but plain link tables
  and composite-key association links cannot store duplicates either way.

- **Indexed dotted client input parses** — vi/admin4 posts multiple
  records as `stops.<idx>.<field>=…` and relations/references WITH a
  using payload as `tags.<idx>.key=…` + `tags.<idx>.<payload>=…`;
  `viur_from_client` silently dropped both shapes (multiple records never
  saved). They now rebuild in index order — records as value dicts,
  relational entries in the bone's wire shape (`{"dest": {"key": …},
  "rel": {…}}`). Empty fields inside using payloads fall back to their
  defaults (the link row is validated as a whole).

- **Relational bones emit the SERVING module, not the table name.** The
  bone's `module` field is what admin clients query for relation
  selections — it carried the target's kind/table name (e.g.
  `example_marker`), which no admin knows as a module, so selection lists
  stayed empty. `SQLList` now registers "this module serves that model"
  at construction (first module wins; cached structures are dropped so
  they rebuild with the registry), `_relational_structure` emits that
  name — e.g. `module: "sqlexamplemarker"` — and
  `viur_relation_meta = {"rel": {"module": …}}` overrides explicitly.
  The bone's `type` keeps the kind (`relational.example_marker`), like
  core. Unserved targets keep the kind fallback.

- **Empty submissions clear non-string bones** (core-bone parity): HTML
  forms send cleared inputs as `""` — for bones whose emptyvalue is not
  `""` (date/time/datetime, numeric, select, spatial, relational, record,
  …) that now counts as an empty submission and clears the value instead
  of failing pydantic validation: multiple bones to `[]` (e.g. a multiple
  record posted as `""`), everything else to `None`. String-family bones
  keep `""`. The normalization recurses into record values (`zip_code=""`
  inside an address clears like a top-level bone); spatial input is also
  accepted as a `{"lat", "lng"}` dict (JSON clients) and counts as empty
  when both coordinates are empty.
- **Unsubmitted multiple bones clear on edit.** Browsers submit NOTHING
  for an empty multi-selection, so a multiple bone (m2m relations,
  association links, `SkeletonLink`s, multiple cross-store JSON fields,
  multiple records) absent from an edit payload now means "empty" — it
  is no longer merged from the stored dump. The form posts its full
  state; partial API edits must re-send multiple values they want to
  keep.
- **Empty write-only input is ignored** (PasswordBone parity): the form
  renders Password/Credential bones empty (masked), so an unsubmitted or
  empty (`""`) value keeps the stored secret on edit — previously the
  edit merge fed the masked emptyvalue back and **blanked the stored
  password on every edit**. A non-empty submission still overwrites.

- **Rejected forms roundtrip the submitted values.** On validation or
  relation errors, `add`/`edit` re-rendered an *empty* (add) or *stored*
  (edit) instance — every client input was lost. `viur_from_client` now
  returns a best-effort **unvalidated** form instance carrying the
  submitted values on errors (`skel.fromClient` parity — never persist
  it), `add`/`edit` render it (edit keyed like the stored row), and dumps
  roundtrip parked many-to-many selections as key-only dests
  (`_viur_pending_relations` fallback). Invalid raw values dump as
  submitted (e.g. `rating: "99"`); a non-dict raw value on a language
  field dumps as the empty language dict.

- **Opening the add form no longer creates a row.** `SQLList.add`/`edit`
  treated *any* non-empty request as a submit — vi/admin4 opens forms via
  POST with `skey` + `bounce=true`, which slipped into the write path.
  Both actions now mirror core's `List` gate: `bounce` yields a validated
  re-render (review) that never writes, and writes additionally require a
  real POST (`isPostRequest`); outside a request context (library/test
  use) the POST gate is a no-op.

- `SQLList.view` / `edit` / `delete` annotated their `key` parameter as
  `t.Any` — viur-core's Method argument parser has no handler for
  `typing.Any` and rejected every real request with **406 Not Acceptable**
  ("Unhandled type"). Keys are opaque strings on the wire; the parameter
  is now annotated `str`. (Unit tests run with identity decorators and
  never hit the Method parser, which is why this only surfaced through
  real routing.)

### Changed

- Runtime-hook dispatch migrated from viur-actions' removed per-slot
  callers (`call_can` / `call_on` / `call_then` / `call_skel`) to
  `get_hook_method(module, hooks, slot)(…)` — compatible with
  viur-actions 0.2 **and** 0.3. To serve a SQLList module with envelope
  v2 on every mount (admin5 on unversioned `/vi/`), pin it with
  viur-actions' `json_version = 2` class attribute.

### Added

- **`@computed_field` support — the `compute` (method `Always`)
  analogue**: pydantic computed fields emit a read-only bone
  (`compute: {"method": "Always"}`, not indexed — there is no column to
  filter or sort on) whose shape comes from the property's return
  annotation, through the same mapping as regular fields (semantic types
  like `Text` work; a write-only return type such as `Password` keeps
  masking dumps). Values are computed at dump time and never stored;
  client input under a computed name is dropped like any read-only bone.
  Bone parameters travel in the decorator's
  `json_schema_extra={"viur": {...}}`. Computed names also work as
  `viur_ref_keys` (they appear in `dest` snapshots and `relskel`) and
  inside records.

- **`conf.models` engine presets** — viur-typical configuration via
  viur-core's `conf`: `install_config()` registers a `ModelsConfig`
  namespace (idempotent, strict-mode compatible like `conf.actions`),
  and `db.configure_from_conf()` builds the shared engine from
  `conf.models.engine`: `"memory"` (SQLite in-memory over a single
  `StaticPool` connection so all sessions share one database — Postgres
  has no in-memory mode, this preset is SQLite by design), `"sqlite"`
  (`conf.models.sqlite_file`) and `"postgres"`
  (`conf.models.postgres_dsn`, driver package required). Extra
  `create_engine` kwargs — Cloud SQL `creator=…`, `echo`, pool tuning —
  pass through `conf.models.engine_options`; the direct
  `db.configure(url_or_engine)` API stays available.

- **`install_refresh_hooks()` — automatic change propagation**: called
  once at app boot, it wraps `Skeleton.postSavedHandler`/
  `postDeletedHandler` (the seam every skeleton write/delete passes) and
  defers `refresh_for_target` for referenced kinds only — the automatic
  counterpart of core's `update_relations` (delete policy defaults to
  `set_null`, `countdown=10` like core). Skeletons overriding the
  handlers without `super()` need manual `onEdited` wiring (documented).
- **Relations index + targeted refresh** — the `viur-relations`
  analogue: SQLList maintains a reverse index table
  (`viur_models_relations`: target key → referencing table/row/field) on
  every add/edit/delete; `refresh_for_target(key, missing=…)` then
  updates exactly the affected rows (plus `SkeletonLink` tables directly
  over their indexed `key` column) — meant to be wired into the target
  module's `onEdited`/`onDeleted` hooks via `CallDeferred`, like core's
  `updateRelations`. Stale index rows (vanished rows/renamed fields)
  clean themselves up on the way.
- **`refresh_crossstore(*models, missing="keep"|"set_null")`** — the
  `updateRelations` analogue for cross-store references: re-reads every
  referenced datastore target and rewrites stale `dest` snapshots; with
  `set_null`, references to deleted targets clear (single → `None`,
  JSON lists drop the entry, `SkeletonLink` rows are deleted). Meant for
  a project cron/deferred task; `SkeletonLink` tables refresh over their
  indexed `key` column, JSON columns require a table scan.

### Fixed

- A **deleted cross-store target no longer blocks unrelated edits**: the
  edit-merge roundtrips the full `dest` snapshot, and when its target has
  vanished the stored snapshot is kept instead of rejecting the whole
  edit. Actively setting a bare unknown key still rejects.

- Exclusive numeric bounds now feed the structure: `PositiveInt` /
  `conint(gt=…, lt=…)` convert exactly for integers (`gt=0` → `min: 1`,
  `lt=10` → `max: 9`) instead of leaving the int64 defaults; floats keep
  ge/le only (no exact inclusive bound exists). Docs list which pydantic/
  pydantic-extra-types work natively (`ByteSize`, `Epoch.Integer`,
  `Strict*`, str-subclass extras) and the known normalizations
  (`AnyUrl` trailing slash, `PhoneNumber` RFC3966) plus the footguns
  (`SecretStr`, `pydantic.Json`).

### Added

- **Keyset pagination** — the opaque `list` cursor now encodes the last
  row's sort-key values (bound to `orderby`/`orderdir`; a mismatched or
  malformed cursor restarts the listing) and the next page SEEKS past
  them instead of counting an OFFSET: stable under concurrent inserts/
  deletes and O(1) regardless of page depth. Results always have a total
  order (`id` tiebreaker; sort columns order `NULLS LAST`
  backend-independently, the NULL tail paginates correctly); cursor
  values roundtrip through JSON with type coercion (datetimes/Decimals).

- **SQLList query language + search** — `list` now speaks core's filter
  language (pinned against `mergeExternalFilter`/`buildDBFilter`):
  equality (lists → `IN`), the operator suffixes `$lt`/`$le`/`$gt`/`$ge`,
  and `$lk` as case-insensitive prefix match (StringBone semantics; LIKE
  wildcards escaped). Numeric filter values are coerced (unusable ones
  ignored, like core); relations and write-only fields are not
  filterable. `search=<term>` does an OR-`ilike` over all str/text fields
  (write-only and language fields excluded) — without any searchable
  field the query is unsatisfiable, exactly like core without a fulltext
  adapter.

- **Using-relations (edge payload)** — the `RelationalBone(using=RelSkel)`
  analogue as the association-object pattern: derive the link table from
  `RelationLink`; its payload columns ARE the using-skel
  (structure-parity-verified against the real bone). Dumps carry
  `{"dest": …, "rel": {…}}` (dest via the link's to-one relation,
  chain-eager-loaded); client input is the bone's wire shape with
  validated payload (errors keep the `[name, "rel", field]` path);
  replacing the list deletes orphaned rows (`cascade="all,
  delete-orphan"` required). `SkeletonLink` subclasses carry payload
  fields the same way for cross-store references.

- **Cross-store references** (`viur.models.crossstore`) — SQL models can
  reference **datastore skeletons**: `SkeletonRef(kind, ref_keys=…,
  multiple=…, type_suffix=…)` builds a field type whose value is the
  `dest` snapshot (encoded datastore key + ref-key values) in a JSON
  column — the same denormalization the real `RelationalBone` stores.
  `relskel` resolves through the real skeleton registry
  (`RefSkel.fromSkel`, always incl. `key`/`shortkey`) — structure parity
  verified against `RelationalBone`. Client input (opaque datastore key
  or dump shape) reads the target from the datastore, rebuilds the
  snapshot and rejects unknown keys; every edit refreshes it (no
  `updateRelations`-style background refresh in v1). Presets:
  `UserRef()` (UserBone shape incl. its display format) and `FileRef()`
  (reference only — upload stays with the file module). Class-definition
  stays registry-free (structure resolution defers to first use).
  Multiple references come in two storage shapes with identical bone
  structure: a JSON array column (`SkeletonRef(kind, multiple=True)`) or
  **link-table-backed** via a `SkeletonLink` subclass + `Relationship`
  (one queryable row per reference, `cascade="all, delete-orphan"`
  required; bone parameters and `multiple` constraints via
  `viur_relation_meta`).

- **RecordBone mapping — plain pydantic nesting** (structure- and
  dump-parity-verified): `ViURRecord` (a ViURModel without `table=True` and without
  system fields — the `RelSkel` analogue; any plain non-table SQLModel
  works too) is the using-skel; `Address | None` maps to a `record` bone with `using` =
  the nested structure, `list[Address]` to `multiple: True` (with
  `defaultvalue: []`, unindexed like the bone). Validation, nested
  error paths and the dump shape (the plain values dict) come natively
  from pydantic; the new `RecordJSON` column type serializes instances
  to JSON on write and validates them back on read. `ViURField` gained
  a `format=` passthrough (records + relational bones); dotted client
  input (`address.street=…`) works for single records. Table models as
  record targets fail fast (they are relations).
- **v2 feature wave, all structure-parity-verified against the real
  bones:**
    - **`Language[X]` wrapper type** — multilingual fields: the type
      describes the data structure (`{lang: value}` JSON column), the
      bone shape comes from the inner type (`Language[str]`,
      `Language[Text]`); language list via `ViURField(languages=…)` or
      `set_default_languages()`. Dumps normalize to the declared
      languages (incl. the per-language `defaultvalue` dict the real
      bone emits); input accepted dotted (`title.de=…`) and as dict.
    - **Write-only enforcement** — `BoneType(write_only=True)`:
      `Credential` and the new **`Password`** type (full `PasswordBone`
      structure incl. pinned complexity `tests`) never appear in dumps;
      hashing stays in module hooks.
    - **`Spatial(bounds_lat=…, bounds_lng=…)` type factory** —
      `SpatialBone` parity; `(lat, lng)` JSON values, dotted
      (`pos.lat`/`pos.lng`) and list input.
    - **`MultipleConstraints`** for multiple relations via
      `viur_relation_meta` (`{"multiple": {"min", "max", "duplicates"}}`)
      — emitted like the bone, enforced in `viur_from_client`.
    - **pydantic ecosystem registrations** — `EmailStr` → `str.email`,
      `AnyUrl`/`HttpUrl` → `uri`, pydantic-extra-types `Color` → `color`,
      `PhoneNumber` → `str.phone` (when `phonenumbers` is installed);
      `constr`/`conint`/`condecimal` constraints feed the derivation
      with no registration (incl. union-arm `Annotated` metadata).
      Non-`str` semantic objects stringify in dumps. New dependency:
      `email-validator`.

- **Full bone-type palette** — new built-in field types, each
  structure-parity-verified against its real bone: `Raw`, `Code`
  (CodeBone/JinjaBone/LogicsBone/PythonBone; unindexed), `Color`,
  `Phone` (with PhoneBone's test regex), `Uri` (hint set), `Uid`
  (readonly, unique lock, `compute: Once`; value generation stays the
  module's job), `SortIndex` (`set_default` clone), `Json`
  (unindexed; needs `sa_type=JSON`), `Credential` (structure only —
  write-only semantics not enforced yet). Docs gained a complete bone
  reference (`docs/bones.md`) covering **all** viur-core bones incl.
  the deliberately unmapped ones with workarounds.

### Fixed

- Numeric structure: `decimal` is no longer derived as `precision > 0` —
  the real NumericBone keeps `decimal: false` for floats (e.g.
  SortIndexBone: precision 8, decimal false); it only flips for
  `Decimal`-typed fields.

- **Multiple relations** — many-to-many via `Relationship(link_model=…)`
  emit a `relational.<kind>` bone with `multiple: True` and
  `defaultvalue: []` (structure-parity-verified against
  `RelationalBone(multiple=True)`), appended after the regular fields.
  Bone parameters come from the owning model's `viur_relation_meta`
  ClassVar (no FK field exists as carrier). Dumps emit a list of
  `{"dest": …}` objects; client input accepts key lists, single keys and
  dump shapes (empty clears). Parsed keys are parked on the instance and
  resolved by SQLList in its session (existence check + assignment; the
  link table syncs on commit). The FK-less side of one-to-one relations
  is now skipped like children lists.
- **Relational mapping** (analysis/01 §5.4) — an FK field + to-one
  `Relationship()` emit ONE `relational.<kind>` bone under the
  relationship's name (bone parameters from the FK field's ViURField;
  `required` from FK nullability; to-many sides skipped). `relskel`
  carries key + `viur_ref_keys` + the `shortkey` system bone and is
  structure-parity-verified against the real `RelationalBone`. Dumps emit
  the `{"dest": {…ref keys…}, "rel": None}` shape (loaded relations from
  `__dict__` only — no lazy IO on detached instances; key-only fallback
  from the FK column). `viur_from_client` accepts opaque key strings and
  the dump shape; `SQLList` eager-loads relations (`selectinload`),
  verifies target existence in its session and expires stale loaded
  relations on edit.

- **`SQLList` module prototype** (`viur.models.sqllist`, analysis/02) —
  the SQL counterpart of viur-core's `List`: `list`/`view`/`add`/`edit`/
  `delete`/`structure` over envelope v2, hook system
  (`can<X>`/`on<X>`/`then<X>`/`<X>Skel`) reused from viur-actions with
  fail-closed defaults, merge-not-replace edit semantics, structure-
  whitelisted equality filters and `orderby` (injection-safe by
  construction), opaque OFFSET cursor pagination, and an `sqlFilter(stmt)`
  hook as the `listFilter` analogue. Deliberately not re-exported from
  `viur.models` — plain model definitions stay framework-import-free.
  Carries `handler = "list"` and the renderer opt-in flags
  `SQLList.json = True` / `SQLList.vi = True` — viur-core's `__build_app`
  only mounts a module for renderer families flagged on its class (the
  same mechanism as `List.vi = True` in core); without them the module
  never gets instantiated and every route 404s.
- **`viur.models.db`** — shared-engine session lifecycle: `configure()`
  once at boot (URL or engine; `poolclass=NullPool` default for App
  Engine), `get_session()` context manager with commit-on-success /
  rollback-on-error and `expire_on_commit=False`.
- **`ViURModel.errors`** — instance error list (render-protocol
  counterpart of `SkeletonInstance.errors`), stored ORM-safe in
  `__dict__`; dumps normalize naive datetimes from tz-less backends
  (SQLite) to UTC so values are identical before and after a DB roundtrip.

### Changed

- viur-actions is now a dependency (hooks + envelope for `SQLList`).

- **`ViURField`** — `sqlmodel.Field()` wrapper carrying the ViUR bone
  parameters (`descr`, `required`, `visible`, `readonly`, `params`,
  `values`, `compute`) in `FieldInfo.json_schema_extra["viur"]`; extra
  pydantic kwargs go through a whitelisted `schema_extra` (typos raise
  instead of vanishing).
- **Bone-type field types** — the bone type is decided by the Python
  type, never a string parameter: `Text` (`"text"`), `Email`
  (`"str.email"`), `Country` (pydantic-extra-types `CountryAlpha2` →
  `"select.country"` with pycountry values). Custom types via
  `BoneType` `Annotated` markers and `register_bone_type()` (MRO-based
  registry lookup).
- **`ViURModel`** — base class with system fields (`id` → `key` bone,
  `creationdate`/`changedate` as readonly compute dates), cached
  skeleton-compatible `viur_structure()` (fail-fast on unmappable types
  at class-definition time) and opaque `viur_key` encoding with
  `viur_parse_key()`.
- **`viur_dump()`** — JSON-serializable values shaped like
  `SkeletonInstance.dump()` (`key` as opaque string, datetimes as ISO
  strings, enums as values, Decimal as float), plus `dump()`/`structure()`
  aliases so the same envelope code renders models and skeletons.
- **`viur_from_client()`** — the `skel.fromClient()` counterpart: drops
  unknown and readonly fields, validates via pydantic and maps
  `ValidationError` to viur-core's `ReadFromClientError`
  (`missing` → `NotSet` as the real bones report unsubmitted fields,
  everything else → `Invalid`); viur-core is imported lazily so plain
  model definitions never need the App Engine stack.
- **Parity verification** — integration suite compares structure, dump
  values and fromClient error shapes field-by-field against the real
  viur-core skeleton twin (business fields and system bones); a golden
  file pins the structure emission in the unit suite.

## [0.1.0]

### Added

- Initial project scaffold: `src/` package layout (`viur.models` namespace
  package), unit suite with 100 % coverage gate, integration suite against
  the real viur-core, MkDocs Material documentation site, and CI workflows
  for tests, docs deployment and tagged releases.
