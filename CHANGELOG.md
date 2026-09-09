# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0]

Initial release: SQLModel-backed models for ViUR — `Model`/`Record` with
skeleton-compatible structure and dump, `Field` carrying the bone metadata, the
bone-typed field types, cross-store references into the datastore, and the
`SQLList` module prototype serving envelope v2. Alembic integration with a
generated scaffold and bone-level data migrations, engine presets for SQLite,
Postgres and BigQuery, and two test suites — viur-light-mock overlay with a
100 % coverage gate, and integration against the real viur-core.
