# ViURField & Structure-Mapping

**Status:** Umgesetzt (§4–§8 inkl. Relationen-Mapping §5.4: ViURField, Typen, Structure, Dump, Fehler-Mapping, Keys) · **Bezug:** viur-core 3.9.0.dev6, viur-actions (Envelope v2), SQLModel ≥ 0.0.39 / Pydantic v2

Dieses Dokument spezifiziert, wie SQLModel-Definitionen in viur-models die
ViUR-Bone-Metadaten tragen (`ViURField`), wie daraus die `structure()`
erzeugt wird, die Admin-Clients erwarten, und wie Pydantic-Validierungsfehler
auf viur-cores Fehlerformat abgebildet werden. Der `SQLList`-Prototyp selbst
(Session-Lifecycle, Actions) wird nur angerissen und in einem Folgedokument
spezifiziert.

---

## 1. Ziel und Leitplanke

viur-models bringt SQL-Unterstützung **neben** das Skeleton-System. Die
Leitplanke ist **API-Parität**: Ein SQL-Modul muss über die json-/html-Render
(inkl. der v2-Envelope aus viur-actions) nach außen so aussehen wie ein
Skeleton-Modul — gleiche `structure`, gleiche `dump`-Shapes, gleiches
Fehlerformat. Clients (admin5/vi) dürfen nicht erkennen müssen, welche
Persistenz dahinterliegt.

Daraus folgt die Kernentscheidung dieses Dokuments:

> **Der Vertrag ist die serialisierte Structure, nicht die Bone-Klassen.**
> viur-models re-implementiert keine Bones; es erzeugt dasselbe
> JSON-serialisierbare Structure-Dict, das `BaseBone.structure()` +
> `SkeletonInstance.structure()` liefern würden.

## 2. Ist-Stand: der Structure-Vertrag in viur-core 3.9

`SkeletonInstance.structure()` (skeleton/instance.py) liefert:

```python
{bone_name: bone.structure() | {"sortindex": i} for i, (bone_name, bone) in ...}
```

`BaseBone.structure()` (bones/base.py) emittiert pro Bone:

| Key | Quelle / Semantik |
|---|---|
| `descr` | Anzeigename |
| `type` | Bone-Typ-String (`"str"`, `"numeric"`, `"relational.<kind>"`, …) |
| `required` | `required and not readOnly` (!) |
| `params` | freies Dict für Client-Hints |
| `visible` | bool |
| `readonly` | bool |
| `unique` | `unique.method.value` oder `False` |
| `languages` | Liste oder `None` |
| `emptyvalue` | `getEmptyValue()` |
| `indexed` | bool |
| `clone_behavior` | `{"strategy": …}` |
| `multiple` | bool **oder** `{"duplicates", "max", "min"}` bei Constraints |
| `defaultvalue` | nur wenn nicht callable und nicht `None` |
| `compute` | nur wenn gesetzt: `{"method", "lifetime"?}` |

Spezialisierungen (Auszug, für das Typ-Mapping in §5 relevant):

- **StringBone**: `+ maxlength, minlength`
- **NumericBone**: `+ min, max, precision, decimal`
- **SelectBone**: `+ values` (Dict `{key: label}`)
- **RelationalBone**: `type = "relational.<kind>"`, `+ module, format, using, relskel`

Fehlerformat (`viur.core.bones.base`):

```python
ReadFromClientErrorSeverity(Enum): NotSet=0, InvalidatesOther=1, Empty=2, Invalid=3

@dataclass
class ReadFromClientError:
    severity: ReadFromClientErrorSeverity
    errorMessage: str | None
    fieldPath: list[str]
    invalidatedFields: list[str] | None
```

## 3. `ViURModel` — die Basisklasse

```python
# viur.models.base
class ViURModel(SQLModel):
    """Gemeinsame Basis aller viur-models. Kein table=True — das setzen
    erst die konkreten Models im Projektordner ``models/``."""

    # --- Renderer-Protokoll (siehe §3.1) ---
    @classmethod
    def viur_structure(cls) -> dict: ...      # §6
    def viur_dump(self) -> dict: ...          # Werte, JSON-serialisierbar
    @classmethod
    def viur_from_client(cls, data: dict) -> tuple["ViURModel | None", list[ReadFromClientError]]: ...  # §7

    # --- Key-Handling (siehe §8) ---
    @property
    def viur_key(self) -> str: ...
    @classmethod
    def viur_parse_key(cls, key: str) -> t.Any: ...
```

### 3.1 Renderer-Anbindung als Protokoll, nicht als Vererbung

Die Envelope-Render (viur-actions) brauchen von einem "Datending" genau
drei Fähigkeiten. Wir definieren sie als `typing.Protocol`:

```python
class Renderable(t.Protocol):
    def dump(self, *, bones: t.Iterable[str] = ()) -> dict: ...
    def structure(self) -> dict: ...
```

`SkeletonInstance` erfüllt das Protokoll bereits de facto (§2). Für
`ViURModel` gibt es einen schmalen Adapter `ModelInstance`, der eine
Model-Instanz (oder die Klasse, für `add`-Formulare) wrappt und
`dump()`/`structure()` auf `viur_dump()`/`viur_structure()` delegiert.
Damit rendert **derselbe** Envelope-Code beide Welten; Parität wird zur
Typfrage statt zur Disziplinfrage.

### 3.2 Systemfelder

Skeletons haben Systembones (`key`, `creationdate`, `changedate`), die
Clients voraussetzen. `ViURModel` liefert die Entsprechungen als
vordefinierte Spalten + Structure-Einträge:

| Skeleton-Bone | ViURModel-Feld | SQL | Structure |
|---|---|---|---|
| `key` | `id` (PK) → `viur_key` (kodiert, §8) | `INTEGER PRIMARY KEY` o. UUID | `"key"`, readonly, `visible: False`, descr `"Key"` |
| `creationdate` | `creationdate` | `TIMESTAMP` | `"date"`, readonly, `visible: False`, `compute: {"method": "Once"}` |
| `changedate` | `changedate` | `TIMESTAMP` | `"date"`, readonly, `visible: False`, `compute: {"method": "OnWrite"}` |

Die Werte sind durch den Parity-Test gegen die echten System-Bones
gepinnt. Im `dump()` erscheint `key` als kodierter String — **nie** der
nackte PK.

## 4. `ViURField` — Felddefinition

`ViURField` ist ein dünner Wrapper um `sqlmodel.Field()`. Er nimmt die
Bone-Parameter entgegen, legt sie unter `json_schema_extra["viur"]` in der
Pydantic-`FieldInfo` ab und reicht alles andere unverändert an SQLModel
durch. Das Model bleibt dadurch ein **reines SQLModel** (Alembic, FastAPI,
Plain-SQLAlchemy funktionieren unverändert); die ViUR-Semantik ist reine
Metadaten-Annotation.

```python
def ViURField(
    default: t.Any = PydanticUndefined,
    *,
    # --- ViUR-Bone-Parameter (→ json_schema_extra["viur"]) ---
    descr: str | None = None,          # Default: Feldname, title-cased
    required: bool | None = None,      # Default: aus Typ abgeleitet (§5.1)
    visible: bool = True,
    readonly: bool = False,
    params: dict | None = None,
    values: dict | None = None,        # Label-Override für select (Enum/Literal)
    compute: dict | None = None,       # Structure-Passthrough (Systemfelder)
    schema_extra: dict | None = None,  # whitelistete pydantic-FieldInfo-Kwargs
    # --- SQLModel/Pydantic-Passthrough ---
    **kwargs: t.Any,                   # primary_key, foreign_key, max_length,
) -> t.Any:                            # ge/le, sa_column, index, unique, …
    viur_meta = {k: v for k, v in dict(
        descr=descr, required=required, visible=visible, readonly=readonly,
        params=params, values=values, compute=compute,
    ).items() if v is not None}
    # SQLModel 0.0.39: ``schema_extra`` wird als **kwargs in die FieldInfo
    # gespreizt — json_schema_extra muss darin verschachtelt übergeben werden.
    schema_extra = kwargs.pop("schema_extra", {})
    schema_extra["json_schema_extra"] = \
        schema_extra.get("json_schema_extra", {}) | {"viur": viur_meta}
    return Field(default, schema_extra=schema_extra, **kwargs)
```

Verifiziert gegen SQLModel 0.0.39 / Pydantic 2.13: die Metadaten landen in
`FieldInfo.json_schema_extra["viur"]` und erscheinen zusätzlich im
Pydantic-JSON-Schema. Für die Ableitungen in §5.1 stehen bereit:
Constraints via `FieldInfo.metadata` (`MaxLen`/`MinLen`/`Ge`/`Le` aus
annotated_types), `FieldInfo.primary_key` / `.foreign_key` / `.index` /
`.unique` (SQLModel-eigene Attribute) sowie `FieldInfo.is_required()`.

Bewusste Einschränkungen der Signatur:

- **Keine Doppel-Wahrheit:** Constraints, die Pydantic/SQL bereits kennt
  (`max_length`, `ge`/`le`, `unique`, `index`, Nullability, Default), werden
  **nicht** als ViUR-Parameter dupliziert, sondern aus der `FieldInfo`
  abgeleitet (§5.1). `ViURField` trägt nur, was SQL/Pydantic nicht
  ausdrücken kann (`descr`, `visible`, `params`, …).
- **Kein Bone-Typ-Parameter:** Der Bone-Typ wird vom **Python-Typ**
  bestimmt, nie von einem String-Parameter — dedizierte Typen siehe §5.5.
- `languages` und `multiple` sind in v1 **nicht** Teil der Signatur (§9).
- `schema_extra`-Keys werden gegen eine Whitelist geprüft (Tippfehler
  verschwinden bei Pydantic sonst lautlos im Deprecated-Extra-Pfad).

Ein Feld ohne besondere Anforderungen darf weiterhin plain annotiert werden
(`name: str`) oder `sqlmodel.Field` direkt nutzen — das Structure-Mapping
arbeitet über `model_fields` und behandelt fehlende `viur`-Metadaten mit
Defaults. `ViURField` ist Komfort, kein Zwang.

## 5. Typ-Mapping: Pydantic/SQLModel → Bone-Structure

### 5.1 Ableitungsregeln (gelten für alle Typen)

| Structure-Key | Ableitung |
|---|---|
| `descr` | `viur.descr` → `FieldInfo.title` → Feldname (`"created_at"` → `"Created At"`) |
| `required` | `viur.required` → sonst: Typ nicht `Optional` **und** kein Default. Emittiert wird `required and not readonly` (Parität zu BaseBone!) |
| `readonly` | `viur.readonly` → `False`; Systemfelder (§3.2) immer `True` |
| `visible` | `viur.visible` → `True`; `id`-Rohspalte: nicht in Structure (nur `key`) |
| `params` | `viur.params` → `None` |
| `unique` | `FieldInfo.unique`/SQL-Unique-Constraint → `"CustomMethodOnUniqueBone"`-Äquivalent entfällt; emittiert wird der viur-Default (`False` oder Methoden-String) |
| `languages` | v1: immer `None` |
| `multiple` | v1: immer `False` (Ausnahme §5.3 `list[...]`) |
| `emptyvalue` | typabhängig: `""` (str), `None` (numeric/date/relational), `False` entfällt bei bool → `None` |
| `indexed` | `FieldInfo.index` → `True` (Datastore-Konvention: Default indexed) |
| `defaultvalue` | Felddefault, wenn nicht callable/`PydanticUndefined`/`None` |
| `clone_behavior` | konstanter BaseBone-Default |
| `sortindex` | Definitionsreihenfolge in `model_fields` (Pydantic erhält die Reihenfolge) |

### 5.2 Typtabelle

| Python-Typ | Bone-`type` | zusätzliche Structure-Keys |
|---|---|---|
| `str` | `"str"` | `maxlength` ← `max_length` (Default 254), `minlength` ← `min_length` |
| `viur.models.Text` | `"text"` | `valid_html: None` (v1-Abweichung: Core liefert das Default-HTML-Set) |
| `viur.models.Email` | `"str.email"` | str-Extras bleiben (wie `EmailBone`) |
| `viur.models.Country` (= pydantic-extra-types `CountryAlpha2`) | `"select.country"` | `values: {alpha2: name}` aus pycountry |
| `int` | `"numeric"` | `min` ← `ge` (Default int64), `max` ← `le`, `precision: 0`, `decimal: False` |
| `float` / `Decimal` | `"numeric"` | `min`/`max`, `precision` ← `decimal_places` (Default 8), `decimal: precision > 0` |
| `bool` | `"bool"` | — |
| `datetime` | `"date"` | `date: True, time: True, naive: False` |
| `date` | `"date"` | `date: True, time: False, naive: False` |
| `time` | `"date"` | `date: False, time: True, naive: False` |
| `enum.Enum` / `t.Literal[...]` | `"select"` | `values: {wert: label}` (Label aus `viur.values`-Override oder Member-Name) |
| FK-Feld + `Relationship` | `"relational.<zieltabelle>"` | `module`, `format: "$(dest.name)"`, `using: None`, `relskel` (key + `viur_ref_keys` + `shortkey`-Systembone) — §5.4, paritätsgeprüft gegen `RelationalBone` |
| `list[T]` (JSON-Column) | Bone-Typ von `T` | `multiple: True` — **v2, siehe §9** |

Nicht abbildbare Typen (Dispatch läuft über `issubclass` in fester
Reihenfolge: Enum vor str/int wegen StrEnum/IntEnum, bool vor int,
datetime vor date) führen zum Fail-fast aus §5.2-Ende.

### 5.2.1 Dedizierte Typen statt Typ-Parameter (umgesetzt)

Der Bone-Typ wird **vom Python-Typ** bestimmt, nie von einem
String-Parameter. Zwei Mechanismen (`viur.models.types`):

1. **`Annotated`-Marker** — für Bone-Typen ohne eigenen Python-Typ:

   ```python
   Text  = t.Annotated[str, BoneType("text", extras={"valid_html": None},
                                     replace=True, emptyvalue="")]
   Email = t.Annotated[str, BoneType("str.email")]        # str-Extras bleiben
   Slug  = t.Annotated[str, BoneType("str.slug")]          # eigene sind eine Zeile
   ```

   `replace=False` (Default) verfeinert die Structure des Basistyps nur
   (Typ-String + `extras` obendrauf), `replace=True` definiert sie allein.
   Pydantic reicht unbekannte `Annotated`-Metadaten in
   ``FieldInfo.metadata`` durch — Validierung und SQL-Spaltentyp bleiben
   die des Basistyps.

2. **Registry für Ökosystem-Typen** — wo pydantic-extra-types schon einen
   semantischen Typ hat, wird er gemappt statt neu erfunden:

   ```python
   register_bone_type(CountryAlpha2, BoneType("select.country", replace=True,
                                              extras={"values": …pycountry…}))
   ```

   Der Lookup läuft über die MRO des Feldtyps (Subklassen erben ihr
   Mapping); ein `Annotated`-Marker am Feld schlägt die Registry.

Nicht abbildbare Typen (z. B. `dict`, verschachtelte Pydantic-Models ohne
Relationship) führen beim Structure-Aufbau zu einem **harten Fehler zur
Klassendefinitionszeit** (`__init_subclass__` in `ViURModel`), nicht erst
zur Request-Zeit — dieselbe Fail-fast-Philosophie wie das Hook-Wiring in
viur-actions.

### 5.3 `values` für Selects

`viur.values` akzeptiert wie SelectBone ein Dict `{key: label}` oder eine
Enum-Klasse. Emittiert wird das new-style Dict (`{k: str(v)}`) — das
old-style Key-Tuple-Kompat-Format aus `conf.compatibility` wird **nicht**
unterstützt.

### 5.4 Relationen

Ein FK-Paar

```python
class Feedback(ViURModel, table=True):
    author_id: int | None = ViURField(default=None, foreign_key="user.id", descr="Autor")
    author: "User" = Relationship()
```

wird als **ein** Structure-Eintrag `author` mit `type: "relational.user"`
emittiert (`author_id` erscheint nicht separat). `relskel` ist die
Structure des Zielmodels, reduziert auf dessen `ref_keys` (Klassenattribut
`viur_ref_keys: tuple[str, ...] = ("key", "name")` auf dem Zielmodel —
Analogon zu `refKeys`). Der `dump()` liefert die RelationalBone-Shape:

```json
{"author": {"dest": {"key": "...", "name": "..."}, "rel": null}}
```

`using`-Relationen (Zusatzdaten an der Kante) sind v1 außen vor; das
SQL-Äquivalent (Association-Table mit Payload) ist v2-Material.

**Umsetzungsnotizen (verifiziert):** Bone-Parameter der Relation kommen vom
**FK-Feld** (`Relationship()` ist reines SQLAlchemy und trägt keine);
`required` leitet sich aus der FK-Nullability ab; to-many-Seiten
(`uselist`, z. B. `back_populates`-Listen) haben keine Bone-Form und werden
übersprungen — **many-to-many via Link-Table** (`Relationship(link_model=…)`) wird dagegen als `multiple: True`-Relational-Bone gemappt (Bone-Parameter via `viur_relation_meta`-ClassVar, da kein FK-Feld als Träger existiert; `defaultvalue: []` wie beim echten Multiple-Bone); `relskel` wird **skalar** gebaut (bricht A→B→A-Zyklen) und
enthält zusätzlich den `shortkey`-Systembone echter RefSkels (computed,
im Dump nicht berechnet). Client-Input akzeptiert den opaken Key-String
und die Dump-Shape (`{"dest": {"key": …}}`); die Existenzprüfung des
Ziels macht `SQLList` in seiner Session. Dumps lesen geladene Relationen
nur aus `__dict__` (kein Lazy-IO auf detached Instanzen) und fallen sonst
auf ein key-only `dest` aus der FK-Spalte zurück; `SQLList` lädt Relationen
eager (`selectinload`).

## 6. Structure-Erzeugung

`viur_structure()` läuft einmal pro Klasse und wird gecacht
(`functools.cache` auf Klassenebene — Structure ist statisch):

```
für jedes Feld in cls.model_fields (Definitionsreihenfolge):
    1. FK-Rohspalten überspringen, die von einer Relationship konsumiert werden
    2. base = Basis-Dict mit allen BaseBone-Keys (Ableitungsregeln §5.1)
    3. extra = Typtabelle §5.2 (inkl. Spezialisierungs-Keys)
    4. override = json_schema_extra["viur"] (descr, visible, params, …)
    5. structure[name] = base | extra | override_serialisiert | {"sortindex": i}
```

Ein Golden-File-Test pinnt die exakte Key-Menge gegen
`BaseBone.structure()` — ändert viur-core den Vertrag (neuer Key), schlägt
die Integration-Suite an, nicht erst der Admin-Client.

## 7. Fehler-Mapping: Pydantic → `ReadFromClientError`

`viur_from_client(data)` ist das Gegenstück zu `skel.fromClient()`:

```python
@classmethod
def viur_from_client(cls, data: dict) -> tuple[Self | None, list[ReadFromClientError]]:
    try:
        return cls.model_validate(data), []
    except ValidationError as exc:
        return None, [_map_error(e) for e in exc.errors()]
```

Mapping-Tabelle Pydantic-`type` → Severity (**gegen den echten Core
verifiziert** — nicht übermittelte Pflichtfelder meldet der Core als
`NotSet` „Field not submitted", nicht als `Empty`; `Empty` ist
übermittelt-aber-leer und bleibt eine v2-Verfeinerung):

| Pydantic-Fehlertyp | Severity | `errorMessage` |
|---|---|---|
| `missing` | `NotSet` | Pydantic-Message („Field required") |
| `*_type`, `*_parsing`, `enum`, `literal_error` | `Invalid` | Pydantic-Message |
| `string_too_long/short`, `greater_than*`, `less_than*` | `Invalid` | Pydantic-Message |
| Custom-Validator (`value_error`) | `Invalid` | Message des Validators |

Vor der Validierung filtert `viur_from_client()` unbekannte Felder und
readonly-Bones (`key`, `creationdate`, `changedate`) aus den Client-Daten —
Skeleton-Verhalten. Die Fehlermeldungs-**Texte** weichen bewusst ab
(Pydantic statt viur-i18n); Severity und `fieldPath` sind paritätisch
(Integration-Test `test_client_parity.py`).

`fieldPath` = `[str(loc) for loc in error["loc"]]` — bei Relationen entsteht
so derselbe Pfadstil (`["author", "dest", "key"]`) wie bei Bones.
`invalidatedFields` bleibt `None` (das Bone-Konzept
`InvalidatesOther` hat kein Pydantic-Gegenstück; wird nicht emuliert).

Damit serialisiert die viur-actions-Envelope die Fehler **unverändert über
denselben Codepfad** — es gibt keinen zweiten Fehler-Serialisierer.

## 8. Key-Encoding

Nach außen ist `key` ein opaker urlsafe-String, wie beim Datastore:

```
key = urlsafe_b64(f"{tabelle}\x1f{pk}")     # ohne Padding
```

- `viur_key` (Property) kodiert, `viur_parse_key()` dekodiert und
  validiert, dass die Tabelle zur Klasse passt (falscher Key → `None`,
  nie Exception mit Tabellen-Leak).
- Clients dürfen Keys nie parsen müssen — Format ist Implementierungsdetail
  und darf sich ändern.
- Empfehlung für neue Models: UUID-PKs (`uuid7`), dann sind Keys auch ohne
  Roundtrip zur DB erzeugbar (Parität zu `skel = ...; skel["key"]`-Flows).

## 9. Explizit außerhalb von v1

| Feature | Grund | Pfad für v2 |
|---|---|---|
| ~~`languages`~~ | **umgesetzt (v2):** `Language[X]`-Wrapper-Typ, JSON-Column `{lang: value}`, dotted+dict-Input, paritätsgeprüft | — |
| ~~`multiple` mit Constraints~~ | **umgesetzt (v2):** via `viur_relation_meta`, Structure + Enforcement paritätsgeprüft | — |
| `using`-Relationen | Association-Payload | Association-Table-Mapping |
| `compute` (Semantik) | Bone-Laufzeitkonzept | hybrid_property-Mapping; das Structure-Feld wird für die Systemfelder bereits durchgereicht (§3.2) |
| `Text` `valid_html`-Default | Core-Default-Set ist core-versionsabhängig | emittiert `None`; Client fällt auf sein Default zurück |
| `unique` mit Methoden-Varianten | SQL kennt nur echtes UNIQUE | — |
| old-style `values`-Tuples | Kompat-Altlast | wird nicht kommen |

Die Structure emittiert für diese Felder die neutralen Defaults
(`languages: None`, `multiple: False`, kein `compute`) — Clients sehen
also ein valides, nur eingeschränktes Bone-Set, niemals fehlende Keys.

## 10. Parity-Tests (Integration-Suite)

Herzstück der Qualitätssicherung, läuft gegen den **echten** viur-core:

1. **Structure-Parität:** äquivalentes Paar aus Skeleton (`StringBone`,
   `NumericBone`, `SelectBone`, `DateBone`, `RelationalBone`) und
   `ViURModel`; Assertion: identische Structure-Dicts pro Feld (modulo
   dokumentierter v1-Ausnahmen aus §9 — die Ausnahmenliste steht im Test).
2. **Fehler-Parität:** gleicher invalider Payload gegen `skel.fromClient`
   und `viur_from_client`; Assertion: gleiche Severity + `fieldPath`-Shape.
3. **Golden-File des Structure-Vertrags** (§6) als Frühwarnung bei
   Core-Änderungen.

## 11. Beispiel (Soll-Bild)

```python
# deploy/models/feedback.py
import enum
from viur.models import Country, Email, Text, ViURField, ViURModel


class FeedbackKind(enum.Enum):
    PRAISE = "praise"
    COMPLAINT = "complaint"


class Feedback(ViURModel, table=True):   # id/creationdate/changedate kommen aus der Basis
    name: str = ViURField(descr="Name", max_length=100)
    mail: Email = ViURField(descr="E-Mail")
    rating: int = ViURField(descr="Bewertung", ge=1, le=5)
    kind: FeedbackKind = ViURField(descr="Art")
    country: Country | None = ViURField(default=None, descr="Land")
    message: Text = ViURField(descr="Nachricht", required=False, default="")
```

ergibt (Auszug, Feld `rating`):

```json
{
  "rating": {
    "descr": "Bewertung", "type": "numeric", "required": true,
    "params": null, "visible": true, "readonly": false, "unique": false,
    "languages": null, "emptyvalue": null, "indexed": true,
    "clone_behavior": {"strategy": "set_default"},
    "multiple": false, "min": 1, "max": 5, "precision": 0, "decimal": false,
    "sortindex": 2
  }
}
```

## 12. Offene Punkte (Folgedokumente)

- **02 — SQLList-Prototyp:** Action-Set (`list`/`view`/`add`/`edit`/`delete`
  über viur-actions `@action`), Session-Lifecycle (Session pro Request,
  Cloud-SQL-Connector/`NullPool` auf App Engine), Query-Parameter-Parität
  (`orderby`, `limit`, Cursor vs. OFFSET-Pagination).
- **03 — Migrations:** Alembic-Einbettung (wo leben Migrationen im
  Projektlayout, `viur-cli`-Anbindung).
- HTML-Render: Jinja-Kontext (`skel`-Objekt in Templates) — Adapter aus
  §3.1 muss auch dort das Skeleton-Interface erfüllen.
