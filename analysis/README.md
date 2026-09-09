# Analyse- und Designdokumente

Interne Design- und Analysedokumente für viur-models — nicht Teil der
veröffentlichten MkDocs-Site (die dokumentiert die umgesetzte API; hier
liegt das Soll-Bild und seine Begründung).

| Nr. | Dokument | Inhalt |
|---|---|---|
| 01 | [Field & Structure-Mapping](01-viurfield-und-structure-mapping.md) | Felddefinition über SQLModel/Pydantic, Erzeugung der Bone-Structure für API-Parität, Fehler-Mapping, Key-Encoding, v1-Abgrenzung |
| 02 | [SQLList-Prototyp](02-sqllist-prototyp.md) | Action-Set + Hooks (viur-actions), Session-Lifecycle, Query-Parität, Envelope-v2-Render-Integration |
| 03 | [Migrations](03-migrations.md) | Alembic-Einbettung ins Projektlayout, URL-Auflösung ohne Core-Boot, Autogenerate-Fallstricke (TypeDecorator/AutoString), Abgrenzung Boot vs. Deploy |
