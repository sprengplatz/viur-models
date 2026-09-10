# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0]

### Added
- Named databases: `Model.viur_database` / `RelationLink.viur_database` select the
  engine; `conf.models.databases` holds one entry per name, `install(databases=…)`
  merges them. `SQLList`, cross-store index and refresh hooks follow the model,
  `setup()` reports every database, `alembic -x db=<name>` migrates one of them.

### Changed
- `conf.models.engine`/`sqlite_file`/`postgres_dsn`/`bigquery_dsn`/`engine_options`
  replaced by `conf.models.databases["default"]`; the flat `install()` arguments
  remain as its shorthand.

## [0.1.0]

Initial release: SQLModel-backed models for ViUR — `Model`/`Record` with
skeleton-compatible structure and dump, `Field` carrying the bone metadata, the
bone-typed field types, cross-store references into the datastore, and the
`SQLList` module prototype serving envelope v2. Alembic integration with a
generated scaffold and bone-level data migrations, engine presets for SQLite,
Postgres and BigQuery, and two test suites — viur-light-mock overlay with a
100 % coverage gate, and integration against the real viur-core.
