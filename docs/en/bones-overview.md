# Bone overview

Compact reference for how every viur-core bone is declared as a
viur-models field. The rule: **the Python type decides the bone** — never a
string parameter. Everything pydantic and SQL already express
(`max_length`, `ge`/`le`, optionality, defaults) is derived; only the
ViUR-specific parts (`descr`, `visible`, `params`, …) go through `Field`.
Optionality as usual via `| None` + `default=None`. All derivation rules in
detail: [bone reference](bones.md); runnable example:
`deploy/models/example.py`.

## Full overview

### Scalars

| Bone | Type string | Field definition |
|---|---|---|
| `StringBone` | `str` | `name: str = Field(descr="Name", max_length=100)` |
| `NumericBone` (int) | `numeric` | `rating: int \| None = Field(default=None, ge=1, le=5)` |
| `NumericBone` (float/Decimal) | `numeric` | `price: Decimal \| None = Field(default=None, decimal_places=2)` |
| `BooleanBone` | `bool` | `active: bool = Field(default=True)` |
| `DateBone` | `date` | `due: datetime \| None` · `day: date \| None` · `slot: time \| None` |

### Selects

| Bone | Type string | Field definition |
|---|---|---|
| `SelectBone` (Enum) | `select` | `kind: EntryKind = Field(descr="Kind")` |
| `SelectBone` (Literal) | `select` | `status: Literal["new","done"] \| None = Field(values={…}, sa_type=String)` |
| `SelectCountryBone` | `select.country` | `country: Country \| None = Field(default=None)` |

### String refinements (the type carries the bone)

| Bone | Type string | Field definition |
|---|---|---|
| `TextBone` | `text` | `message: Text = Field(default="", required=False)` |
| `EmailBone` | `str.email` | `mail: Email \| None = Field(default=None)` |
| `PhoneBone` | `str.phone` | `phone: Phone \| None = Field(default=None, max_length=15)` |
| `UriBone` | `uri` | `site: Uri \| None = Field(default=None)` |
| `ColorBone` | `color` | `tint: Color \| None = Field(default=None)` |
| `RawBone` | `raw` | `blob: Raw \| None = Field(default=None)` |
| `CodeBone`/`Jinja`/`Logics`/`Python` | `raw.code` | `template: Code \| None = Field(default=None)` |
| `JsonBone` | `raw.json` | `data: Json \| None = Field(default=None, sa_type=JSON)` |
| `UidBone` | `uid` | `uid: Uid \| None = Field(default=None)` — ⚠️ value generated in a hook |
| `SortIndexBone` | `numeric.sortindex` | `sortindex: SortIndex \| None = Field(default=None)` |
| `CredentialBone` | `str.credential` | `secret: Credential \| None = Field(default=None, visible=False)` — write-only |
| `PasswordBone` | `password` | `pwd: Password \| None = Field(default=None)` — write-only, ⚠️ hashing in a hook |
| `SpatialBone` | `spatial` | `pos: Spatial(bounds_lat=…, bounds_lng=…) \| None = Field(sa_type=JSON)` |

### Multilingual

| Bone | Field definition |
|---|---|
| `StringBone(languages=…)` | `title: Language[str] = Field(languages=("de","en"), sa_type=JSON)` |
| `TextBone(languages=…)` | `body: Language[Text] \| None = Field(default=None, sa_type=JSON)` |

The language list comes from `languages=` or project-wide from
`set_default_languages("de", "en")`. Input is accepted dotted
(`title.de=…`) and as a dict; the value is a `{lang: value}` dict.

### Relations (SQL → SQL)

| Bone | Field definition |
|---|---|
| `RelationalBone` | FK field (carries the bone parameters) + `Relationship()`:<br>`category_id: int \| None = Field(default=None, foreign_key="example_category.id", descr="Category")`<br>`category: ExampleCategory \| None = Relationship()` |
| `RelationalBone(multiple=True)` | `tags: list[ExampleTag] = Relationship(link_model=EntryTagLink)` |
| `RelationalBone(using=RelSkel)` | Association object — the link table's payload columns ARE the using RelSkel:<br>`class EntryTagLink(RelationLink, table=True): … tag: ExampleTag = Relationship(); weight: int = Field(ge=0, le=10)`<br>`tags: list[EntryTagLink] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})` |
| `RecordBone` / `AddressBone` | plain pydantic nesting: `class Address(Record): …`<br>`address: Address \| None = Field(default=None, sa_type=RecordJSON(Address), format="$(street)")`<br>multiple: `stops: list[Address] = Field(default_factory=list, sa_type=RecordJSON(Address))` |

`required` is derived from the FK nullability; `viur_ref_keys = ("name",)`
on the **target** decides relskel/dest. For multiple and using,
`cascade="all, delete-orphan"` is mandatory.

### Cross-store (SQL → datastore skeleton)

What is stored is the dest snapshot (key + ref_keys values) as JSON; the
read on input rebuilds the snapshot and doubles as the existence check.
Deleted targets do not block edits — the old snapshot stays. Changes to
referenced skeletons propagate automatically: call
`viur.models.install_refresh_hooks()` once at boot. It wraps the skeleton
postSaved/postDeleted handlers and defers a targeted `refresh_for_target`
over the `viur_models_relations` index table, the `viur-relations`
equivalent; deletions default to `set_null`. For bulk repair there is
`refresh_crossstore(Model, missing=…)` as a cron safety net.

| Bone | Field definition |
|---|---|
| `UserBone` | `owner: UserRef() \| None = Field(default=None, sa_type=JSON)` |
| `FileBone`/`ImageBone` | `upload: FileRef() \| None = Field(default=None, sa_type=JSON)` — a reference, not an upload |
| `TreeLeafBone`/`TreeNodeBone` | `node: SkeletonRef(kind, type_suffix="tree.node") \| None` |
| any skeleton | `ref: SkeletonRef("feedback", ref_keys=("subject",)) \| None = Field(sa_type=JSON)` |
| multiple (JSON array) | `refs: SkeletonRef("feedback", multiple=True) \| None = Field(sa_type=JSON)` |
| multiple (link table) | `class FbLink(SkeletonLink, table=True): viur_kind = "feedback"; …`<br>`history: list[FbLink] = Relationship(sa_relationship_kwargs={"cascade": "all, delete-orphan"})` |

### System and other

| Bone | Field definition |
|---|---|
| `KeyBone` / `creationdate` / `changedate` | automatic from the `Model` base — declare nothing |
| `BaseBone` ("hidden") | `Hidden = Annotated[str, BoneType("hidden", replace=True)]` |
| `CaptchaBone` / `SpamBone` | request-time checks, nothing persisted — not applicable |
| `RandomSliceBone` | query behaviour, not a field: `def sqlFilter(self, stmt): return stmt.order_by(func.random())` |

## Common bone parameters

| Bone parameter | Field equivalent |
|---|---|
| `descr` | `Field(descr=…)` — defaults to the title-cased field name |
| `required` | derived (non-optional without a default); override with `required=` |
| `defaultValue` | `default=` / `default_factory=` |
| `visible` / `readOnly` | `visible=False` / `readonly=True` (forces `required: false`) |
| `params` | `params={…}` |
| `languages` | `Language[X]` + `languages=(…)` or `set_default_languages()` |
| `multiple` constraints | `viur_relation_meta = {"rel": {"multiple": {"min": …, "max": …, "duplicates": …}}}` |
| `format` | `format="$(dest.name)"` — records and relations |
| `refKeys` | `viur_ref_keys = ("name",)` on the target model (key/shortkey always included) |
| `unique` / `indexed` | `unique=True` / `index=…` — enforced by SQL |

## Custom types and the pydantic ecosystem

```python
# A bone type with no Python counterpart: one Annotated line
Slug = Annotated[str, BoneType("str.slug")]                # refines the str extras
Hidden = Annotated[str, BoneType("hidden", replace=True)]  # defines the bone alone

# Ecosystem types are registered — the validated type IS the declaration:
mail: EmailStr | None = Field(default=None)                # str.email
site: AnyUrl | None = Field(default=None, sa_type=String)  # uri
short: constr(max_length=12) | None = Field(default=None)  # str, maxlength 12

register_bone_type(MyType, BoneType("select.something"))   # MRO lookup, subclasses inherit
```

Also working natively: `Strict*`, `PositiveInt`/`conint(gt=…)` (exclusive
bounds become exact `min`/`max` for ints), `ByteSize`,
`AwareDatetime`/`PastDate`, extra-types `Epoch.Integer` and every
str-subclass extra type (`MacAddress`, `ISBN`, `TimeZoneName`, `ISO4217`, …)
as validated str bones. **Do not use:** `SecretStr` (masks dumps — take
`Password`/`Credential`) and `pydantic.Json` (validates JSON *strings* —
take `viur.models.Json`). Mind the normalizations: `AnyUrl` appends a
slash, `PhoneNumber` stores RFC3966 (`tel:+49…`).
