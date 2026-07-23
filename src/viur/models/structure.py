"""Structure mapping — SQLModel/pydantic field definitions → ViUR bone structure.

Emits, per field, the same JSON-serializable dict that
``viur.core.bones.BaseBone.structure()`` (and its specializations) would
produce, so clients cannot tell a SQL-backed module from a skeleton-backed
one. The emitted key set and defaults are pinned against viur-core 3.9:

- base keys: ``descr, type, required, params, visible, readonly, unique,
  languages, emptyvalue, indexed, clone_behavior, multiple`` (+ optional
  ``defaultvalue``) — plus ``sortindex`` per ``SkeletonInstance.structure()``
- ``str`` → ``maxlength`` (default 254), ``minlength``
- ``numeric`` → ``min``/``max`` (int64 bounds), ``precision``, ``decimal``
- ``date`` → ``date``, ``time``, ``naive``
- ``select`` → ``values`` (new-style ``{value: label}`` dict)

The integration suite compares this against the real bones; the unit suite
pins it with a golden file.
"""
import datetime
import decimal
import enum
import types
import typing as t

from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from sqlmodel import SQLModel

from .crossstore import SkeletonLink, SkeletonRefMarker
from .links import RelationLink
from .fields import VIUR_META_KEY
from .types import BONE_TYPE_REGISTRY, BoneType, LanguageWrapper
from . import crossstore as _crossstore
from . import types as _types

#: Model class → name of the module serving it (filled by ``SQLList`` at
#: construction). Relational bones emit it as their ``module`` so admin
#: clients query the RIGHT module for selections — the kind/table name is
#: only the fallback for unserved targets.
MODULE_BY_MODEL: dict[type, str] = {}

# Defaults pinned against viur-core 3.9 bone implementations.
NUMERIC_MIN = -9223372036854775806
NUMERIC_MAX = 9223372036854775807
STRING_MAXLENGTH = 254
FLOAT_PRECISION = 8
CLONE_BEHAVIOR = {"strategy": "copy_value"}


def _viur_meta(field_info: t.Any) -> dict:
    """Bone metadata a ViURField left in ``json_schema_extra["viur"]``."""
    extra = field_info.json_schema_extra
    if isinstance(extra, dict):
        return dict(extra.get(VIUR_META_KEY) or {})
    return {}


def _unwrap_annotation(
    annotation: t.Any,
) -> tuple[t.Any, BoneType | None, LanguageWrapper | None, tuple]:
    """Core type of ``T | None`` / ``Optional[T]`` / ``Annotated[T, …]``.

    pydantic moves top-level ``Annotated`` metadata into
    ``FieldInfo.metadata``, but leaves it in place inside a union arm
    (``Email | None``) — so :class:`BoneType` and :class:`LanguageWrapper`
    markers are extracted here as well and returned alongside the core type.
    """
    if t.get_origin(annotation) in (t.Union, types.UnionType):
        args = [arg for arg in t.get_args(annotation) if arg is not type(None)]
        if len(args) != 1:
            raise TypeError(
                f"Union type {annotation!r} has no bone mapping — "
                "only ``T | None`` is supported"
            )
        annotation = args[0]
    marker = language = None
    metadata = getattr(annotation, "__metadata__", None) or ()  # Annotated[…]
    if metadata:
        marker = next((item for item in metadata if isinstance(item, BoneType)), None)
        language = next((item for item in metadata if isinstance(item, LanguageWrapper)), None)
        annotation = annotation.__origin__
    return annotation, marker, language, tuple(metadata)


def _constraints(field_info: t.Any, annotated_metadata: tuple = ()) -> dict:
    """Collect relevant validation constraints from ``FieldInfo.metadata``
    plus metadata still sitting inside a union-arm ``Annotated`` (e.g.
    ``constr(max_length=…) | None`` — pydantic leaves those in place)."""
    out = {}
    for item in (*field_info.metadata, *annotated_metadata):
        for attr in ("max_length", "min_length", "ge", "le", "gt", "lt", "decimal_places"):
            if (value := getattr(item, attr, None)) is not None:
                out[attr] = value
    return out


def _numeric(cons: dict, precision: int, decimal_mode: bool) -> dict:
    # NOTE: ``decimal`` is NOT ``precision > 0`` — the real NumericBone
    # keeps ``decimal: false`` for floats (e.g. SortIndexBone: precision 8,
    # decimal false); it only flips for exact-decimal mode.
    minimum, maximum = cons.get("ge"), cons.get("le")
    if precision == 0:
        # exclusive Gt/Lt (PositiveInt, conint(gt=…)) convert exactly for
        # integers; for floats there is no exact inclusive bound, so only
        # ge/le feed the structure hint there (validation still applies).
        if minimum is None and (exclusive := cons.get("gt")) is not None:
            minimum = int(exclusive) + 1
        if maximum is None and (exclusive := cons.get("lt")) is not None:
            maximum = int(exclusive) - 1
    return {
        "type": "numeric",
        "emptyvalue": 0,
        "min": NUMERIC_MIN if minimum is None else minimum,
        "max": NUMERIC_MAX if maximum is None else maximum,
        "precision": precision,
        "decimal": decimal_mode,
    }


def _select(core_values: dict, meta: dict) -> dict:
    values = meta.get("values") or core_values
    return {
        "type": "select",
        "emptyvalue": None,
        "values": {key: str(label) for key, label in values.items()},
    }


def crossstore_fields(cls: type) -> dict:
    """Field names carrying a :class:`SkeletonRefMarker` (cross-store
    references into the datastore world) → their marker."""
    out = {}
    for name, field_info in cls.model_fields.items():
        _, _, _, annotated_metadata = _unwrap_annotation(field_info.annotation)
        marker = next(
            (item for item in (*annotated_metadata, *field_info.metadata)
             if isinstance(item, SkeletonRefMarker)),
            None,
        )
        if marker is not None:
            out[name] = marker
    return out


def _analyze_relation_link(parent_table: t.Any, link_cls: type) -> dict:
    """Derive dest side and using fields from an association-object link.

    The link's FK pointing at the parent table is the parent side; its
    single to-one relationship is the ``dest`` side; every scalar field
    that is not one of the two FK columns is edge payload (the using-skel).
    """
    link_mapper = link_cls.__mapper__
    parent_fk = next(
        (column.key for column in link_mapper.local_table.columns
         if any(fk.references(parent_table) for fk in column.foreign_keys)),
        None,
    )
    dest_props = [
        prop for prop in link_mapper.relationships.values()
        if not prop.uselist and prop.mapper.local_table is not parent_table
    ]
    if parent_fk is None or len(dest_props) != 1:
        raise TypeError(
            f"association link {link_cls.__name__!r} needs a FK to its "
            "parent and exactly ONE to-one Relationship() to the target "
            "(the dest side)"
        )
    dest_prop = dest_props[0]
    dest_fk = next(
        local.key for local, remote in dest_prop.local_remote_pairs
        if local.table is link_mapper.local_table
    )
    using_fields = [
        name for name in link_cls.model_fields
        if name not in (parent_fk, dest_fk)
    ]
    return {
        "fk": None,
        "target": dest_prop.mapper.class_,
        "required": False,
        "multiple": True,
        "link": link_cls,
        "dest_fk": dest_fk,
        "dest_rel": dest_prop.key,
        "using_fields": using_fields,
    }


def _using_structure(link_cls: type, using_fields: list) -> dict:
    """The ``using`` structure — the link model's payload fields, mapped
    like any other fields (they ARE the using-skel)."""
    return {
        name: _bone_structure(name, link_cls.model_fields[name]) | {"sortindex": index}
        for index, name in enumerate(using_fields)
    }


def _crossstore_structure(marker: SkeletonRefMarker, meta: dict) -> dict:
    """A ``relational.<kind>`` bone entry for a datastore reference —
    ``relskel`` comes from the REAL skeleton registry (lazy)."""
    suffix = f"{marker.type_suffix}." if marker.type_suffix else ""
    entry = {
        "type": f"relational.{suffix}{marker.kind}",
        "emptyvalue": None,
        "multiple": marker.multiple,
        "module": marker.module,
        "format": meta.get("format") or marker.format or "$(dest.name)",
        "using": None,
        "relskel": _crossstore.resolve_relskel(marker),
    }
    if marker.multiple:
        entry["defaultvalue"] = []
        if isinstance(constraints := meta.get("multiple"), dict):
            entry["multiple"] = {
                "duplicates": bool(constraints.get("duplicates", False)),
                "max": int(constraints.get("max", 0)),
                "min": int(constraints.get("min", 0)),
            }
    if marker.extras:
        entry |= marker.extras
    return entry


def _record(target: type, meta: dict, *, multiple: bool) -> dict:
    """A ``record`` bone entry — pinned against ``RecordBone``.

    Records are **plain pydantic nesting**: the nested (non-table) SQLModel
    is the ``using``-skel analogue — validation, error paths and the dump
    shape (the plain values dict) all come natively from pydantic. Only
    the structure entry is built here.
    """
    if getattr(target, "__table__", None) is not None:
        raise TypeError(
            f"record target {target.__name__!r} is a table model — table "
            "models are relations, use Relationship() (analysis/01 §5.4)"
        )
    ret = {
        "type": "record",
        "emptyvalue": None,
        "multiple": multiple,
        "indexed": False,  # like RecordBone
        "format": meta.get("format"),  # RecordBone default: None
        "using": structure_for_model(target),
    }
    if multiple:
        ret["defaultvalue"] = []  # like multiple bones
    return ret


def _bone_type_marker(
    core: t.Any, field_info: t.Any, annotated_marker: BoneType | None,
) -> BoneType | None:
    """Find a bone-type override: ``Annotated`` marker first, then registry.

    The registry is walked along the type's MRO, so subclasses inherit the
    bone mapping of their base type (e.g. everything derived from
    ``CountryAlpha2`` stays a ``select.country``).
    """
    if annotated_marker:
        return annotated_marker
    for item in field_info.metadata:
        if isinstance(item, BoneType):
            return item
    for klass in getattr(core, "__mro__", ())[:-1]:  # object never maps
        if marker := BONE_TYPE_REGISTRY.get(klass):
            return marker
    return None


def _bone_for_type(core: t.Any, cons: dict, meta: dict, marker: BoneType | None) -> dict:
    """Bone ``type`` + type-specific structure keys for a core Python type.

    The bone type is decided by the **type**, never by a string parameter —
    dedicated types (``Text``, ``Email``, ``Country``, …) carry a
    :class:`BoneType` marker that either refines the base type's structure
    (``replace=False``) or defines it alone (``replace=True``).
    """
    if marker and marker.replace:
        return {"type": marker.name, "emptyvalue": marker.emptyvalue, **(marker.extras or {})}

    def _marker_only_or_raise() -> dict:
        # A registered semantic class (pydantic AnyUrl, EmailStr, …) may not
        # subclass a dispatchable base — the marker alone defines the bone.
        if marker:
            return {"type": marker.name, "emptyvalue": marker.emptyvalue, **(marker.extras or {})}
        raise TypeError(
            f"type {core!r} has no bone mapping — see analysis/01 §5.2 "
            "(map a dedicated type via viur.models.types)"
        )

    # NOTE: order matters — enum.Enum before str/int (StrEnum/IntEnum),
    # bool before int, datetime before date.
    if not isinstance(core, type):
        origin = t.get_origin(core)
        args = t.get_args(core)
        if origin is t.Literal:
            ret = _select({value: str(value) for value in args}, meta)
        elif origin is list and args and isinstance(args[0], type) \
                and issubclass(args[0], SQLModel):
            ret = _record(args[0], meta, multiple=True)  # list[Record]
        else:
            return _marker_only_or_raise()
    elif issubclass(core, enum.Enum):
        ret = _select(
            {member.value: member.name.replace("_", " ").title() for member in core},
            meta,
        )
    elif issubclass(core, bool):
        ret = {"type": "bool", "emptyvalue": False}
    elif issubclass(core, str):
        ret = {
            "type": "str",
            "emptyvalue": "",
            "maxlength": cons.get("max_length", STRING_MAXLENGTH),
            "minlength": cons.get("min_length"),
        }
    elif issubclass(core, int):
        ret = _numeric(cons, precision=0, decimal_mode=False)
    elif issubclass(core, float):
        ret = _numeric(
            cons, precision=cons.get("decimal_places", FLOAT_PRECISION), decimal_mode=False,
        )
    elif issubclass(core, decimal.Decimal):
        ret = _numeric(
            cons, precision=cons.get("decimal_places", FLOAT_PRECISION), decimal_mode=True,
        )
    elif issubclass(core, datetime.datetime):
        ret = {"type": "date", "emptyvalue": None, "date": True, "time": True, "naive": False}
    elif issubclass(core, datetime.date):
        ret = {"type": "date", "emptyvalue": None, "date": True, "time": False, "naive": False}
    elif issubclass(core, datetime.time):
        ret = {"type": "date", "emptyvalue": None, "date": False, "time": True, "naive": False}
    elif issubclass(core, SQLModel):
        ret = _record(core, meta, multiple=False)  # nested model = record
    else:
        return _marker_only_or_raise()

    if marker:
        ret["type"] = marker.name
        ret.update(marker.extras or {})
    return ret


def _key_bone() -> dict:
    """The system ``key`` bone — pinned against ``KeyBone`` defaults."""
    return {
        "descr": "Key",
        "type": "key",
        "required": False,
        "params": {},
        "visible": False,
        "readonly": True,
        "unique": False,
        "languages": None,
        "emptyvalue": None,
        "indexed": True,
        "clone_behavior": dict(CLONE_BEHAVIOR),
        "multiple": False,
    }


def _bone_structure(name: str, field_info: t.Any) -> dict:
    meta = _viur_meta(field_info)
    core, annotated_marker, annotated_language, annotated_metadata = \
        _unwrap_annotation(field_info.annotation)

    readonly = bool(meta.get("readonly", False))
    required = meta["required"] if "required" in meta else field_info.is_required()

    bone = {
        "descr": meta.get("descr") or field_info.title or name.replace("_", " ").title(),
        # BaseBone parity: readOnly forces required off in the structure.
        "required": bool(required) and not readonly,
        "params": meta.get("params") or {},
        "visible": bool(meta.get("visible", True)),
        "readonly": readonly,
        "unique": False,
        "languages": None,
        "indexed": getattr(field_info, "index", None) is not False,
        "clone_behavior": dict(CLONE_BEHAVIOR),
        "multiple": False,
    }

    # Cross-store reference (SkeletonRef) — the value is the dest snapshot
    # of a datastore skeleton; the bone is relational.<kind>.
    cross = next(
        (item for item in (*annotated_metadata, *field_info.metadata)
         if isinstance(item, SkeletonRefMarker)),
        None,
    )
    if cross is not None:
        bone |= _crossstore_structure(cross, meta)
        return bone

    # ``Language[X]`` wrapper — the type describes the data structure
    # ({lang: value} dict); the bone shape comes from the INNER type, plus
    # the languages list (StringBone(languages=…) analogue).
    language = annotated_language or next(
        (item for item in field_info.metadata if isinstance(item, LanguageWrapper)), None,
    )
    if language is not None:
        langs = meta.get("languages") or _types.DEFAULT_LANGUAGES
        if not langs:
            raise TypeError(
                f"Language field {name!r} needs ViURField(languages=…) or "
                "viur.models.set_default_languages(…)"
            )
        inner_core, inner_marker, _, inner_metadata = _unwrap_annotation(language.inner)
        bone |= _bone_for_type(
            inner_core, _constraints(field_info, inner_metadata), meta,
            inner_marker or _bone_type_marker(inner_core, field_info, None),
        )
        bone["languages"] = list(langs)
        # language bones default to a per-language None dict (real-bone parity)
        default = field_info.default
        bone["defaultvalue"] = (
            default if default is not PydanticUndefined and default is not None
            else {lang: None for lang in langs}
        )
        return bone
    if meta.get("languages"):
        raise TypeError(
            f"{name!r}: ViURField(languages=…) requires a Language[…] annotation"
        )

    marker = _bone_type_marker(core, field_info, annotated_marker)
    bone |= _bone_for_type(core, _constraints(field_info, annotated_metadata), meta, marker)

    default = field_info.default
    if default is not PydanticUndefined and default is not None:
        bone["defaultvalue"] = default.value if isinstance(default, enum.Enum) else default

    if compute := meta.get("compute"):
        bone["compute"] = compute

    return bone


def _computed_bone_structure(name: str, computed_info: t.Any) -> dict:
    """Bone entry for a pydantic ``@computed_field`` — the ``compute``
    (method ``Always``) analogue: the value is derived from the instance at
    dump time and never stored, so the bone is always read-only and not
    indexed (it has no column to filter or sort on).

    The bone shape comes from the property's **return annotation**, through
    the same mapping as regular fields (semantic types like ``Text`` work).
    Bone parameters travel in the decorator's ``json_schema_extra`` under
    the ``"viur"`` key (``descr``, ``visible``, ``params``, ``format``, …)::

        @computed_field(json_schema_extra={"viur": {"descr": "Anzeigename"}})
        @property
        def display_name(self) -> str:
            return f"{self.name} ({self.kind.value})"
    """
    field_info = FieldInfo.from_annotation(computed_info.return_type)
    extra = computed_info.json_schema_extra
    meta = dict(extra.get(VIUR_META_KEY) or {}) if isinstance(extra, dict) else {}
    meta["readonly"] = True  # never writable — there is no setter
    meta.setdefault("compute", {"method": "Always"})
    field_info.title = computed_info.title
    field_info.json_schema_extra = {VIUR_META_KEY: meta}
    bone = _bone_structure(name, field_info)
    bone["indexed"] = False
    return bone


def relations_for_model(cls: type) -> dict:
    """Mapped relationships: ``rel_name → {"fk", "target", "required", "multiple"}``.

    Derived from the SQLAlchemy mapper, so it exists only on table models.
    Two shapes are mapped:

    - **to-one** (FK column on this table): ``fk`` names the consumed raw
      field, the column's nullability drives ``required``.
    - **many-to-many** (link table via ``Relationship(link_model=…)``):
      ``fk`` is ``None``, ``multiple`` is ``True`` — the ``RelationalBone
      multiple`` analogue.

    Inverse sides — one-to-many children lists (``uselist`` without a link
    table) and the FK-less side of a one-to-one — have no bone shape and
    are skipped.
    """
    rel_infos = getattr(cls, "__sqlmodel_relationships__", None)
    if not rel_infos:
        return {}
    mapper = getattr(cls, "__mapper__", None)
    if mapper is None:
        raise NotImplementedError(
            "relationships need a table model (table=True) — the FK column "
            "defines the mapping (analysis/01 §5.4)"
        )
    relations = {}
    for rel_name in rel_infos:
        prop = mapper.relationships[rel_name]
        if prop.uselist and prop.secondary is None \
                and issubclass(prop.mapper.class_, RelationLink):
            # association object → multiple relation WITH edge payload
            # (the RelationalBone(using=…) analogue).
            relations[rel_name] = _analyze_relation_link(
                mapper.local_table, prop.mapper.class_,
            )
            continue
        if prop.uselist and prop.secondary is None \
                and issubclass(prop.mapper.class_, SkeletonLink):
            # link-table-backed cross-store reference (one row per
            # datastore target) — the link model carries the marker; its
            # extra scalar fields are edge payload (using-skel).
            link_cls = prop.mapper.class_
            parent_fk = next(
                (column.key for column in link_cls.__mapper__.local_table.columns
                 if any(fk.references(mapper.local_table) for fk in column.foreign_keys)),
                None,
            )
            relations[rel_name] = {
                "fk": None,
                "target": link_cls,
                "required": False,
                "multiple": True,
                "crossstore": link_cls.viur_marker(),
                "using_fields": [
                    name for name in link_cls.model_fields
                    if name not in ("key", "dest", parent_fk)
                ],
            }
            continue
        if prop.secondary is not None:  # link table → multiple
            relations[rel_name] = {
                "fk": None,
                "target": prop.mapper.class_,
                "required": False,
                "multiple": True,
            }
            continue
        if prop.uselist:
            continue  # one-to-many inverse (children list)
        local_column = next(
            local for local, remote in prop.local_remote_pairs
            if local.table is mapper.local_table
        )
        if not local_column.foreign_keys:
            continue  # FK-less side of a one-to-one
        relations[rel_name] = {
            "fk": local_column.key,
            "target": prop.mapper.class_,
            "required": not local_column.nullable,
            "multiple": False,
        }
    return relations


def _shortkey_bone() -> dict:
    """The ``shortkey`` system bone real RefSkels carry (raw, computed) —
    emitted in ``relskel`` for parity; the dump does not compute it (v1)."""
    return {
        "descr": "Shortkey",
        "type": "raw",
        "required": False,
        "params": {},
        "visible": False,
        "readonly": True,
        "unique": False,
        "languages": None,
        "emptyvalue": None,
        "indexed": True,
        "clone_behavior": dict(CLONE_BEHAVIOR),
        "multiple": False,
        "compute": {"method": "Always"},
    }


def _relational_structure(
    rel_name: str, fk_field_info: t.Any, info: dict, owner_cls: type,
) -> dict:
    """One ``relational.<kind>`` bone entry — pinned against ``RelationalBone``
    (``format`` default ``"$(dest.name)"``, ``refKeys`` default ``{"name"}``).

    Bone parameters (``descr``, ``required`` override, ``visible``, …) are
    read from the **FK field's** ViURField metadata — the relationship
    attribute itself is pure SQLAlchemy and carries none. Many-to-many
    relations have no FK field; their parameters come from the owning
    model's ``viur_relation_meta`` dict (which also overrides FK metadata
    for to-one relations).
    """
    meta = _viur_meta(fk_field_info) if fk_field_info is not None else {}
    meta |= getattr(owner_cls, "viur_relation_meta", {}).get(rel_name, {})
    target = info["target"]
    kind = target._viur_kind()
    # ``module`` must name the module SERVING the target (admins query it
    # for selections) — explicit meta wins, then the SQLList registry, and
    # the kind only as fallback for unserved targets.
    module = meta.get("module") or MODULE_BY_MODEL.get(target) or kind
    readonly = bool(meta.get("readonly", False))
    required = meta["required"] if "required" in meta else info["required"]
    ref_keys = ("key", *(
        k for k in target.viur_ref_keys
        if k in target.model_fields or k in target.model_computed_fields
    ))
    relskel = {
        name: bone
        for name, bone in structure_for_model(target, include_relations=False).items()
        if name in ref_keys
    }
    relskel["shortkey"] = _shortkey_bone()
    fk_title = fk_field_info.title if fk_field_info is not None else None
    bone = {
        "descr": meta.get("descr") or fk_title or rel_name.replace("_", " ").title(),
        "type": f"relational.{kind}",
        "required": bool(required) and not readonly,
        "params": meta.get("params") or {},
        "visible": bool(meta.get("visible", True)),
        "readonly": readonly,
        "unique": False,
        "languages": None,
        "emptyvalue": None,
        "indexed": True,
        "clone_behavior": dict(CLONE_BEHAVIOR),
        "multiple": bool(info["multiple"]),
        "module": module,
        "format": meta.get("format") or "$(dest.name)",
        # association links carry edge payload — their scalar fields are
        # the using-skel (RelationalBone(using=…) analogue).
        "using": (
            _using_structure(info["link"], info["using_fields"])
            if info.get("using_fields") else None
        ),
        "relskel": relskel,
    }
    if info["multiple"]:
        # multiple bones default to an empty list (not None), which
        # BaseBone.structure therefore emits as defaultvalue.
        bone["defaultvalue"] = []
        # MultipleConstraints analogue: viur_relation_meta may carry
        # {"multiple": {"min": …, "max": …, "duplicates": …}} — emitted in
        # the bone's serialization order, enforced by viur_from_client.
        if isinstance(constraints := meta.get("multiple"), dict):
            bone["multiple"] = {
                "duplicates": bool(constraints.get("duplicates", False)),
                "max": int(constraints.get("max", 0)),
                "min": int(constraints.get("min", 0)),
            }
    return bone


def write_only_fields(cls: type) -> frozenset:
    """Field names whose :class:`BoneType` marker is ``write_only`` —
    their values never appear in dumps (Password/Credential semantics)."""
    out = set()
    for name, field_info in cls.model_fields.items():
        core, marker, _, _metadata = _unwrap_annotation(field_info.annotation)
        if marker is None:
            marker = next(
                (item for item in field_info.metadata if isinstance(item, BoneType)), None,
            )
        if marker is not None and marker.write_only:
            out.add(name)
    for name, computed_info in cls.model_computed_fields.items():
        _, marker, _, _metadata = _unwrap_annotation(computed_info.return_type)
        if marker is not None and marker.write_only:
            out.add(name)
    return frozenset(out)


def structure_for_model(cls: type, *, include_relations: bool = True) -> dict:
    """Build the skeleton-compatible structure dict for a ViURModel class.

    The primary-key field is emitted as the system ``key`` bone (clients
    never see the raw column); an FK field consumed by a to-one relationship
    is emitted as **one** ``relational.<kind>`` bone under the relationship's
    name (analysis/01 §5.4); every other field maps per analysis/01 §5.
    Unmappable types raise ``TypeError`` at class-definition time (via
    ``ViURModel.__pydantic_init_subclass__``), not at request time.

    ``include_relations=False`` builds the scalar-only structure — used for
    ``relskel`` payloads, which also breaks relation cycles (A → B → A).
    """
    relations = relations_for_model(cls) if include_relations else {}
    consumed = {
        info["fk"]: rel_name
        for rel_name, info in relations.items()
        if info["fk"] is not None
    }

    structure = {}
    for sortindex, (name, field_info) in enumerate(cls.model_fields.items()):
        if getattr(field_info, "primary_key", False) is True:
            structure["key"] = _key_bone() | {"sortindex": sortindex}
        elif name in consumed:
            rel_name = consumed[name]
            structure[rel_name] = (
                _relational_structure(rel_name, field_info, relations[rel_name], cls)
                | {"sortindex": sortindex}
            )
        else:
            structure[name] = _bone_structure(name, field_info) | {"sortindex": sortindex}

    # Many-to-many and link-backed cross-store relations have no FK field
    # to take a position from — they are appended after the regular fields,
    # in declaration order.
    sortindex = len(cls.model_fields)
    for rel_name, info in relations.items():
        if info["fk"] is not None:
            continue
        meta = getattr(cls, "viur_relation_meta", {}).get(rel_name, {})
        if marker := info.get("crossstore"):
            entry = _crossstore_structure(marker, meta)
            entry["descr"] = meta.get("descr") or rel_name.replace("_", " ").title()
            if info.get("using_fields"):
                entry["using"] = _using_structure(info["target"], info["using_fields"])
            entry = {
                "params": meta.get("params") or {},
                "required": False,
                "visible": bool(meta.get("visible", True)),
                "readonly": bool(meta.get("readonly", False)),
                "unique": False,
                "languages": None,
                "indexed": True,
                "clone_behavior": dict(CLONE_BEHAVIOR),
            } | entry
        else:
            entry = _relational_structure(rel_name, None, info, cls)
        structure[rel_name] = entry | {"sortindex": sortindex}
        sortindex += 1

    # pydantic ``@computed_field`` properties — read-only bones computed at
    # dump time (see _computed_bone_structure); appended after the stored
    # fields, in declaration order.
    for name, computed_info in cls.model_computed_fields.items():
        structure[name] = _computed_bone_structure(name, computed_info) \
            | {"sortindex": sortindex}
        sortindex += 1
    return structure
