# Migrationsmatrix

Was passiert, wenn Du ein SQL-Modell änderst: was automatisch migriert wird,
wo Daten reduziert werden, und die Fälle, die Handarbeit brauchen.

Alle Kommandos aus dem Verzeichnis, in dem `alembic.ini` liegt:

```bash
# 1. Model ändern, dann:
alembic revision --autogenerate -m "tags single"
# 2. Die erzeugte Datei lesen — Autogenerate erklärt jede Entscheidung
alembic upgrade head
alembic check          # Modelle == Schema?
```

## Die fünf Klassen

| Klasse | Bedeutung |
|---|---|
| **Vollautomatisch** | Generieren, lesen, anwenden. Keine Daten gehen verloren. |
| **Automatisch, reduziert** | Läuft ohne Zutun, verwirft aber Daten — und sagt, wie viele Zeilen betroffen sind. |
| **Eine Zeile von Dir** | Der Generator schreibt einen Stub, die Revision verweigert den Start bis er gefüllt ist. |
| **Handarbeit** | Wird nicht erkannt oder nicht erzeugt. Revision von Hand korrigieren. |
| **Keine Revision** | Kein Schema-, kein Datenbezug. Autogenerate meldet „no changes". |

---

## Vollautomatisch (14 Fälle)

Nichts zu tun außer die Revision zu lesen.

| Änderung | Erzeugt | Anmerkung |
|---|---|---|
| Neues optionales Feld | `add_column` | Alembic-Standard. |
| Neues Pflichtfeld | `add_column` (nullable) + `fill_column` | Der Füllwert kommt aus dem Model selbst — `default`, sonst `emptyvalue`. Ohne den Dreischritt scheitert es an `Cannot add a NOT NULL column`. |
| Feld entfernt | `drop_column` | Der Verlust ist gewollt. |
| Neues Model | `create_table` | Inklusive FKs und Composite-PKs in Abhängigkeitsreihenfolge. |
| Index dazu oder weg | `create_index` / `drop_index` | Aus `Field(index=…)`. |
| Unique-Constraint dazu | `create_unique_constraint` | Der Constraint bekommt automatisch einen Namen — SQLite lehnt unbenannte im Batch-Modus ab. Auf befüllten Tabellen die Falle unten beachten. |
| Neues Feld im LinkModel (`using`) | `add_column` + `fill_column` | Auf der Link-Tabelle, sonst wie ein normales neues Feld. |
| `str` ↔ `Text` | `coerce_text` | **Alembic allein sieht diese Änderung gar nicht.** `Text` lässt `max_length` weg, und Alembic behandelt „keine Länge" als „keine Meinung" — die Spalte behielte still ihr Limit. |
| `max_length` größer | `alter_column` | Verlustfreie Erweiterung. |
| `int` → `float` | `coerce_numeric` | Rundet auf die neue Precision (`round(float(v), p)`). |
| single → multiple | `expand_multiple` | Verlustfrei: eine Link-Zeile pro vorhandenem Wert. Auch bei Umbenennung `tag` → `tags`. `NOT NULL`-Payload-Spalten eines Association-Links werden aus dem Model-Default gefüllt (`payload_defaults`; `Ellipsis`-Stub, wenn es keinen gibt). |
| einsprachig → mehrsprachig | `expand_languages` | Der Wert landet unter `languages[0]`, die übrigen Sprachen bleiben leer. |
| Enum-/Select-Option dazu | `add_enum_values` | **Postgres braucht das** (`ALTER TYPE … ADD VALUE`, sonst `invalid input value for enum` zur Laufzeit); auf SQLite ist die Spalte ein unbeschränktes VARCHAR und die Op ein No-op. |
| Select → `bool` | `coerce_bool` | Textuelle Regel aus `parse.bool`: `"yes"` wird `True`, `"active"` wird `False`. |

---

## Automatisch, aber reduziert (4 Fälle)

Läuft ohne Zutun. Die Auswahl, welcher Wert überlebt, ist dieselbe, die
viur-core beim Lesen trifft — und sie steht sichtbar in der Revision, also
korrigierbar.

| Änderung | Erzeugt | Was überlebt |
|---|---|---|
| multiple → single | `collapse_multiple` | Der **erste** Eintrag, wie `loadVal[0]` im Bone. Beim Anwenden gemeldet: `3 row(s) had several targets`. Auch bei Umbenennung `tags` → `tag`. |
| mehrsprachig → einsprachig | `reduce_languages` | Eine Sprache, per `keep='de'` in der Revision sichtbar. Default ist die erste deklarierte Sprache. |
| `float` → `int` | `coerce_numeric` | **Schneidet ab, rundet nicht** — `3.7` wird `3`. Das ist die Bone-Regel. |
| `max_length` kleiner | `alter_column` | Bestehende Werte werden **nicht** gekürzt — genauso wenig wie die Bones das tun. Postgres kann die Änderung dann beim Commit ablehnen; SQLite nicht. |

---

## Eine Zeile von Dir (1 Fall)

Der einzige Übergang, für den viur-core keine brauchbare Regel hat:
`SelectBone.singleValueUnserialize` sucht ein Enum-Member mit passendem
`value`, ein gespeichertes `True` trifft nichts. Im schemalosen Datastore
fällt das nicht auf, eine typisierte Spalte kann es nicht halten.

| Änderung | Erzeugt | Was Du tust |
|---|---|---|
| `bool` → Select | `remap_values` mit `{True: ..., False: ...}` | Das Dict ausfüllen — **mit den gespeicherten Labels** (Enum-Member-*Namen* wie `YES`), die in der Autogenerate-Ausgabe stehen, nicht mit den Client-Werten (`yes`): die Spalte speichert den Namen, das Wire-Format den Wert. Die Revision **verweigert den Start**, solange `Ellipsis` drinsteht, statt still etwas Plausibles zu schreiben. |

---

## Handarbeit (5 Fälle)

Hier wird bewusst nicht geraten. Bei den ersten beiden gibt es nichts zu
erkennen; bei den letzten drei bleibt die Spalte technisch gültig, nur ihr
Inhalt passt nicht mehr.

| Änderung | Autogenerate | Was Du tust |
|---|---|---|
| Spalte umbenannt | drop + add — verliert Daten | Von Hand auf `alter_column(…, new_column_name=…)` korrigieren. Kein Werkzeug erkennt Umbenennungen; nur der *Relations*-Wechsel multiple↔single wird gepaart. |
| Tabelle umbenannt | drop + create | Von Hand auf `op.rename_table(…)` korrigieren. |
| Record-Feld dazu oder weg (`Record`) | nichts | Der Record lebt in einer JSON-Spalte, die sich nicht ändert. Die gespeicherten JSON-Objekte behalten ihre alte Struktur. Wenn das stört: eigene `op.execute(…)`-Migration. |
| Enum-Option entfernt | Warnung, keine Op | Zeilen mit dem alten Wert bleiben stehen, und Postgres kann einen Enum-Wert nicht löschen (Typ müsste neu aufgebaut werden). Per `op.remap_values(…)` umschreiben; der Generator warnt. |
| Relationsziel geändert | FK-Wechsel, keine Datenmigration | Die FK-Werte zeigen auf IDs der alten Zieltabelle. Es gibt keine ableitbare Zuordnung — Umschreiben ist projektspezifisch. |

---

## Keine Revision nötig (3 Fälle)

Autogenerate meldet „no changes in schema detected" — korrekt.

| Änderung | Warum |
|---|---|
| `descr`, `visible`, `readonly`, `params`, `format` | Reine Client-Metadaten. Ändern die Bone-Structure, aber weder Schema noch Daten — der Admin sieht sie beim nächsten Request. |
| `@computed_field` dazu oder weg | Hat keine Spalte. Der Wert wird beim Dump berechnet. |
| `viur_ref_keys` geändert | Betrifft die `dest`-Snapshots von Cross-Store-Referenzen, nicht das Schema. Auffrischen über `refresh_crossstore(Model)` bzw. `refresh_for_target(key)`. |

---

## Drei Regeln, die überraschen

Alle drei sind viur-cores Regeln.

**Sprachen sind asymmetrisch.** *Runter* gilt `conf.i18n.default_language`,
*hoch* gilt `languages[0]` — die erste **deklarierte** Sprache. Das ist nicht
dasselbe. Und ist die gewünschte Sprache als Schlüssel vorhanden, gewinnt ihr
Wert, auch wenn er leer ist.

**`float` → `int` schneidet ab.** `int(float(3.7))` ergibt `3`, nicht `4`. Nur
`precision > 0` rundet. Beides ist `NumericBone._convert_to_numeric`.

**Bones kürzen nie.** `max_length` wird ausschließlich in `fromClient`
validiert, nie beim Lesen. Ein verkleinertes Limit lässt lange Bestandswerte
also stehen — `coerce_text` macht es genauso.

---

## Fallen

**Pflichtfeld + UNIQUE auf einer befüllten Tabelle.** Ein Füllwert für alle
Zeilen verletzt `UNIQUE`, sobald mehr als eine Zeile existiert. Der Generator
kann keine eindeutigen Werte erfinden, **warnt aber vorher** statt beim Commit
zu scheitern. Den `fill_column`-Wert dann durch zeilenweise Werte ersetzen.

**Abgebrochene Migration auf SQLite.** Jeder Batch-Block baut die Tabelle über
`_alembic_tmp_<table>` neu. Scheitert eine Migration mittendrin, bleibt diese
Tabelle liegen und blockiert den nächsten Versuch mit
`table _alembic_tmp_x already exists` — von Hand löschen. Inhärent in Alembics
Batch-Modus.

**Snapshots gehören ins Repository.** Unter
`migrations/structures/<revision>.json` liegt die Bone-Structure pro Revision —
die Erkennungsgrundlage. Fehlt ein Snapshot, wird kein Übergang erkannt (still,
ohne Fehler). Nachträglich einführen ist gefahrlos: ein fehlender Snapshot
ergibt einen leeren Diff.

**Migration nie beim Instanz-Start.** `main.py` erzeugt kein Schema mehr, es
berichtet nur die Revision. Bei mehreren App-Engine-Instanzen liefen sonst
mehrere Migrationen gleichzeitig gegen dieselbe Datenbank.

---

Regeln geprüft gegen viur-core 3.9 · Alembic ≥ 1.13 · verifiziert auf SQLite **und** Postgres 16.
Ausführlich: [Migrationen](migrations.md) · Runbook: `migrations/README`.
