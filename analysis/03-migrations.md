# Migrations

**Status:** Umgesetzt (`viur.models.migrations`, `viur.models.schema`, `viur.models.migrate` + `viur.models.db.schema_revision`) — inkl. automatisch generierter Bone-Level-Datenmigrationen (§9) · **Bezug:** Alembic ≥ 1.13, SQLModel ≥ 0.0.39, [01 — Field & Structure-Mapping](01-viurfield-und-structure-mapping.md), [02 — SQLList-Prototyp](02-sqllist-prototyp.md)

Dokument 01 beschreibt, wie ein Model zu einer Bone-Structure wird, 02 wie
es als Modul serviert wird. Dieses Dokument schließt die Lücke, die beide
offen lassen: **wie das SQL-Schema über die Zeit an die Models kommt.**

---

## 1. Warum `create_all()` nicht reicht

`SQLModel.metadata.create_all()` ist ein *Bootstrap*, kein
Schema-Lebenszyklus. Es führt `CREATE TABLE IF NOT EXISTS` aus und ist
danach fertig:

| Änderung am Model | `create_all()` |
|---|---|
| neue Tabelle | ✅ angelegt |
| neue Spalte in bestehender Tabelle | ❌ ignoriert |
| entfernte Spalte | ❌ ignoriert |
| geänderter Typ / Länge / Nullability | ❌ ignoriert |
| neuer/entfernter Index, Unique-Constraint | ❌ ignoriert |
| Umbenennung | ❌ ignoriert |

Das Fatale ist nicht das Ignorieren, sondern dass es **stillschweigend**
passiert: Das Model sagt `max_length=100`, die Spalte ist `VARCHAR(50)`,
und der Fehler tritt erst beim Schreiben eines langen Wertes auf — in
Produktion, weit entfernt von der Änderung, die ihn verursacht hat.

Zweites Problem: `create_all()` lief bisher **beim App-Boot**
(`deploy/main.py`). Auf App Engine mit mehreren Instanzen heißt das
DDL-Ausführung durch beliebig viele Prozesse gleichzeitig.

---

## 2. Zwei Randbedingungen, die ein ViUR-Projekt besonders machen

Ein Standard-Alembic-Setup löst 90 % davon. Die restlichen 10 % sind der
Grund, warum es dafür Paketcode gibt statt nur einer Anleitung.

### 2.1 Die URL muss ohne viur-core-Boot auflösbar sein

`alembic upgrade head` läuft aus einer Shell oder einem Deploy-Schritt.
Dort ist der App-Engine-Stack nicht importierbar und schon gar nicht
erwünscht. Die Engine-Konfiguration des Projekts liegt aber in
`conf.models`, also *hinter* dem Boot.

**Lösung:** eine Vorrangkette in `resolve_url()`, die vom Explizitesten zum
Impliziten geht und den conf-Pfad nur als *eine* Quelle unter mehreren
behandelt:

| # | Quelle | Wofür |
|---|---|---|
| 1 | `-x url=…` | einmaliger Override auf der Kommandozeile |
| 2 | `$VIUR_MODELS_DSN` | CI, Deploy-Schritte |
| 3 | die im Prozess konfigurierte Engine | Migration aus einer laufenden App/Test-Fixture |
| 4 | `conf.models`-Preset | nach einem App-Boot |
| 5 | `fallback_url` aus `env.py` | der Projekt-Default |
| 6 | `sqlalchemy.url` in `alembic.ini` | Fixierung auf eine DB |

Schlägt alles fehl, wird **aufgezählt, was versucht wurde** — „keine URL"
ist sonst ein besonders undurchsichtiger Fehler.

Wichtig ist die Abgrenzung bei Stufe 4: `conf.models` wird nur gelesen,
wenn der Namespace existiert **und** ein Preset gesetzt ist. Ein
*fehlkonfiguriertes* Preset (`postgres` ohne DSN) muss durchschlagen, nicht
als „conf nicht verfügbar" verschluckt und stillschweigend durch Stufe 5
ersetzt werden.

### 2.2 Doppelte Wahrheit vermeiden

Wenn `main.py` und `env.py` die DB-Settings je selbst hinschreiben, zeigen
App und Migration irgendwann auf verschiedene Datenbanken — ein Fehler, der
sich als „die Migration lief doch durch" tarnt.

**Lösung:** Das Projekt hält die Settings in einem **framework-freien**
Modul (`deploy/models_db.py`), das beide lesen. Die Abbildung
Preset → URL lebt weiterhin nur im Paket
(`db.url_from_preset()`), das `configure_from_conf()` und `models_db.url()`
gemeinsam benutzen. Damit gibt es eine Settings-Quelle und eine
Mapping-Implementierung.

---

## 3. Autogenerate: zwei Typen-Fallstricke

### 3.1 SQLModels eigene Typen

Autogenerate rendert Spaltentypen über ihren Klassenpfad, also
`sqlmodel.sql.sqltypes.AutoString(length=50)`. Ohne `import sqlmodel` im
Revisions-Template scheitert jede Migration mit
`NameError: name 'sqlmodel' is not defined`. Das Template des Projekts
bringt den Import mit; der Kommentar dort sagt, warum.

### 3.2 Eigene `TypeDecorator`s — der interessantere Fall

`RecordJSON(Address)` (Dokument 01 §5, RecordBone-Mapping) wird von
Autogenerate als `viur.models.db.RecordJSON()` gerendert: im Revisionsmodul
nicht importiert **und** ohne sein Pflichtargument. Die naheliegende
Reparatur — Importe ergänzen und die Record-Klasse mitrendern — ist die
falsche:

> Eine Revision ist ein **eingefrorener Schnappschuss** des Schemas. Sie
> darf nicht von einer Model-Klasse abhängen, die sich weiterentwickelt.
> Sonst bricht eine alte Migration, sobald `Address` umbenannt oder
> entfernt wird.

Ein `TypeDecorator` fügt ohnehin nur **Python-seitiges** Verhalten hinzu;
das DDL ist sein `impl`. `render_item()` rendert deshalb den Impl-Typ:
`RecordJSON(Address)` → `sa.JSON()`. Das ist nicht nur einfacher, es ist
korrekter.

Abgrenzungen in `render_item()`:

- SQLModels eigene Decorator (`AutoString` …) bleiben bei Alembics
  Default-Rendering — sie nehmen die Argumente ihres Impl und
  round-trippen sauber.
- Ein dialektspezifisches Impl (`JSONB`, `ARRAY`) hat keinen `sa.`-Namen;
  dort wird ebenfalls an Alembic zurückgegeben, statt etwas Falsches zu
  emittieren.
- Die Impl-Parameter bleiben erhalten (`String(50)` → `sa.String(length=50)`).

### 3.3 Metadata muss gefüllt sein

`SQLModel.metadata` füllt sich als *Seiteneffekt* der Klassendefinition.
Importiert `env.py` die Models nicht, sieht Autogenerate eine leere
Metadata und erzeugt bereitwillig eine Migration, die **jede Tabelle
löscht**. `import_models("models")` importiert das Paket und dessen
öffentliche Submodule (eine Ebene, wie ein `models/`-Ordner).

---

## 4. Weitere Konfigurationsentscheidungen

| Einstellung | Wert | Begründung |
|---|---|---|
| `render_as_batch` | `True` für SQLite-URLs | SQLite kann die meisten `ALTER TABLE`-Varianten nicht; Alembic baut die Tabelle über eine temporäre Kopie um |
| `compare_type` | `True` | Alembics Default ist `False` — geänderte Spaltentypen würden sonst unentdeckt bleiben, also genau der Fall, der `create_all()` schon durchgeht |
| `compare_server_default` | Default (`False`) | erzeugt bei SQLModel viel Rauschen; per `run(compare_server_default=True)` nachrüstbar |
| `include_object` | schließt `alembic_version` aus | Alembics eigene Buchhaltung |
| `viur_models_relations` | **eingeschlossen** | eine echte Tabelle, deren Schema sich zwischen Paketversionen ändern kann — sie herauszunehmen hieße, sie ewig von Hand zu pflegen |
| Pool | `NullPool` | eine Migration ist ein kurzlebiger Prozess |

Projekte mit fremdverwalteten Tabellen in derselben Datenbank müssen
`include_object` erweitern — sonst schlägt Autogenerate vor, sie zu löschen.

---

## 5. Wo das Schema erzeugt wird — und wo nicht

`main.py` erzeugt nichts mehr. Es berichtet:

```python
if conf.models.engine == "memory":
    SQLModel.metadata.create_all(...)      # lebt nur im Prozess
else:
    revision = viur.models.db.schema_revision()
    ...  # loggt die Revision oder warnt, dass nie migriert wurde
```

`schema_revision()` ist bewusst **plain SQL** (`SELECT version_num FROM
alembic_version`, plus ein `has_table`-Check) und importiert Alembic
nicht: Alembic ist eine Dev-/Deploy-Zeit-Abhängigkeit und hat in einer
Request-Instanz nichts zu suchen.

Bewusst *nicht* implementiert: eine Prüfung „ist das auch die neueste
Revision?" beim Boot. Dafür müsste der Prozess das Revisions-Verzeichnis
lesen und Alembic importieren. Das ist die Aufgabe von `alembic check` in
CI bzw. im Pre-Deploy-Schritt — nicht die einer bootenden App.

Ebenso bewusst nicht: Migration beim Instanz-Start. Bei mehreren
App-Engine-Instanzen liefen mehrere Migrationen gleichzeitig gegen
dieselbe Datenbank.

---

## 6. Das Preset `memory`

Für `engine = "memory"` bleibt `create_all()` richtig: Die Datenbank
existiert nur für die Prozesslebensdauer, eine Migrationshistorie wäre
sinnlos. Das ist der Grund für die Verzweigung in `main.py` statt einer
generellen Regel.

---

## 7. Tests

- **Unit** (`tests/test_migrations.py`): die Vorrangkette Stufe für Stufe
  (inklusive „fehlkonfiguriertes Preset schlägt durch"), `include_object`,
  `render_item` in allen vier Fällen, Offline-/Online-Dispatch gegen einen
  Fake-Context, `schema_revision` (nicht migriert / gestempelt / leere
  Tabelle).
- **End-to-End** (im selben Modul): ein echter Alembic-Durchlauf gegen
  echtes SQLite — Autogenerate → `upgrade` → `check` (clean) →
  Modelländerung wird als Diff erkannt → `upgrade` → `downgrade base`.
  Zusätzlich die Zusicherung, dass `RecordJSON` als `sa.JSON()` in der
  Revision landet und der Name `RecordJSON` dort **nicht** vorkommt.

Der End-to-End-Test liegt in der Unit-Suite, nicht in der
Integration-Suite: Er prüft Alembic und SQLModel, nicht viur-core — es gibt
dort also keine Mock-vs-Core-Drift zu fangen (die Begründung der
Suite-Trennung steht in [integration/README.md](../integration/README.md)).

---

## 8. Explizit außerhalb

| Thema | Grund / Weg |
|---|---|
| Daten-Migrationen (Backfills) | Alembic kann es (`op.execute` / Session in der Revision); es gibt hier nur keine viur-spezifische Hilfe dafür |
| Automatische Umbenennungs-Erkennung | Autogenerate sieht drop + add. Kein Werkzeug löst das; die Revision muss von Hand auf `alter_column(new_column_name=…)` korrigiert werden — deshalb steht „Revision immer lesen" im Runbook |
| Mehrere Datenbanken / Schemas | Alembics `--name`-Mechanismus; bisher kein Bedarf |
| viur-cli-Anbindung (`viur migrate …`) | Kandidat; `alembic` direkt aus dem Projekt-Root funktioniert und hat keine Indirektion |
| Migration im Deploy automatisch auslösen | Projekt-/Pipeline-Entscheidung, keine Paketaufgabe |

---

## 9. Bone-Level-Übergänge automatisieren

Nachtrag zur Umsetzung. §1–§8 lösen das *Schema*; dieser Abschnitt löst die
**Daten** — die Übergänge, bei denen ein Skeleton-Projekt einfach den Bone
ändert und viur-core beim Lesen umformt.

### 9.1 Warum es überhaupt eine Lücke gibt

Im Datastore ist der Bone die Interpretationsschicht:
`BaseBone.unserialize` liest den rohen Wert und formt ihn auf die *aktuelle*
Bone-Konfiguration. `multiple=True → False` heißt dort `loadVal[0]` —
`StringBone.refresh` schreibt es als Kommentar sogar hin: *„take the first
one"*. Das passiert **lazy** pro Read, und die neue Form wird beim nächsten
Write persistiert.

Was oft übersehen wird: der Datastore hat für „jetzt alle umstellen" **auch**
einen expliziten Batch-Schritt. `SkeletonMaintenanceTask` (Action `refresh`)
iteriert eine Kind-Query und ruft pro Entity `skel.refresh()` +
`skel.write()`. Es ist also dieselbe Zweiteilung wie bei SQL — nur ohne
Schema-Schritt, weil der Datastore keins hat.

In SQL erzwingt die Datenbank das Schema. Eine multiple-Relation *ist* eine
Link-Tabelle, eine single-Relation *ist* eine FK-Spalte: zwei verschiedene
Tabellen, keine Interpretationsfrage. Die Umformung muss also explizit
stattfinden — und genau die wird hier generiert.

### 9.2 Warum die Erkennung nicht auf dem DDL-Diff läuft

Alembics Autogenerate vergleicht Datenbank gegen Metadata und sieht damit
ausschließlich DDL. Das reicht in **beide** Richtungen nicht:

- `str → Text` lässt `max_length` weg, also `VARCHAR(200)` → `VARCHAR` ohne
  Länge. Alembics Typvergleich behandelt „keine Länge" als „keine Meinung" —
  auch mit `compare_type=True`. Es meldet **nichts**, und die Spalte behält
  still ihr Limit. (Eine konkrete Längenänderung `200 → 100` wird dagegen
  erkannt.)
- `multiple → single` erscheint als „Tabelle weg, Spalte neu", ohne
  Verbindung zwischen beiden — und Alembic emittiert das `drop_table`
  **vor** dem `add_column`.

Die fehlende Information liegt aber schon im Paket:
`viur_structure()` ist eine vollständige, JSON-serialisierbare Beschreibung
jedes Bones (Dokument 01 §6). Ein **Structure-Snapshot** pro Revision
(`migrations/structures/<rev>.json`) macht aus dem Raten ein Nachschlagen:
`multiple: true → false`, `languages: [de,en] → null`, `type: "str" →
"text"` sind dort explizite Einträge.

Zwei Konsequenzen aus dem Snapshot-Ansatz:

- Er wird mit `resolve_refs=False` gebaut. Der `relskel` eines
  Cross-Store-Feldes kommt aus der Skeleton-Registry, also aus einem
  gebooteten App — was `env.py` per Definition nicht hat. Der Diff ignoriert
  `relskel` ohnehin (`IGNORED_BONE_KEYS`), also wäre das Auflösen Arbeit,
  die nur scheitern kann. Der Aufruf umgeht zugleich den Structure-Cache,
  damit ein Snapshot niemals die für Requests gecachten Strukturen
  verfälscht.
- Snapshots gehören ins Repository, wie die Revisionen selbst. Nachträgliche
  Einführung ist gefahrlos: ein fehlender Snapshot ergibt einen leeren Diff,
  also keine falsch erkannten Übergänge.

### 9.3 Die Regeln, alle aus viur-core

| Übergang | Regel | Fundstelle |
|---|---|---|
| multiple → single | `values[0]` | `BaseBone.unserialize`, `StringBone.refresh` |
| single → multiple | Wert in Liste wickeln | `BaseBone.unserialize` |
| multilang → scalar | `conf.i18n.default_language`, sonst erster Wert | `BaseBone.unserialize` |
| scalar → multilang | Wert nach `languages[0]` | `BaseBone.unserialize` |
| Select → bool | `str(v).strip().lower() in conf.bone_boolean_str2true` | `BooleanBone.refresh`, `utils.parse.bool` |
| int → float | `round(float(v), precision)` | `NumericBone._convert_to_numeric` |
| float → int | `int(float(v))` — **abschneiden**, nicht runden | dito |
| String ↔ Text | `str(v)`, **nie** truncaten | `StringBone.type_coerce_single_value` |
| Neues Feld | `getDefaultValue(skel)` | `BaseBone.unserialize` |

Drei Stellen verdienen Aufmerksamkeit, weil sie kontraintuitiv sind und beim
Nachbauen aus dem Bauch heraus falsch geraten würden:

1. **Die Sprachrichtungen sind asymmetrisch.** Runter gilt
   `default_language`, hoch gilt `languages[0]`. Das ist nicht dasselbe.
   Weil eine Migration die Laufzeit-i18n-Config nicht lesen darf, übergibt
   der Generator die erste *deklarierte* Sprache als Stellvertreter — und
   schreibt sie sichtbar in die Revision (`keep='de'`), damit sie
   korrigierbar ist.
2. **`float → int` schneidet ab.** `int(float(3.7))` ist `3`, nicht `4`.
3. **Die Bones truncaten Bestandsdaten nie.** `max_length` wird
   ausschließlich in `fromClient` validiert, nicht beim Lesen. Eine
   verkleinerte Länge lässt lange Werte also stehen — und `coerce_text`
   macht es genauso.

Eine bewusste, dokumentierte Abweichung: fällt der Fallback bei
`pick_language` (die gewünschte Sprache ist gar kein Schlüssel), nimmt
viur-core den ersten Wert des Dicts, auch `None`; hier wird der erste
**nicht-leere** genommen. Core-Zweig gilt nur für das Legacy-Format
`_viurLanguageWrapper_`, das viur-models nie schreibt, und NULL zu schreiben
während eine andere Sprache Text hält wäre grundloser Verlust. Cores
Key-Präsenz-Regel bleibt dagegen erhalten: **ist** die Sprache ein
Schlüssel, gewinnt ihr Wert, auch wenn er `None` ist.

### 9.4 Der eine Fall ohne Core-Regel: `bool → select`

`SelectBone.singleValueUnserialize` sucht ein Enum-Member mit
`member.value == val`. Bei gespeichertem `True` trifft das nichts, und der
rohe Bool bleibt im Feld stehen. Im schemalosen Datastore fällt das nicht
auf; eine typisierte Spalte kann es nicht halten.

Hier wird deshalb **nicht** geraten. Der Generator schreibt einen Stub mit
den verfügbaren Werten in die Log-Zeile, und `remap_values` **verweigert den
Start**, solange `Ellipsis` drinsteht:

```python
op.remap_values("post", "flag", {True: ..., False: ...}, new_type=…)
```

Das Ausfüllen ist die einzige Handarbeit in den acht Fällen. Die
Gegenrichtung (`select → bool`) ist über `parse.bool` vollständig definiert
und läuft automatisch.

### 9.5 Relations-Umbenennung

Der natürliche Weg, `multiple → single` zu schreiben, benennt die Relation
mit um: `tags: list[Tag] = Relationship(link_model=…)` wird zu `tag: Tag |
None = Relationship()`. Feld für Feld verglichen ist das „eine Relation
entfernt, eine hinzugefügt" — und die Verbindung, die die Datenmigration
braucht, wäre verloren.

Die Paarung läuft deshalb über die **Zieltabelle**: eine entfernte
multiple-Relation und eine hinzugefügte single-Relation auf dieselbe Tabelle
sind dieselbe Relation. Nur eindeutige Paare werden verbunden; gewann oder
verlor eine Tabelle mehrere Relationen auf dasselbe Ziel, bleiben
Hinzufügung und Entfernung stehen (ein ehrlicher Drop+Create statt einer
Vermutung).

### 9.6 Warum die Coercions in Python laufen

`transform_column` liest chunkweise, formt in Python um und schreibt zurück.
Die Bone-Regeln **sind** Python-Semantik (`round()`, `int(float(v))`,
`str(v).strip().lower() in truthy`); sie in SQL nachzubauen hieße, pro
Backend eine andere Annäherung zu pflegen. Eine Migration läuft einmal, also
gewinnt Korrektheit gegen Durchsatz.

Ein Typwechsel, der zugleich die Daten umformt, kann ohnehin kein einzelnes
`ALTER COLUMN` sein — die alten Werte müssen in ihrer alten Form gelesen
werden. Die Spalte reist deshalb über eine temporäre: anlegen, umgeformt
kopieren, Original löschen, zurückbenennen. Das funktioniert auf SQLite
(das die meisten `ALTER`-Varianten nicht kann) und Postgres identisch.

**Betriebshinweis:** Auf SQLite baut jeder Batch-Block die Tabelle über
`_alembic_tmp_<table>` neu. Scheitert eine Migration mittendrin, bleibt
diese Tabelle liegen und blockiert den erneuten Versuch, bis sie gelöscht
wird — inhärent in Alembics Batch-Modus, aber durch mehrere Spalten-Ops pro
Tabelle wahrscheinlicher. Die Zahl der Blöcke pro Spalte ist deshalb auf
zwei reduziert.

### 9.7 Was hier explizit nicht automatisiert wird

| Fall | Grund |
|---|---|
| Spalten-Umbenennung | Autogenerate sieht drop + add; kein Werkzeug löst das. Von Hand auf `alter_column(new_column_name=…)` korrigieren |
| Cross-Store-Felder (`SkeletonRef`) | Ihre Bone-Struktur braucht die Skeleton-Registry; im Snapshot steht `relskel: {}`. Struktur*änderungen* an solchen Feldern werden erkannt, ihr `relskel` aber nicht verglichen |
| `using`-Feld entfernt | Wird erkannt (`using_field_removed`), erzeugt aber nur den `drop_column` — der Verlust ist gewollt |
| Mehrere Relationen auf dasselbe Ziel | Paarung wäre eine Vermutung, s. §9.5 |
