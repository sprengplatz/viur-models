# viur-models

SQL-Modelle für ViUR — SQLModel-Klassen, die sich nach außen wie Skeletons
verhalten.

## Warum viur-models?

ViUR speichert im Datastore. Manche Daten gehören trotzdem in eine relationale
Datenbank: weil sie Joins, Transaktionen oder Aggregationen brauchen, weil sie
dort schon liegen, oder weil sie analytisch sind (BigQuery). viur-models bindet
solche Tabellen ein, ohne dass Admin oder API einen Unterschied sehen.

- **Dieselbe Oberfläche wie ein Skeleton.** Ein `Model` liefert dieselbe
  Bone-Structure und dieselbe Dump-Form; `SQLList` bedient
  `list`/`view`/`add`/`edit`/`delete` über Envelope v2 mit derselben Hook-Kette
  (`can<X>`/`on<X>`/`then<X>`). Der Admin-Client kann die beiden nicht
  unterscheiden.
- **Der Python-Typ ist die Bone.** `str`, `int | None`, `Enum`, `datetime` —
  was pydantic und SQL ausdrücken, wird abgeleitet; ViUR-Spezifisches
  (`descr`, `visible`, `params`) kommt über `Field`. Jede viur-core-Bone hat
  eine Entsprechung.
- **Beide Welten verbunden.** Cross-Store-Referenzen zeigen aus SQL auf
  Datastore-Skeletons (`UserRef`, `FileRef`, `SkeletonRef`) — mit
  `dest`-Snapshot, `relskel` aus der echten Skeleton-Registry und
  automatischem Refresh, wenn sich das Ziel ändert.
- **Schema-Änderungen als Datenmigration.** Alembic mit generiertem Scaffold;
  Bone-Übergänge (`multiple`↔single, Sprachen, Typwechsel) werden erkannt und
  nach viur-cores eigenen Regeln migriert, statt Daten zu verlieren.

Der Import ist nebenwirkungsfrei: `import viur.models` patcht viur-core nicht
und registriert nichts.

## Inhalt

| | |
|---|---|
| [Erste Schritte](getting-started.md) | Installation, `install()`/`setup()` beim App-Boot, Engine-Presets, Entwicklungsumgebung |
| [Bone-Referenz](bones.md) | Jede viur-core-Bone neben ihrem Model-Feld, Ableitungsregeln, Structure-Keys |
| [Bone-Übersicht](bones-overview.md) | Dasselbe als Kompakttabelle |
| [Migrationen](migrations.md) | Alembic-Scaffold, erste Revision, Bone-Übergänge, Snapshots |
| [Migrationsmatrix](migration-matrix.md) | Was bei welcher Model-Änderung automatisch, reduziert oder von Hand migriert wird |
| [BigQuery](bigquery.md) | Das analytische Backend und seine drei Kompromisse |
| [API-Referenz](api/models.md) | Aus den Docstrings generiert (englisch) |
| [Änderungshistorie](changelog.md) | Releases |

## Status

Alpha, 0.1.0.
