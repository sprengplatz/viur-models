# Bone reference

Every viur-core bone next to its `Model` field. Mapped bones emit the same
structure and dump shapes (verified bone-by-bone by the integration suite).
**The Python type decides the bone type**; what pydantic/SQL express
(`max_length`, `ge`/`le`, nullability, defaults) is derived, ViUR-specific
parameters (`descr`, `visible`, `params`, …) go through
[`Field`][viur.models.Field].

## Overview — all bones

| Bone | Structure `type` | Field equivalent | Status |
|---|---|---|---|
| `StringBone` | `str` | `str` | ✅ parity |
| `TextBone` | `text` | `viur.models.Text` | ✅ parity (`valid_html: null`) |
| `EmailBone` | `str.email` | `viur.models.Email` | ✅ parity |
| `PhoneBone` | `str.phone` | `viur.models.Phone` | ✅ parity |
| `CredentialBone` | `str.credential` | `viur.models.Credential` | ✅ parity, write-only enforced |
| `NumericBone` | `numeric` | `int` / `float` / `Decimal` | ✅ parity |
| `SortIndexBone` | `numeric.sortindex` | `viur.models.SortIndex` | ✅ parity |
| `BooleanBone` | `bool` | `bool` | ✅ parity |
| `DateBone` | `date` | `datetime` / `date` / `time` | ✅ parity |
| `SelectBone` | `select` | `enum.Enum` / `typing.Literal` | ✅ parity |
| `SelectCountryBone` | `select.country` | `viur.models.Country` | ✅ (full ISO set, no subsets) |
| `ColorBone` | `color` | `viur.models.Color` | ✅ parity |
| `UriBone` | `uri` | `viur.models.Uri` | ✅ parity (hints emitted as defaults) |
| `RawBone` | `raw` | `viur.models.Raw` | ✅ parity |
| `CodeBone` / `JinjaBone` / `LogicsBone` / `PythonBone` | `raw.code` | `viur.models.Code` | ✅ parity (shared type string) |
| `JsonBone` | `raw.json` | `viur.models.Json` (+ `sa_type=JSON`) | ✅ parity (`schema` emitted empty) |
| `UidBone` | `uid` | `viur.models.Uid` | ⚠️ structure parity; server-side generation is your hook's job |
| `KeyBone` | `key` | automatic (`id` from the base) | ✅ parity |
| `RelationalBone` | `relational.<kind>` | FK field + `Relationship()` | ✅ parity |
| `RelationalBone(multiple=True)` | `relational.<kind>` | `Relationship(link_model=…)` | ✅ parity, incl. `MultipleConstraints` |
| `StringBone(languages=…)` | `str` + `languages` | `Language[str]` / `Language[Text]` | ✅ parity |
| `UserBone` | `relational.user` | `viur.models.UserRef()` | ✅ structure parity; dest snapshot at write |
| `FileBone` / `ImageBone` | `relational.tree.leaf.file.file` | `viur.models.FileRef()` | ⚠️ reference only — no upload handling |
| `TreeLeafBone` / `TreeNodeBone` | `relational.tree.*` | `SkeletonRef(kind, type_suffix="tree.leaf")` | ✅ structure parity |
| `RecordBone` / `AddressBone` | `record` | nested `Record` (+ `RecordJSON`) | ✅ parity — plain pydantic nesting |
| `SpatialBone` | `spatial` | `viur.models.Spatial(bounds_lat=…, bounds_lng=…)` | ✅ parity |
| `PasswordBone` | `password` | `viur.models.Password` | ⚠️ write-only enforced; hashing stays in your hooks |
| `CaptchaBone` | `captcha` | — | ➖ request-time verification, no storage |
| `SpamBone` | `numeric.spam` | — | ➖ honeypot, request-time |
| `RandomSliceBone` | `randomslice` | — | ➖ query behavior, not a field (see tip) |
| `BaseBone` | `hidden` | inline alias (see custom types) | ✅ trivial |

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

`required` is derived: a non-`Optional` type without a default is
required. `maxlength`/`minlength` come from `max_length`/`min_length`
(default 254, like `StringBone`). An optional string is
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

`Text` is `Annotated[str, BoneType("text", …)]` — the column stays a
plain string. `valid_html` is emitted as `null`, since the core default
HTML set depends on the core version; clients fall back to their own.

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

All three are `str` refinements — the column stays a string. `Phone`
carries `PhoneBone`'s client-side test regex; pair it with
`max_length=15` for full parity.

`Credential` is **write-only**: like viur-core, the stored value never
appears in dumps — reads emit `""`. The value stays accessible on the
instance for your module's hooks.

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

`min`/`max` come from `ge`/`le` (defaults: int64 bounds, like
`NumericBone`); `precision` from `decimal_places` (`int` → 0,
`float` → 8). `decimal` is `true` only for `Decimal` fields —
floats keep `decimal: false`, exactly like the real bones.
`SortIndex` gets a fresh value on clone (`set_default`), like
`SortIndexBone`; computing it on insert is the module's job.

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
    due = DateBone(descr="Fällig am")                 # date + time
    day = DateBone(descr="Tag", time=False)           # date only
    slot = DateBone(descr="Uhrzeit", date=False)      # time only
    ```

=== "Model"

    ```python
    from datetime import date, datetime, time

    due: datetime | None = Field(default=None, descr="Fällig am")
    day: date | None = Field(default=None, descr="Tag")
    slot: time | None = Field(default=None, descr="Uhrzeit")
    ```

The `date`/`time` structure flags come from the Python type. Values dump
as ISO strings; naive datetimes from tz-less backends (SQLite) are
normalized to UTC.

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

    # or via Literal, with explicit labels:
    status: t.Literal["new", "done"] | None = Field(
        default="new", descr="Status",
        values={"new": "Neu", "done": "Fertig"},
        sa_type=String,   # SQLModel cannot map Literal to a column itself
    )
    ```

Enum member names become labels (`PRAISE` → `"Praise"`); pass `values=`
to override. Enums map to SQL enum columns automatically; `Literal`
needs an explicit `sa_type`.

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

`Country` is pydantic-extra-types' `CountryAlpha2` — validation for
free; `values` come from pycountry, always the full ISO-3166 set.

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

`Uri` emits `UriBone`'s hint set (`accepted_protocols`, allow-lists, …)
with the bone's defaults. The hints are client-side; add pydantic
validation (`schema_extra={"pattern": …}`) where you need it enforced.

## RawBone / CodeBone / JsonBone

=== "Skeleton"

    ```python
    blob = RawBone(descr="Blob")
    template = CodeBone(descr="Template")     # JinjaBone/LogicsBone/PythonBone: same type
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

`Code` and `Json` are unindexed, like their bones. `Json` needs an
explicit `sa_type=JSON` — SQLModel cannot map `dict` to a column on its
own. `JsonBone`'s `schema` validation is emitted empty.

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

Structure parity is complete (readonly, unique lock, `compute: Once`,
`*`-pattern). The value is **not** generated; fill it in your module's
`onAdd` hook, e.g. from the row id after flush.

## RelationalBone (single)

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

The FK column is where SQL physically stores the reference —
`Relationship()` alone creates no column. viur-models consumes the FK
field: the API shows **one** `category` bone (`relational.<kind>`),
`category_id` never leaves the model. Bone parameters live on the FK
field (a `Relationship()` cannot carry any); `required` derives from the
FK's nullability (`int` instead of `int | None` → required).

Which target fields appear in `relskel`/`dest` is the target's decision:

```python
class ExampleCategory(Model, table=True):
    viur_ref_keys = ("name",)   # default — the refKeys analogue
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
    class ExampleEntryTagLink(SQLModel, table=True):   # plain link table
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

Many-to-many via a link table is the SQL shape of `multiple=True`. There
is no FK field to carry bone parameters, so they come from the owning
model's `viur_relation_meta`. Values dump as a list of `{"dest": …}`
objects; client input takes key lists (an empty value clears the list).
Multiple bones sort after the regular fields.

## StringBone / TextBone with languages

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

`Language[X]` is a wrapper type — like `list[X]`, the type describes the
data structure (a `{lang: value}` dict, stored as a JSON column). The
bone shape comes from the inner type (`str`/`Text`), plus the `languages`
list from `Field(languages=…)` or a project-wide
`set_default_languages("de", "en")` at app boot. Dumps normalize to all
declared languages; client input is accepted dotted (`title.de=…`) and as a
dict — a partial dotted submission merges into the stored value, the other
languages survive an edit.

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

Structure parity is complete (complexity `tests`, `test_threshold`), and
the field is **write-only** — dumps emit `""`. What viur-models does
*not* do is hash: transform the incoming value in your module's
`onAdd`/`onEdit` hooks (viur-core uses PBKDF2) before it hits the
database.

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

`Spatial(...)` is a type **factory** (bounds are per-field). Values are
`(lat, lng)` pairs stored as a JSON list; client input is accepted dotted
(`pos.lat=…&pos.lng=…`, like the real bone) and as a two-element list.
The map-tile *query* logic of `SpatialBone` has no SQL counterpart —
filter via `sqlFilter` if you need geo queries.

## RelationalBone with MultipleConstraints

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

The structure emits the constraints dict exactly like the bone;
`viur_from_client` enforces them (too few/too many entries, duplicates)
with `Invalid` errors before anything touches the database.

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

    class Address(Record):              # Model without table=True/system fields
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

Records are **plain pydantic nesting** — `Record` is the `RelSkel`
analogue: a `Model` without `table=True` and without the system
fields (no identity, no key). Validation, error paths (`["address", "street"]`)
and the dump shape (the plain values dict, no wrapper) come natively.
`list[Address]` is the `multiple=True` shape. viur-models adds only the
structure entry (`type: record` + `using`, unindexed like the bone) and
the `RecordJSON` column type, which serializes instances to JSON on
write and validates them back on read. Client input works as nested
JSON and dotted (`address.street=…`) for single records.

## pydantic ecosystem types

Where pydantic already ships a semantic type, the registry maps it — the
validated type **is** the declaration:

```python
from pydantic import AnyUrl, EmailStr, constr
from pydantic_extra_types.color import Color

mail: EmailStr | None = Field(default=None)                    # str.email
site: AnyUrl | None = Field(default=None, sa_type=String)      # uri
tint: Color | None = Field(default=None, sa_type=String)       # color
short: constr(max_length=12) | None = Field(default=None)      # str, maxlength 12
```

Notes (verified against pydantic 2.13 / pydantic-extra-types 2.x):

- **`constr` / `conint` / `condecimal` need no registration at all** —
  their constraints land in `FieldInfo.metadata` and feed the regular
  derivation (`maxlength`, `min`/`max`, `precision`). Exclusive bounds
  (`PositiveInt`, `conint(gt=…)`) convert exactly for integers
  (`gt=0` → `min: 1`); floats keep the int64 defaults there.
- **More types that just work:** `StrictStr`/`StrictInt`/…,
  `AwareDatetime`/`PastDate`/`FutureDate` (→ `date`), `ByteSize`
  (int subclass → `numeric`, parses `"1.5MiB"`), extra-types'
  `Epoch.Integer` (validates epoch numbers into datetimes → `date`),
  and every str-subclass extra type (`MacAddress`, `ISBN`,
  `TimeZoneName`, `ISO4217`, `DomainStr`, …) as a validated `str` bone —
  give them their own type string with one `BoneType` alias if a client
  should distinguish them.
- **Known normalizations:** `AnyUrl` appends a trailing slash
  (`https://viur.dev` → `https://viur.dev/`); extra-types' `PhoneNumber`
  stores RFC3966 (`tel:+49…`) — subclass it and set `phone_format` for
  raw storage.
- `AnyUrl`/`HttpUrl` and `Color` are **not `str` subclasses** — such
  columns need an explicit `sa_type` (e.g. `sqlalchemy.String`); dumps
  stringify the objects.
- **Do not use** `SecretStr` (its masked `str()` would dump/store
  literal asterisks — use `Password`/`Credential`, they enforce
  write-only correctly) or `pydantic.Json` (it validates a JSON
  *string*, not a dict — use `viur.models.Json`). Both fail fast as
  unmappable. `FilePath`/`DirectoryPath` validate **server** paths and
  have nothing to do with `FileBone`.
- The thin `viur.models` aliases (`Email`, `Uri`, `Color`, `Phone`)
  remain for the zero-extra-validation case; both spellings emit the
  same bone.

## RelationalBone with using (edge payload)

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

    class EntryTagLink(RelationLink, table=True):        # association object
        __tablename__ = "example_entry_tag"
        entry_id: int | None = Field(default=None, foreign_key="example_entry.id", primary_key=True)
        tag_id: int | None = Field(default=None, foreign_key="example_tag.id", primary_key=True)
        tag: ExampleTag = Relationship()                 # the dest side
        weight: int = Field(default=0, ge=0, le=10, descr="Gewichtung")

    tags: list[EntryTagLink] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},   # required
    )
    ```

Edge payload is the association-object pattern: the link table's payload
columns **are** the using-skel (structure-parity-verified against
`RelationalBone(using=RelSkel)`). Dumps carry `{"dest": …, "rel": {…}}`;
client input is the bone's wire shape (`[{"dest": {"key": …}, "rel":
{…}}, …]`; plain keys take the payload defaults), payload validation
errors keep the bone path (`["tags", "rel", "weight"]`). The link model
declares exactly one to-one `Relationship` to the target (the dest side);
`SkeletonLink` subclasses can carry payload fields the same way for
cross-store references.

## Cross-store references (UserBone / FileBone / TreeBones)

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

`SkeletonRef(kind, ref_keys=…)` references a **datastore skeleton** from a
SQL model. The stored value is the `dest` snapshot (encoded datastore key +
the `ref_keys` values) in a JSON column — the same denormalization the real
`RelationalBone` writes into its entity. The `relskel` structure is resolved
through the real skeleton registry (`RefSkel.fromSkel`, always including
`key`/`shortkey`), so structure parity is exact. `SkeletonRef(kind,
multiple=True)` gives the list shape as a JSON array in one column;
for the `link_model` shape — one queryable row per reference — derive a
link table from `SkeletonLink`:

```python
class EntryFeedbackLink(SkeletonLink, table=True):
    __tablename__ = "example_entry_feedback"
    viur_kind = "feedback"
    viur_link_ref_keys = ("subject",)
    entry_id: int | None = Field(default=None, foreign_key="example_entry.id", primary_key=True)

feedback_history: list[EntryFeedbackLink] = Relationship(
    sa_relationship_kwargs={"cascade": "all, delete-orphan"},   # required
)
```

The base carries the datastore `key` (part of the primary key) and the
`dest` snapshot; bone parameters come from `viur_relation_meta` (incl.
`multiple` constraints). Both shapes emit the identical bone structure.

On client input (an opaque datastore key, or the dump's
`{"dest": {"key": …}}`) the target is read from the datastore and the
snapshot rebuilt — an unknown key is rejected; a roundtripped snapshot whose
target has been deleted is kept. Snapshots go stale between edits — two repair
paths, mirroring core's `updateRelations`:

- **Automatic**: `install_refresh_hooks()` at boot wraps
  `Skeleton.postSavedHandler`/`postDeletedHandler` and defers
  `refresh_for_target` for referenced kinds (deletes default to
  `missing="set_null"`), resolving the affected rows over the
  `viur_models_relations` reverse index that `SQLList` maintains. A skeleton
  overriding the handler without `super()` must call it from its own
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

- **Full sweep**: `refresh_crossstore(Model, missing=…)` — table scan for
  JSON columns, `key` lookup for `SkeletonLink` tables.

`set_null` clears single references, drops list entries and deletes
`SkeletonLink` rows. `FileRef` is a reference — upload/serving stays with the
file module.

## System bones (automatic)

=== "Skeleton"

    ```python
    # key, creationdate, changedate — added by the Skeleton base
    ```

=== "Model"

    ```python
    # id (emitted as the "key" bone), creationdate, changedate —
    # come from the Model base class; nothing to declare.
    ```

`key` is an opaque encoded string (never the raw primary key);
`creationdate`/`changedate` are readonly compute dates, pinned against
the real system bones.

## Not mapped (and what to do instead)

| Bone | Why | Workaround / plan |
|---|---|---|
| `CaptchaBone` | request-time verification, **no storage** | belongs to the form/anti-abuse layer, not the model |
| `SpamBone` | honeypot field, request-time | same as CaptchaBone |
| `RandomSliceBone` | query *behavior* (random sampling), not a field | in SQL simply: `def sqlFilter(self, stmt): return stmt.order_by(func.random())` |
| `BaseBone` (`"hidden"`) | raw hidden storage | one alias away: `Hidden = t.Annotated[str, BoneType("hidden", replace=True)]` |

## Emitted structure keys

Every bone carries the same base keys, pinned against viur-core 3.9:

`descr`, `type`, `required`, `params`, `visible`, `readonly`, `unique`,
`languages`, `emptyvalue`, `indexed`, `clone_behavior`, `multiple` — plus
`defaultvalue` where one exists, and the `sortindex` that
`SkeletonInstance.structure()` adds.

Per bone family, on top of those:

| Family | Additional keys |
|---|---|
| `str` | `maxlength` (default 254), `minlength` |
| `numeric` | `min` / `max` (int64 bounds), `precision`, `decimal` |
| `date` | `date`, `time`, `naive` |
| `select` | `values` as a `{value: label}` dict |

`clone_behavior` defaults to `{"strategy": "copy_value"}` and is
`{"strategy": "set_default"}` for the bones that regenerate on clone
(`Uid`, `SortIndex`). `emptyvalue` is `""` for the string family and `None`
otherwise; multiple bones carry `defaultvalue: []`. `emptyvalue` decides how a
cleared form input is read (`""` clears bones whose emptyvalue is not `""`).

The unit suite pins this with a golden file; the integration suite compares
it against the real bones.

## Common bone parameters

| Bone parameter | Field equivalent |
|---|---|
| `descr` | `Field(descr=…)` (default: title-cased field name) |
| `required` | derived from the type; override with `required=` |
| `defaultValue` | plain `default=` / `default_factory=` |
| `visible=False` | `Field(visible=False)` |
| `readOnly=True` | `Field(readonly=True)` (forces `required: false`, like `BaseBone`) |
| `params` | `Field(params={…})` |
| `unique` | `Field(unique=True)` — enforced by SQL; the structure emits `False` |
| `indexed` | `Field(index=…)` (structure default `True`, like datastore) |
| `languages` | `Language[X]` wrapper type + `Field(languages=…)` or `set_default_languages()` |
| `multiple` constraints | `viur_relation_meta = {"rel": {"multiple": {"min": …, "max": …, "duplicates": …}}}` |
| `using` relations | association object: derive the link table from `RelationLink`, its payload columns ARE the using-skel |

## Custom bone types

Where no Python type exists, one `Annotated` alias creates it; where the
pydantic ecosystem has a semantic type, register it:

```python
from viur.models import BoneType, register_bone_type

Slug = t.Annotated[str, BoneType("str.slug")]          # refines str extras
Hidden = t.Annotated[str, BoneType("hidden", replace=True)]  # defines them alone

register_bone_type(SomePydanticType, BoneType("select.something"))
```

An `Annotated` marker on the field wins over the registry; registry
lookup walks the type's MRO, so subclasses inherit their base's mapping.
Markers can override any structure key via `extras` (that is how `Code`
sets `indexed: false` and `Uid` its readonly/unique/compute semantics).
