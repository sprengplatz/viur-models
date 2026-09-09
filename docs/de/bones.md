# Bone-Referenz

Jede viur-core-Bone neben ihrem `Model`-Feld. Abgebildete Bones liefern
dieselben Structure- und Dump-Formen (Bone für Bone von der Integrations-Suite
verifiziert). **Der Python-Typ bestimmt den Bone-Typ**; was Pydantic/SQL
ausdrücken (`max_length`, `ge`/`le`, Nullability, Defaults), wird abgeleitet,
ViUR-spezifische Parameter (`descr`, `visible`, `params`, …) laufen über
[`Field`][viur.models.Field].

## Übersicht — alle Bones

| Bone | Structure-`type` | Feld-Entsprechung | Status |
|---|---|---|---|
| `StringBone` | `str` | `str` | ✅ Parität |
| `TextBone` | `text` | `viur.models.Text` | ✅ Parität (`valid_html: null`) |
| `EmailBone` | `str.email` | `viur.models.Email` | ✅ Parität |
| `PhoneBone` | `str.phone` | `viur.models.Phone` | ✅ Parität |
| `CredentialBone` | `str.credential` | `viur.models.Credential` | ✅ Parität, write-only erzwungen |
| `NumericBone` | `numeric` | `int` / `float` / `Decimal` | ✅ Parität |
| `SortIndexBone` | `numeric.sortindex` | `viur.models.SortIndex` | ✅ Parität |
| `BooleanBone` | `bool` | `bool` | ✅ Parität |
| `DateBone` | `date` | `datetime` / `date` / `time` | ✅ Parität |
| `SelectBone` | `select` | `enum.Enum` / `typing.Literal` | ✅ Parität |
| `SelectCountryBone` | `select.country` | `viur.models.Country` | ✅ (vollständiger ISO-Satz, keine Teilmengen) |
| `ColorBone` | `color` | `viur.models.Color` | ✅ Parität |
| `UriBone` | `uri` | `viur.models.Uri` | ✅ Parität (Hints als Defaults ausgegeben) |
| `RawBone` | `raw` | `viur.models.Raw` | ✅ Parität |
| `CodeBone` / `JinjaBone` / `LogicsBone` / `PythonBone` | `raw.code` | `viur.models.Code` | ✅ Parität (gemeinsamer Type-String) |
| `JsonBone` | `raw.json` | `viur.models.Json` (+ `sa_type=JSON`) | ✅ Parität (`schema` leer ausgegeben) |
| `UidBone` | `uid` | `viur.models.Uid` | ⚠️ Structure-Parität; die serverseitige Erzeugung übernimmt Dein Hook |
| `KeyBone` | `key` | automatisch (`id` aus der Basis) | ✅ Parität |
| `RelationalBone` | `relational.<kind>` | FK-Feld + `Relationship()` | ✅ Parität |
| `RelationalBone(multiple=True)` | `relational.<kind>` | `Relationship(link_model=…)` | ✅ Parität, inkl. `MultipleConstraints` |
| `StringBone(languages=…)` | `str` + `languages` | `Language[str]` / `Language[Text]` | ✅ Parität |
| `UserBone` | `relational.user` | `viur.models.UserRef()` | ✅ Structure-Parität; dest-Snapshot beim Schreiben |
| `FileBone` / `ImageBone` | `relational.tree.leaf.file.file` | `viur.models.FileRef()` | ⚠️ nur Referenz — keine Upload-Behandlung |
| `TreeLeafBone` / `TreeNodeBone` | `relational.tree.*` | `SkeletonRef(kind, type_suffix="tree.leaf")` | ✅ Structure-Parität |
| `RecordBone` / `AddressBone` | `record` | verschachteltes `Record` (+ `RecordJSON`) | ✅ Parität — reines Pydantic-Nesting |
| `SpatialBone` | `spatial` | `viur.models.Spatial(bounds_lat=…, bounds_lng=…)` | ✅ Parität |
| `PasswordBone` | `password` | `viur.models.Password` | ⚠️ write-only erzwungen; das Hashing bleibt in Deinen Hooks |
| `CaptchaBone` | `captcha` | — | ➖ Prüfung zur Request-Zeit, keine Persistenz |
| `SpamBone` | `numeric.spam` | — | ➖ Honeypot, Request-Zeit |
| `RandomSliceBone` | `randomslice` | — | ➖ Query-Verhalten, kein Feld (siehe Hinweis) |
| `BaseBone` | `hidden` | Inline-Alias (siehe eigene Typen) | ✅ trivial |

## StringBone

=== "Skeleton"

    ```python
    name = StringBone(
        descr="Name",
        required=True,
        max_length=100,
    )
    ```

=== "Model"

    ```python
    name: str = Field(descr="Name", max_length=100)
    ```

`required` wird abgeleitet: Ein nicht-`Optional`-Typ ohne Default ist
Pflicht. `maxlength`/`minlength` kommen aus `max_length`/`min_length`
(Default 254, wie bei `StringBone`). Ein optionaler String ist
`name: str | None = Field(default=None, …)`.

## TextBone

=== "Skeleton"

    ```python
    message = TextBone(descr="Nachricht")
    ```

=== "Model"

    ```python
    from viur.models import Text

    message: Text = Field(default="", required=False, descr="Nachricht")
    ```

`Text` ist `Annotated[str, BoneType("text", …)]` — die Spalte bleibt ein
einfacher String. `valid_html` wird als `null` ausgegeben, da der
Default-HTML-Satz des Core von dessen Version abhängt; Clients greifen auf
ihren eigenen zurück.

## EmailBone / PhoneBone / CredentialBone

=== "Skeleton"

    ```python
    mail = EmailBone(descr="E-Mail")
    phone = PhoneBone(descr="Telefon")
    secret = CredentialBone(descr="API-Secret")
    ```

=== "Model"

    ```python
    from viur.models import Credential, Email, Phone

    mail: Email | None = Field(default=None, descr="E-Mail")
    phone: Phone | None = Field(default=None, descr="Telefon", max_length=15)
    secret: Credential | None = Field(default=None, visible=False, descr="API-Secret")
    ```

Alle drei sind `str`-Verfeinerungen — die Spalte bleibt ein String.
`Phone` trägt die clientseitige Test-Regex von `PhoneBone`; für volle
Parität mit `max_length=15` kombinieren.

`Credential` ist **write-only**: Wie in viur-core taucht der gespeicherte
Wert nie in Dumps auf — Lesezugriffe liefern `""`. Auf der Instanz bleibt
er für die Hooks Deines Moduls erreichbar.

## NumericBone / SortIndexBone

=== "Skeleton"

    ```python
    rating = NumericBone(descr="Bewertung", min=1, max=5)
    price = NumericBone(descr="Preis", precision=2)
    sortindex = SortIndexBone()
    ```

=== "Model"

    ```python
    from viur.models import SortIndex

    rating: int | None = Field(default=None, ge=1, le=5, descr="Bewertung")
    price: Decimal | None = Field(default=None, decimal_places=2, descr="Preis")
    factor: float = Field(default=1.0, descr="Faktor")
    sortindex: SortIndex | None = Field(default=None)
    ```

`min`/`max` kommen aus `ge`/`le` (Default: int64-Grenzen, wie bei
`NumericBone`), `precision` aus `decimal_places` (`int` → 0, `float` → 8).
`decimal` ist nur bei `Decimal`-Feldern `true` — Floats behalten
`decimal: false`, genau wie die echten Bones. `SortIndex` bekommt beim
Klonen einen frischen Wert (`set_default`), wie `SortIndexBone`; ihn beim
Insert zu berechnen ist Sache des Moduls.

Client-Input wie bei `NumericBone`: `,` gilt als Dezimaltrenner, Whitespace
wird entfernt, und die Null-Tokens der Clients (`""`, `"None"`, `"null"`,
`"undefined"`) zählen als **leer**. Leer heißt „nicht gesetzt": Optionale
Felder werden `None`, Felder mit Default behalten ihn, Pflichtfelder melden
`NotSet`. Anders als im Core wird sonstiger Text (`"3x"`) nicht zu leer,
sondern bleibt ein Fehler.

## BooleanBone

=== "Skeleton"

    ```python
    active = BooleanBone(descr="Aktiv", defaultValue=True)
    ```

=== "Model"

    ```python
    active: bool = Field(default=True, descr="Aktiv")
    ```

## DateBone

=== "Skeleton"

    ```python
    due = DateBone(descr="Fällig am")                 # Datum + Uhrzeit
    day = DateBone(descr="Tag", time=False)           # nur Datum
    slot = DateBone(descr="Uhrzeit", date=False)      # nur Uhrzeit
    ```

=== "Model"

    ```python
    from datetime import date, datetime, time

    due: datetime | None = Field(default=None, descr="Fällig am")
    day: date | None = Field(default=None, descr="Tag")
    slot: time | None = Field(default=None, descr="Uhrzeit")
    ```

Die Structure-Flags `date`/`time` ergeben sich aus dem Python-Typ. Werte
werden als ISO-Strings gedumpt; naive Datetimes aus Backends ohne
Zeitzonen (SQLite) werden auf UTC normalisiert.

## SelectBone

=== "Skeleton"

    ```python
    kind = SelectBone(descr="Art", values={
        "praise": "Lob",
        "complaint": "Beschwerde",
    })
    ```

=== "Model"

    ```python
    class EntryKind(enum.Enum):
        PRAISE = "praise"
        COMPLAINT = "complaint"

    kind: EntryKind = Field(descr="Art")

    # oder per Literal, mit expliziten Labels:
    status: t.Literal["new", "done"] | None = Field(
        default="new", descr="Status",
        values={"new": "Neu", "done": "Fertig"},
        sa_type=String,   # SQLModel kann Literal nicht selbst auf eine Spalte abbilden
    )
    ```

Enum-Member-Namen werden zu Labels (`PRAISE` → `"Praise"`); mit `values=`
überschreibbar. Enums werden automatisch auf SQL-Enum-Spalten abgebildet,
`Literal` braucht ein explizites `sa_type`.

## SelectCountryBone

=== "Skeleton"

    ```python
    country = SelectCountryBone(descr="Land", values="dach")
    ```

=== "Model"

    ```python
    from viur.models import Country

    country: Country | None = Field(default=None, descr="Land")
    ```

`Country` ist `CountryAlpha2` aus pydantic-extra-types — Validierung
inklusive; `values` kommen aus pycountry, immer der vollständige
ISO-3166-Satz.

## ColorBone / UriBone

=== "Skeleton"

    ```python
    tint = ColorBone(descr="Farbe")
    website = UriBone(descr="Webseite")
    ```

=== "Model"

    ```python
    from viur.models import Color, Uri

    tint: Color | None = Field(default=None, descr="Farbe")
    website: Uri | None = Field(default=None, descr="Webseite")
    ```

`Uri` gibt den Hint-Satz von `UriBone` aus (`accepted_protocols`,
Allow-Lists, …) mit den Defaults der Bone. Die Hints wirken clientseitig;
wo sie erzwungen werden sollen, kommt Pydantic-Validierung dazu
(`schema_extra={"pattern": …}`).

## RawBone / CodeBone / JsonBone

=== "Skeleton"

    ```python
    blob = RawBone(descr="Blob")
    template = CodeBone(descr="Template")     # JinjaBone/LogicsBone/PythonBone: gleicher Typ
    data = JsonBone(descr="Daten")
    ```

=== "Model"

    ```python
    from sqlalchemy import JSON
    from viur.models import Code, Json, Raw

    blob: Raw | None = Field(default=None, descr="Blob")
    template: Code | None = Field(default=None, descr="Template")
    data: Json | None = Field(default=None, sa_type=JSON, descr="Daten")
    ```

`Code` und `Json` sind nicht indiziert, wie ihre Bones. `Json` braucht ein
explizites `sa_type=JSON` — SQLModel kann `dict` nicht von sich aus auf
eine Spalte abbilden. Die `schema`-Validierung von `JsonBone` wird leer
ausgegeben.

## UidBone

=== "Skeleton"

    ```python
    uid = UidBone()
    ```

=== "Model"

    ```python
    from viur.models import Uid

    uid: Uid | None = Field(default=None)
    ```

Die Structure-Parität ist vollständig (readonly, Unique-Lock,
`compute: Once`, `*`-Pattern). Der Wert wird **nicht** erzeugt; fülle ihn
im `onAdd`-Hook Deines Moduls, z. B. aus der Row-ID nach dem Flush.

## RelationalBone (einzeln)

=== "Skeleton"

    ```python
    category = RelationalBone(
        descr="Kategorie",
        kind="example_category",
        module="example_category",
    )
    ```

=== "Model"

    ```python
    category_id: int | None = Field(
        default=None, foreign_key="example_category.id", descr="Kategorie",
    )
    category: ExampleCategory | None = Relationship()
    ```

Die FK-Spalte ist der Ort, an dem SQL die Referenz physisch ablegt —
`Relationship()` allein erzeugt keine Spalte. viur-models verbraucht das
FK-Feld: Die API zeigt **eine** `category`-Bone (`relational.<kind>`),
`category_id` verlässt das Model nie. Die Bone-Parameter stehen am FK-Feld
(ein `Relationship()` kann keine tragen); `required` folgt der Nullability
des FK (`int` statt `int | None` → Pflicht).

Welche Zielfelder in `relskel`/`dest` erscheinen, entscheidet das Ziel:

```python
class ExampleCategory(Model, table=True):
    viur_ref_keys = ("name",)   # Default — das refKeys-Pendant
    name: str = Field(descr="Name", max_length=50)
```

## RelationalBone (multiple)

=== "Skeleton"

    ```python
    tags = RelationalBone(
        descr="Schlagworte",
        kind="example_tag",
        module="example_tag",
        multiple=True,
    )
    ```

=== "Model"

    ```python
    class ExampleEntryTagLink(SQLModel, table=True):   # einfache Link-Tabelle
        entry_id: int | None = Field(
            default=None, foreign_key="example_entry.id", primary_key=True,
        )
        tag_id: int | None = Field(
            default=None, foreign_key="example_tag.id", primary_key=True,
        )

    class ExampleEntry(Model, table=True):
        viur_relation_meta = {"tags": {"descr": "Schlagworte"}}

        tags: list[ExampleTag] = Relationship(link_model=ExampleEntryTagLink)
    ```

Many-to-many über eine Link-Tabelle ist die SQL-Form von `multiple=True`.
Es gibt kein FK-Feld, das Bone-Parameter tragen könnte, sie kommen deshalb
aus `viur_relation_meta` des besitzenden Models. Werte werden als Liste von
`{"dest": …}`-Objekten gedumpt; als Client-Input werden Key-Listen
akzeptiert (ein leerer Wert leert die Liste). Multiple-Bones sortieren
hinter den regulären Feldern.

## StringBone / TextBone mit languages

=== "Skeleton"

    ```python
    title = StringBone(descr="Titel", languages=["de", "en"])
    body = TextBone(descr="Inhalt", languages=["de", "en"])
    ```

=== "Model"

    ```python
    from sqlalchemy import JSON
    from viur.models import Language, Text

    title: Language[str] = Field(
        default=None, languages=("de", "en"), sa_type=JSON, descr="Titel",
    )
    body: Language[Text] | None = Field(default=None, sa_type=JSON, descr="Inhalt")
    ```

`Language[X]` ist ein Wrapper-Typ — wie `list[X]` beschreibt der Typ die
Datenstruktur (ein `{lang: value}`-Dict, gespeichert als JSON-Spalte). Die
Bone-Form kommt aus dem inneren Typ (`str`/`Text`), dazu die
`languages`-Liste aus `Field(languages=…)` oder projektweit aus
`set_default_languages("de", "en")` beim App-Boot. Dumps normalisieren auf
alle deklarierten Sprachen; Client-Input wird dotted (`title.de=…`) und als
Dict akzeptiert — ein teilweiser dotted Input wird in den gespeicherten Wert
gemergt, die übrigen Sprachen überleben einen Edit.

## PasswordBone

=== "Skeleton"

    ```python
    pwd = PasswordBone(descr="Passwort")
    ```

=== "Model"

    ```python
    from viur.models import Password

    pwd: Password | None = Field(default=None, descr="Passwort")
    ```

Die Structure-Parität ist vollständig (Komplexitäts-`tests`,
`test_threshold`), und das Feld ist **write-only** — Dumps liefern `""`.
Was viur-models *nicht* tut, ist hashen: Wandle den eingehenden Wert in den
`onAdd`/`onEdit`-Hooks Deines Moduls um (viur-core nutzt PBKDF2), bevor er
in die Datenbank geht.

## SpatialBone

=== "Skeleton"

    ```python
    pos = SpatialBone(
        descr="Position",
        boundsLat=(46.0, 56.0), boundsLng=(4.0, 17.0), gridDimensions=(10, 10),
    )
    ```

=== "Model"

    ```python
    from sqlalchemy import JSON
    from viur.models import Spatial

    Position = Spatial(bounds_lat=(46.0, 56.0), bounds_lng=(4.0, 17.0))

    pos: Position | None = Field(default=None, sa_type=JSON, descr="Position")
    ```

`Spatial(...)` ist eine Typ-**Factory** (die Grenzen sind feldspezifisch).
Werte sind `(lat, lng)`-Paare, gespeichert als JSON-Liste; Client-Input
wird dotted akzeptiert (`pos.lat=…&pos.lng=…`, wie bei der echten Bone) und
als zweielementige Liste. Die Kachel-*Query*-Logik von `SpatialBone` hat
kein SQL-Gegenstück — für Geo-Abfragen über `sqlFilter` filtern.

## RelationalBone mit MultipleConstraints

=== "Skeleton"

    ```python
    crew = RelationalBone(
        descr="Crew", kind="member", module="member",
        multiple=MultipleConstraints(min=1, max=2, duplicates=False),
    )
    ```

=== "Model"

    ```python
    class Entry(Model, table=True):
        viur_relation_meta = {
            "crew": {"descr": "Crew",
                     "multiple": {"min": 1, "max": 2, "duplicates": False}},
        }

        crew: list[Member] = Relationship(link_model=EntryCrewLink)
    ```

Die Structure gibt das Constraints-Dict exakt wie die Bone aus;
`viur_from_client` erzwingt sie (zu wenige/zu viele Einträge, Duplikate)
mit `Invalid`-Fehlern, bevor irgendetwas die Datenbank berührt.

## RecordBone

=== "Skeleton"

    ```python
    class AddressUsing(RelSkel):
        street = StringBone(descr="Straße", required=True, max_length=100)
        zip_code = NumericBone(descr="PLZ")

    address = RecordBone(descr="Adresse", using=AddressUsing, format="$(street)")
    stops = RecordBone(descr="Stationen", using=AddressUsing,
                       format="$(street)", multiple=True)
    ```

=== "Model"

    ```python
    from viur.models import RecordJSON, Record

    class Address(Record):              # Model ohne table=True/Systemfelder
        street: str = Field(descr="Straße", max_length=100)
        zip_code: int | None = Field(default=None, descr="PLZ")

    address: Address | None = Field(
        default=None, sa_type=RecordJSON(Address), descr="Adresse", format="$(street)",
    )
    stops: list[Address] = Field(
        default_factory=list, sa_type=RecordJSON(Address),
        required=False, descr="Stationen", format="$(street)",
    )
    ```

Records sind **reines Pydantic-Nesting** — `Record` ist das Pendant zu
`RelSkel`: ein `Model` ohne `table=True` und ohne Systemfelder (keine
Identität, kein Key). Validierung, Fehlerpfade (`["address", "street"]`)
und die Dump-Form (das reine Werte-Dict, kein Wrapper) gibt es nativ.
`list[Address]` ist die `multiple=True`-Form. viur-models ergänzt nur den
Structure-Eintrag (`type: record` + `using`, nicht indiziert wie die Bone)
und den Spaltentyp `RecordJSON`, der Instanzen beim Schreiben nach JSON
serialisiert und beim Lesen zurück validiert. Client-Input funktioniert als
verschachteltes JSON und bei einzelnen Records dotted
(`address.street=…`).

## Typen aus dem Pydantic-Ökosystem

Wo Pydantic schon einen semantischen Typ mitbringt, bildet die Registry
ihn ab — der validierte Typ **ist** die Deklaration:

```python
from pydantic import AnyUrl, EmailStr, constr
from pydantic_extra_types.color import Color

mail: EmailStr | None = Field(default=None)                    # str.email
site: AnyUrl | None = Field(default=None, sa_type=String)      # uri
tint: Color | None = Field(default=None, sa_type=String)       # color
short: constr(max_length=12) | None = Field(default=None)      # str, maxlength 12
```

Hinweise (geprüft gegen pydantic 2.13 / pydantic-extra-types 2.x):

- **`constr` / `conint` / `condecimal` brauchen gar keine Registrierung** —
  ihre Constraints landen in `FieldInfo.metadata` und speisen die reguläre
  Ableitung (`maxlength`, `min`/`max`, `precision`). Exklusive Grenzen
  (`PositiveInt`, `conint(gt=…)`) werden bei Integern exakt umgerechnet
  (`gt=0` → `min: 1`); Floats behalten dort die int64-Defaults.
- **Weitere Typen, die einfach funktionieren:** `StrictStr`/`StrictInt`/…,
  `AwareDatetime`/`PastDate`/`FutureDate` (→ `date`), `ByteSize`
  (int-Subklasse → `numeric`, versteht `"1.5MiB"`), `Epoch.Integer` aus
  extra-types (validiert Epoch-Zahlen zu Datetimes → `date`) sowie jeder
  str-Subklassen-Extratyp (`MacAddress`, `ISBN`, `TimeZoneName`, `ISO4217`,
  `DomainStr`, …) als validierte `str`-Bone — mit einem `BoneType`-Alias
  bekommen sie einen eigenen Type-String, falls ein Client sie
  unterscheiden soll.
- **Bekannte Normalisierungen:** `AnyUrl` hängt einen Slash an
  (`https://viur.dev` → `https://viur.dev/`); `PhoneNumber` aus
  extra-types speichert RFC3966 (`tel:+49…`) — für die Rohform ableiten
  und `phone_format` setzen.
- `AnyUrl`/`HttpUrl` und `Color` sind **keine `str`-Subklassen** — solche
  Spalten brauchen ein explizites `sa_type` (z. B. `sqlalchemy.String`);
  Dumps wandeln die Objekte in Strings.
- **Nicht verwenden:** `SecretStr` (sein maskiertes `str()` würde
  buchstäbliche Sternchen dumpen und speichern — nimm
  `Password`/`Credential`, die erzwingen write-only korrekt) und
  `pydantic.Json` (validiert einen JSON-*String*, kein Dict — nimm
  `viur.models.Json`). Beide scheitern früh als nicht abbildbar.
  `FilePath`/`DirectoryPath` validieren **Server**-Pfade und haben nichts
  mit `FileBone` zu tun.
- Die schlanken `viur.models`-Aliase (`Email`, `Uri`, `Color`, `Phone`)
  bleiben für den Fall ohne Zusatzvalidierung; beide Schreibweisen liefern
  dieselbe Bone.

## RelationalBone mit using (Edge-Payload)

=== "Skeleton"

    ```python
    class WeightUsing(RelSkel):
        weight = NumericBone(descr="Gewichtung", min=0, max=10)

    tags = RelationalBone(descr="Schlagworte", kind="example_tag",
                          module="example_tag", multiple=True, using=WeightUsing)
    ```

=== "Model"

    ```python
    from viur.models import RelationLink

    class EntryTagLink(RelationLink, table=True):        # Association-Object
        __tablename__ = "example_entry_tag"
        entry_id: int | None = Field(default=None, foreign_key="example_entry.id", primary_key=True)
        tag_id: int | None = Field(default=None, foreign_key="example_tag.id", primary_key=True)
        tag: ExampleTag = Relationship()                 # die dest-Seite
        weight: int = Field(default=0, ge=0, le=10, descr="Gewichtung")

    tags: list[EntryTagLink] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},   # Pflicht
    )
    ```

Edge-Payload ist das Association-Object-Muster: Die Payload-Spalten der
Link-Tabelle **sind** das using-Skel (Structure-Parität gegen
`RelationalBone(using=RelSkel)` verifiziert). Dumps tragen
`{"dest": …, "rel": {…}}`; Client-Input ist die Wire-Form der Bone
(`[{"dest": {"key": …}, "rel": {…}}, …]`; blanke Keys übernehmen die
Payload-Defaults), Validierungsfehler der Payload behalten den Bone-Pfad
(`["tags", "rel", "weight"]`). Das Link-Model deklariert genau eine
To-One-`Relationship` zum Ziel (die dest-Seite); `SkeletonLink`-Subklassen
tragen Payload-Felder für Cross-Store-Referenzen auf dieselbe Weise.

## Cross-Store-Referenzen (UserBone / FileBone / TreeBones)

=== "Skeleton"

    ```python
    owner = UserBone(descr="Besitzer")
    attachment = FileBone(descr="Anhang")
    node = TreeNodeBone(descr="Ordner", kind="myfolder")
    ```

=== "Model"

    ```python
    from sqlalchemy import JSON
    from viur.models import FileRef, SkeletonRef, UserRef

    owner: UserRef() | None = Field(default=None, sa_type=JSON, descr="Besitzer")
    attachment: FileRef() | None = Field(default=None, sa_type=JSON, descr="Anhang")
    node: SkeletonRef("myfolder", type_suffix="tree.node") | None = Field(
        default=None, sa_type=JSON, descr="Ordner",
    )
    ```

`SkeletonRef(kind, ref_keys=…)` referenziert aus einem SQL-Model heraus ein
**Datastore-Skeleton**. Gespeichert wird der `dest`-Snapshot (kodierter
Datastore-Key + die `ref_keys`-Werte) in einer JSON-Spalte — dieselbe
Denormalisierung, die die echte `RelationalBone` in ihre Entity schreibt.
Die `relskel`-Structure wird über die echte Skeleton-Registry aufgelöst
(`RefSkel.fromSkel`, `key`/`shortkey` immer inbegriffen), die
Structure-Parität ist also exakt. `SkeletonRef(kind, multiple=True)` liefert
die Listenform als JSON-Array in einer Spalte; für die `link_model`-Form —
eine abfragbare Zeile pro Referenz — eine Link-Tabelle von `SkeletonLink`
ableiten:

```python
class EntryFeedbackLink(SkeletonLink, table=True):
    __tablename__ = "example_entry_feedback"
    viur_kind = "feedback"
    viur_link_ref_keys = ("subject",)
    entry_id: int | None = Field(default=None, foreign_key="example_entry.id", primary_key=True)

feedback_history: list[EntryFeedbackLink] = Relationship(
    sa_relationship_kwargs={"cascade": "all, delete-orphan"},   # Pflicht
)
```

Die Basis trägt den Datastore-`key` (Teil des Primärschlüssels) und den
`dest`-Snapshot; die Bone-Parameter kommen aus `viur_relation_meta` (inkl.
`multiple`-Constraints). Beide Formen liefern dieselbe Bone-Structure.

Bei Client-Input (ein opaker Datastore-Key oder das `{"dest": {"key": …}}`
aus dem Dump) wird das Ziel aus dem Datastore gelesen und der Snapshot neu
gebaut — ein unbekannter Key wird abgelehnt; ein zurückgereichter Snapshot,
dessen Ziel gelöscht wurde, bleibt erhalten. Zwischen den Edits veralten
Snapshots — zwei Reparaturwege, analog zu `updateRelations` im Core:

- **Automatisch**: `install_refresh_hooks()` beim Boot wrappt
  `Skeleton.postSavedHandler`/`postDeletedHandler` und deferred
  `refresh_for_target` für referenzierte Kinds (Löschungen defaulten auf
  `missing="set_null"`); die betroffenen Zeilen kommen aus dem Reverse-Index
  `viur_models_relations`, den `SQLList` pflegt. Eine Skeleton-Klasse, die
  den Handler ohne `super()` überschreibt, ruft ihn aus ihrem eigenen
  `onEdited`/`onDeleted`:

  ```python
  from viur.core.tasks import CallDeferred
  from viur.models import refresh_for_target

  _refresh = CallDeferred(refresh_for_target)

  class special(List):
      def onEdited(self, skel):
          super().onEdited(skel)
          _refresh(str(skel["key"]))
  ```

- **Flächendeckend**: `refresh_crossstore(Model, missing=…)` — Table-Scan
  bei JSON-Spalten, `key`-Lookup bei `SkeletonLink`-Tabellen.

`set_null` leert einzelne Referenzen, entfernt Listeneinträge und löscht
`SkeletonLink`-Zeilen. `FileRef` ist eine Referenz — Upload und Ausliefern
bleiben beim Filemodul.

## System-Bones (automatisch)

=== "Skeleton"

    ```python
    # key, creationdate, changedate — von der Skeleton-Basis ergänzt
    ```

=== "Model"

    ```python
    # id (als "key"-Bone ausgegeben), creationdate, changedate —
    # kommen aus der Model-Basisklasse; nichts zu deklarieren.
    ```

`key` ist ein opaker kodierter String (nie der rohe Primärschlüssel);
`creationdate`/`changedate` sind readonly Compute-Daten, gegen die echten
System-Bones gepinnt.

## Nicht abgebildet (und was stattdessen)

| Bone | Warum | Workaround |
|---|---|---|
| `CaptchaBone` | Prüfung zur Request-Zeit, **keine Persistenz** | gehört in die Formular-/Anti-Abuse-Schicht, nicht ins Model |
| `SpamBone` | Honeypot-Feld, Request-Zeit | wie CaptchaBone |
| `RandomSliceBone` | Query-*Verhalten* (Zufallsstichprobe), kein Feld | in SQL schlicht: `def sqlFilter(self, stmt): return stmt.order_by(func.random())` |
| `BaseBone` (`"hidden"`) | rohe versteckte Ablage | einen Alias entfernt: `Hidden = t.Annotated[str, BoneType("hidden", replace=True)]` |

## Ausgegebene Structure-Keys

Jede Bone trägt dieselben Basis-Keys, gegen viur-core 3.9 gepinnt:

`descr`, `type`, `required`, `params`, `visible`, `readonly`, `unique`,
`languages`, `emptyvalue`, `indexed`, `clone_behavior`, `multiple` — dazu
`defaultvalue`, wo es einen gibt, und das `sortindex`, das
`SkeletonInstance.structure()` ergänzt.

Pro Bone-Familie kommen hinzu:

| Familie | Zusätzliche Keys |
|---|---|
| `str` | `maxlength` (Default 254), `minlength` |
| `numeric` | `min` / `max` (int64-Grenzen), `precision`, `decimal` |
| `date` | `date`, `time`, `naive` |
| `select` | `values` als `{value: label}`-Dict |

`clone_behavior` ist standardmäßig `{"strategy": "copy_value"}` und
`{"strategy": "set_default"}` für die Bones, die beim Klonen neu erzeugt
werden (`Uid`, `SortIndex`). `emptyvalue` ist `""` für die String-Familie und
sonst `None`; Multiple-Bones tragen `defaultvalue: []`. `emptyvalue`
entscheidet, wie ein geleertes Formularfeld gelesen wird (`""` leert Bones,
deren emptyvalue nicht `""` ist).

Die Unit-Suite pinnt das per Golden File; die Integrations-Suite vergleicht
es gegen die echten Bones.

## Gemeinsame Bone-Parameter

| Bone-Parameter | Feld-Entsprechung |
|---|---|
| `descr` | `Field(descr=…)` (Default: title-cased Feldname) |
| `required` | aus dem Typ abgeleitet; Override per `required=` |
| `defaultValue` | schlicht `default=` / `default_factory=` |
| `visible=False` | `Field(visible=False)` |
| `readOnly=True` | `Field(readonly=True)` (erzwingt `required: false`, wie `BaseBone`) |
| `params` | `Field(params={…})` |
| `unique` | `Field(unique=True)` — von SQL erzwungen; die Structure gibt `False` aus |
| `indexed` | `Field(index=…)` (Structure-Default `True`, wie im Datastore) |
| `languages` | Wrapper-Typ `Language[X]` + `Field(languages=…)` oder `set_default_languages()` |
| `multiple`-Constraints | `viur_relation_meta = {"rel": {"multiple": {"min": …, "max": …, "duplicates": …}}}` |
| `using`-Relationen | Association-Object: die Link-Tabelle von `RelationLink` ableiten, ihre Payload-Spalten SIND das using-Skel |

## Eigene Bone-Typen

Wo kein Python-Typ existiert, erzeugt ihn ein `Annotated`-Alias; wo das
Pydantic-Ökosystem einen semantischen Typ hat, wird er registriert:

```python
from viur.models import BoneType, register_bone_type

Slug = t.Annotated[str, BoneType("str.slug")]          # verfeinert die str-Extras
Hidden = t.Annotated[str, BoneType("hidden", replace=True)]  # definiert sie allein

register_bone_type(SomePydanticType, BoneType("select.something"))
```

Ein `Annotated`-Marker am Feld schlägt die Registry; der Registry-Lookup
läuft die MRO des Typs ab, Subklassen erben also die Abbildung ihrer Basis.
Marker können über `extras` jeden Structure-Key überschreiben (so setzt
`Code` sein `indexed: false` und `Uid` seine
Readonly-/Unique-/Compute-Semantik).
