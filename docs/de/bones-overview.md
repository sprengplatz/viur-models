# Bone-Übersicht

Kompakte Referenz, wie jede viur-core-Bone als viur-models-Feld definiert
wird. Grundregel: **Der Python-Typ bestimmt die Bone** — nie ein
String-Parameter. Alles, was Pydantic/SQL schon ausdrückt (`max_length`,
`ge`/`le`, Optionalität, Defaults), wird abgeleitet; nur ViUR-Spezifisches
(`descr`, `visible`, `params`, …) geht durch `Field`. Optionalität wie
üblich per `| None` + `default=None`. Alle Ableitungsregeln im Detail:
[Bone-Referenz](bones.md); lauffähiges Beispiel:
`deploy/models/example.py`.

## Gesamtübersicht

### Skalare

| Bone | Type-String | Feld-Definition |
|---|---|---|
| `StringBone` | `str` | `name: str = Field(descr="Name", max_length=100)` |
| `NumericBone` (int) | `numeric` | `rating: int \| None = Field(default=None, ge=1, le=5)` |
| `NumericBone` (float/Decimal) | `numeric` | `price: Decimal \| None = Field(default=None, decimal_places=2)` |
| `BooleanBone` | `bool` | `active: bool = Field(default=True)` |
| `DateBone` | `date` | `due: datetime \| None` · `day: date \| None` · `slot: time \| None` |

### Selects

| Bone | Type-String | Feld-Definition |
|---|---|---|
| `SelectBone` (Enum) | `select` | `kind: EntryKind = Field(descr="Art")` |
| `SelectBone` (Literal) | `select` | `status: Literal["new","done"] \| None = Field(values={…}, sa_type=String)` |
| `SelectCountryBone` | `select.country` | `country: Country \| None = Field(default=None)` |

### String-Verfeinerungen (der Typ trägt die Bone)

| Bone | Type-String | Feld-Definition |
|---|---|---|
| `TextBone` | `text` | `message: Text = Field(default="", required=False)` |
| `EmailBone` | `str.email` | `mail: Email \| None = Field(default=None)` |
| `PhoneBone` | `str.phone` | `phone: Phone \| None = Field(default=None, max_length=15)` |
| `UriBone` | `uri` | `site: Uri \| None = Field(default=None)` |
| `ColorBone` | `color` | `tint: Color \| None = Field(default=None)` |
| `RawBone` | `raw` | `blob: Raw \| None = Field(default=None)` |
| `CodeBone`/`Jinja`/`Logics`/`Python` | `raw.code` | `template: Code \| None = Field(default=None)` |
| `JsonBone` | `raw.json` | `data: Json \| None = Field(default=None, sa_type=JSON)` |
| `UidBone` | `uid` | `uid: Uid \| None = Field(default=None)` — ⚠️ Wert-Generierung im Hook |
| `SortIndexBone` | `numeric.sortindex` | `sortindex: SortIndex \| None = Field(default=None)` |
| `CredentialBone` | `str.credential` | `secret: Credential \| None = Field(default=None, visible=False)` — write-only |
| `PasswordBone` | `password` | `pwd: Password \| None = Field(default=None)` — write-only, ⚠️ Hashing im Hook |
| `SpatialBone` | `spatial` | `pos: Spatial(bounds_lat=…, bounds_lng=…) \| None = Field(sa_type=JSON)` |

### Mehrsprachigkeit

| Bone | Feld-Definition |
|---|---|
| `StringBone(languages=…)` | `title: Language[str] = Field(languages=("de","en"), sa_type=JSON)` |
| `TextBone(languages=…)` | `body: Language[Text] \| None = Field(default=None, sa_type=JSON)` |

Sprachliste per `languages=` oder projektweit `set_default_languages("de", "en")`.
Input dotted (`title.de=…`) und als Dict; Wert ist ein `{lang: value}`-Dict.

### Relationen (SQL → SQL)

| Bone | Feld-Definition |
|---|---|
| `RelationalBone` | FK-Feld (trägt die Bone-Parameter) + `Relationship()`:<br>`category_id: int \| None = Field(default=None, foreign_key="example_category.id", descr="Kategorie")`<br>`category: ExampleCategory \| None = Relationship()` |
| `RelationalBone(multiple=True)` | `tags: list[ExampleTag] = Relationship(link_model=EntryTagLink)` |
| `RelationalBone(using=RelSkel)` | Association-Object — die Payload-Spalten der Link-Table SIND das using-RelSkel:<br>`class EntryTagLink(RelationLink, table=True): … tag: ExampleTag = Relationship(); weight: int = Field(ge=0, le=10)`<br>`tags: list[EntryTagLink] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})` |
| `RecordBone` / `AddressBone` | pures Pydantic-Nesting: `class Address(Record): …`<br>`address: Address \| None = Field(default=None, sa_type=RecordJSON(Address), format="$(street)")`<br>multiple: `stops: list[Address] = Field(default_factory=list, sa_type=RecordJSON(Address))` |

`required` leitet sich aus der FK-Nullability ab; `viur_ref_keys = ("name",)`
am **Ziel** bestimmt relskel/dest. Bei multiple/using ist
`cascade="all, delete-orphan"` Pflicht.

### Cross-Store (SQL → Datastore-Skeleton)

Gespeichert wird der dest-Snapshot (Key + ref_keys-Werte) als JSON; der
Input-Read baut den Snapshot neu und ist der Existenz-Check. Gelöschte
Ziele blockieren Edits nicht (der alte Snapshot bleibt); Änderungen an
referenzierten Skeletons propagieren automatisch: einmal
`viur.models.install_refresh_hooks()` beim Boot (wrappt die
Skeleton-postSaved/postDeleted-Handler und deferred den gezielten
`refresh_for_target` über die Index-Tabelle `viur_models_relations` — das
`viur-relations`-Äquivalent; Löschungen defaulten auf `set_null`).
Flächig gibt es `refresh_crossstore(Model, missing=…)` als
Cron-Sicherheitsnetz.

| Bone | Feld-Definition |
|---|---|
| `UserBone` | `owner: UserRef() \| None = Field(default=None, sa_type=JSON)` |
| `FileBone`/`ImageBone` | `upload: FileRef() \| None = Field(default=None, sa_type=JSON)` — Referenz, kein Upload |
| `TreeLeafBone`/`TreeNodeBone` | `node: SkeletonRef(kind, type_suffix="tree.node") \| None` |
| beliebiges Skeleton | `ref: SkeletonRef("feedback", ref_keys=("subject",)) \| None = Field(sa_type=JSON)` |
| multiple (JSON-Array) | `refs: SkeletonRef("feedback", multiple=True) \| None = Field(sa_type=JSON)` |
| multiple (Link-Table) | `class FbLink(SkeletonLink, table=True): viur_kind = "feedback"; …`<br>`history: list[FbLink] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})` |

### System & Sonstiges

| Bone | Feld-Definition |
|---|---|
| `KeyBone` / `creationdate` / `changedate` | automatisch aus der `Model`-Basis — nichts deklarieren |
| `BaseBone` („hidden") | `Hidden = Annotated[str, BoneType("hidden", replace=True)]` |
| `CaptchaBone` / `SpamBone` | Request-Zeit-Prüfung, keine Persistenz — nicht anwendbar |
| `RandomSliceBone` | Query-Verhalten, kein Feld: `def sqlFilter(self, stmt): return stmt.order_by(func.random())` |

## Gemeinsame Bone-Parameter

| Bone-Parameter | Feld-Äquivalent |
|---|---|
| `descr` | `Field(descr=…)` — Default: title-cased Feldname |
| `required` | abgeleitet (nicht-Optional ohne Default); Override per `required=` |
| `defaultValue` | `default=` / `default_factory=` |
| `visible` / `readOnly` | `visible=False` / `readonly=True` (erzwingt `required: false`) |
| `params` | `params={…}` |
| `languages` | `Language[X]` + `languages=(…)` oder `set_default_languages()` |
| `multiple`-Constraints | `viur_relation_meta = {"rel": {"multiple": {"min": …, "max": …, "duplicates": …}}}` |
| `format` | `format="$(dest.name)"` — Records & Relationen |
| `refKeys` | `viur_ref_keys = ("name",)` am Ziel-Model (key/shortkey immer dabei) |
| `unique` / `indexed` | `unique=True` / `index=…` — SQL erzwingt |

## Eigene Typen & pydantic-Ökosystem

```python
# Bone-Typ ohne Python-Pendant: eine Annotated-Zeile
Slug = Annotated[str, BoneType("str.slug")]                # verfeinert str-Extras
Hidden = Annotated[str, BoneType("hidden", replace=True)]  # definiert sie allein

# Ökosystem-Typen sind registriert — der validierte Typ IST die Deklaration:
mail: EmailStr | None = Field(default=None)                # str.email
site: AnyUrl | None = Field(default=None, sa_type=String)  # uri
short: constr(max_length=12) | None = Field(default=None)  # str, maxlength 12

register_bone_type(MeinTyp, BoneType("select.something"))      # MRO-Lookup, Subklassen erben
```

Funktioniert außerdem nativ: `Strict*`, `PositiveInt`/`conint(gt=…)`
(exklusive Grenzen werden für ints exakt zu `min`/`max`), `ByteSize`,
`AwareDatetime`/`PastDate`, extra-types `Epoch.Integer` sowie alle
str-Subclass-Extra-Typen (`MacAddress`, `ISBN`, `TimeZoneName`, `ISO4217`, …)
als validierte str-Bones. **Nicht verwenden:** `SecretStr` (maskiert Dumps —
`Password`/`Credential` nehmen) und `pydantic.Json` (validiert JSON-*Strings* —
`viur.models.Json` nehmen). Normalisierungen beachten: `AnyUrl` hängt einen
Slash an, `PhoneNumber` speichert RFC3966 (`tel:+49…`).
