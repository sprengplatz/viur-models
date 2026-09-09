"""Field definitions → bone structure dicts (``BaseBone.structure()`` parity, viur-core 3.9)."""
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

#: Model class → serving module name (filled by ``SQLList``); relational bones emit it as ``module``.
MODULE_BY_MODEL: dict[type, str] = {}

# Defaults pinned against viur-core 3.9 bone implementations.
NUMERIC_MIN = -9223372036854775806
NUMERIC_MAX = 9223372036854775807
STRING_MAXLENGTH = 254
FLOAT_PRECISION = 8
CLONE_BEHAVIOR = {"strategy": "copy_value"}


def _viur_meta(field_info: t.Any) -> dict:
    """Bone metadata a Field left in ``json_schema_extra["viur"]``."""
    extra = field_info.json_schema_extra
    if isinstance(extra, dict):
        return dict(extra.get(VIUR_META_KEY) or {})
    return {}


def _tags(meta: dict) -> list:
    """``tags`` structure key (``BaseBone.tags``): a list, empty by default."""
    tags = meta.get("tags")
    return [tags] if isinstance(tags, str) else list(tags or ())


def _unwrap_annotation(
    annotation: t.Any,
) -> tuple[t.Any, BoneType | None, LanguageWrapper | None, tuple]:
    """Core type of ``T | None`` / ``Annotated[T, …]`` plus its ``BoneType``/``LanguageWrapper``
    markers (pydantic leaves ``Annotated`` metadata in place inside a union arm)."""
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
    """Validation constraints from ``FieldInfo.metadata`` and union-arm ``Annotated`` metadata."""
    out = {}
    for item in (*field_info.metadata, *annotated_metadata):
        for attr in ("max_length", "min_length", "ge", "le", "gt", "lt", "decimal_places"):
            if (value := getattr(item, attr, None)) is not None:
                out[attr] = value
    return out


def _numeric(cons: dict, precision: int, decimal_mode: bool) -> dict:
    # decimal is not precision > 0: NumericBone keeps decimal=False for floats
    minimum, maximum = cons.get("ge"), cons.get("le")
    if precision == 0:
        # exclusive bounds convert exactly for integers only
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
    """``name → SkeletonRefMarker`` of the cross-store fields."""
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
    """Association link: parent FK, the single to-one ``dest`` relationship, remaining scalars = using fields."""
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


def _using_structure(
    link_cls: type, using_fields: list, *, resolve_refs: bool = True,
) -> dict:
    """``using`` structure of a link model's payload fields."""
    return {
        name: _bone_structure(
            name, link_cls.model_fields[name], resolve_refs=resolve_refs,
        ) | {"sortindex": index}
        for index, name in enumerate(using_fields)
    }


def _crossstore_structure(
    marker: SkeletonRefMarker, meta: dict, *, resolve_refs: bool = True,
) -> dict:
    """``relational.<kind>`` entry for a datastore reference; ``resolve_refs=False`` leaves
    ``relskel`` empty (no skeleton registry outside a booted app)."""
    suffix = f"{marker.type_suffix}." if marker.type_suffix else ""
    entry = {
        "type": f"relational.{suffix}{marker.kind}",
        "emptyvalue": None,
        "multiple": marker.multiple,
        "module": marker.module,
        "format": meta.get("format") or marker.format or "$(dest.name)",
        "using": None,
        "relskel": _crossstore.resolve_relskel(marker) if resolve_refs else {},
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
    """``record`` bone entry (``RecordBone`` parity); the nested non-table SQLModel is the using-skel."""
    if getattr(target, "__table__", None) is not None:
        raise TypeError(
            f"record target {target.__name__!r} is a table model — table "
            "models are relations, use Relationship()"
        )
    ret = {
        "type": "record",
        "emptyvalue": None,
        "multiple": multiple,
        "indexed": False,
        "format": meta.get("format"),
        "using": structure_for_model(target),
    }
    if multiple:
        ret["defaultvalue"] = []
    return ret


def _bone_type_marker(
    core: t.Any, field_info: t.Any, annotated_marker: BoneType | None,
) -> BoneType | None:
    """``Annotated`` marker, else ``FieldInfo.metadata`` marker, else registry along the MRO."""
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
    """Bone ``type`` + type-specific keys for a Python type; a ``replace`` marker defines the bone alone."""
    if marker and marker.replace:
        return {"type": marker.name, "emptyvalue": marker.emptyvalue, **(marker.extras or {})}

    def _marker_only_or_raise() -> dict:
        # registered class without a dispatchable base
        if marker:
            return {"type": marker.name, "emptyvalue": marker.emptyvalue, **(marker.extras or {})}
        raise TypeError(
            f"type {core!r} has no bone mapping — register one with "
            "viur.models.register_bone_type or annotate it with BoneType"
        )

    # order matters: Enum before str/int, bool before int, datetime before date
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
    """System ``key`` bone (``KeyBone`` defaults)."""
    return {
        "descr": "Key",
        "tags": ["technical"],
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


def _bone_structure(
    name: str, field_info: t.Any, *, resolve_refs: bool = True,
) -> dict:
    meta = _viur_meta(field_info)
    core, annotated_marker, annotated_language, annotated_metadata = \
        _unwrap_annotation(field_info.annotation)

    readonly = bool(meta.get("readonly", False))
    required = meta["required"] if "required" in meta else field_info.is_required()

    bone = {
        "descr": meta.get("descr") or field_info.title or name.replace("_", " ").title(),
        "required": bool(required) and not readonly,
        "params": meta.get("params") or {},
        "visible": bool(meta.get("visible", True)),
        "readonly": readonly,
        "unique": False,
        "languages": None,
        "indexed": getattr(field_info, "index", None) is not False,
        "tags": _tags(meta),
        "clone_behavior": dict(CLONE_BEHAVIOR),
        "multiple": False,
    }

    cross = next(
        (item for item in (*annotated_metadata, *field_info.metadata)
         if isinstance(item, SkeletonRefMarker)),
        None,
    )
    if cross is not None:
        bone |= _crossstore_structure(cross, meta, resolve_refs=resolve_refs)
        return bone

    # Language[X]: bone shape from the inner type plus the languages list
    language = annotated_language or next(
        (item for item in field_info.metadata if isinstance(item, LanguageWrapper)), None,
    )
    if language is not None:
        langs = meta.get("languages") or _types.DEFAULT_LANGUAGES
        if not langs:
            raise TypeError(
                f"Language field {name!r} needs Field(languages=…) or "
                "viur.models.set_default_languages(…)"
            )
        inner_core, inner_marker, _, inner_metadata = _unwrap_annotation(language.inner)
        bone |= _bone_for_type(
            inner_core, _constraints(field_info, inner_metadata), meta,
            inner_marker or _bone_type_marker(inner_core, field_info, None),
        )
        bone["languages"] = list(langs)
        default = field_info.default
        bone["defaultvalue"] = (
            default if default is not PydanticUndefined and default is not None
            else {lang: None for lang in langs}
        )
        return bone
    if meta.get("languages"):
        raise TypeError(
            f"{name!r}: Field(languages=…) requires a Language[…] annotation"
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
    """Bone entry for a ``@computed_field``: shape from the return annotation, read-only,
    not indexed, ``compute.method = Always``; bone parameters in ``json_schema_extra["viur"]``."""
    field_info = FieldInfo.from_annotation(computed_info.return_type)
    extra = computed_info.json_schema_extra
    meta = dict(extra.get(VIUR_META_KEY) or {}) if isinstance(extra, dict) else {}
    meta["readonly"] = True
    meta.setdefault("compute", {"method": "Always"})
    field_info.title = computed_info.title
    field_info.json_schema_extra = {VIUR_META_KEY: meta}
    bone = _bone_structure(name, field_info)
    bone["indexed"] = False
    return bone


def relations_for_model(cls: type) -> dict:
    """Mapped relationships → ``{"fk", "target", "required", "multiple", …}``: to-one via FK
    column, many-to-many via link table, association links, ``SkeletonLink`` tables.
    Inverse sides are skipped. Table models only."""
    rel_infos = getattr(cls, "__sqlmodel_relationships__", None)
    if not rel_infos:
        return {}
    mapper = getattr(cls, "__mapper__", None)
    if mapper is None:
        raise NotImplementedError(
            "relationships need a table model (table=True) — the FK column "
            "defines the mapping"
        )
    relations = {}
    for rel_name in rel_infos:
        prop = mapper.relationships[rel_name]
        if prop.uselist and prop.secondary is None \
                and issubclass(prop.mapper.class_, RelationLink):
            # association object: multiple with payload
            relations[rel_name] = _analyze_relation_link(
                mapper.local_table, prop.mapper.class_,
            )
            continue
        if prop.uselist and prop.secondary is None \
                and issubclass(prop.mapper.class_, SkeletonLink):
            # link-table-backed cross-store reference
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
    """RefSkel ``shortkey`` bone — emitted in ``relskel``, not computed in dumps."""
    return {
        "descr": "Shortkey",
        "tags": ["technical"],
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
    *, resolve_refs: bool = True,
) -> dict:
    """``relational.<kind>`` bone (``RelationalBone`` parity). Bone parameters: the FK field's
    metadata, overridden by ``viur_relation_meta`` (the only source for many-to-many)."""
    meta = _viur_meta(fk_field_info) if fk_field_info is not None else {}
    meta |= getattr(owner_cls, "viur_relation_meta", {}).get(rel_name, {})
    target = info["target"]
    kind = target._viur_kind()
    # module: explicit meta > SQLList registry > kind
    module = meta.get("module") or MODULE_BY_MODEL.get(target) or kind
    readonly = bool(meta.get("readonly", False))
    required = meta["required"] if "required" in meta else info["required"]
    ref_keys = ("key", *(
        k for k in target.viur_ref_keys
        if k in target.model_fields or k in target.model_computed_fields
    ))
    relskel = {
        name: bone
        for name, bone in structure_for_model(
            target, include_relations=False, resolve_refs=resolve_refs,
        ).items()
        if name in ref_keys
    }
    relskel["shortkey"] = _shortkey_bone()
    fk_title = fk_field_info.title if fk_field_info is not None else None
    bone = {
        "descr": meta.get("descr") or fk_title or rel_name.replace("_", " ").title(),
        "tags": _tags(meta),
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
        "using": (
            _using_structure(
                info["link"], info["using_fields"], resolve_refs=resolve_refs,
            )
            if info.get("using_fields") else None
        ),
        "relskel": relskel,
    }
    if info["multiple"]:
        bone["defaultvalue"] = []
        # MultipleConstraints from viur_relation_meta["multiple"]
        if isinstance(constraints := meta.get("multiple"), dict):
            bone["multiple"] = {
                "duplicates": bool(constraints.get("duplicates", False)),
                "max": int(constraints.get("max", 0)),
                "min": int(constraints.get("min", 0)),
            }
    return bone


def write_only_fields(cls: type) -> frozenset:
    """Field names with a ``write_only`` marker."""
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


def structure_for_model(
    cls: type, *, include_relations: bool = True, resolve_refs: bool = True,
) -> dict:
    """Skeleton-compatible structure dict. Primary key → ``key`` bone; an FK consumed by a
    to-one relation → one ``relational.<kind>`` bone under the relation's name.
    ``include_relations=False``: scalar-only (``relskel``); ``resolve_refs=False``: no
    datastore lookup for cross-store ``relskel``."""
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
                _relational_structure(
                    rel_name, field_info, relations[rel_name], cls,
                    resolve_refs=resolve_refs,
                )
                | {"sortindex": sortindex}
            )
        else:
            structure[name] = _bone_structure(
                name, field_info, resolve_refs=resolve_refs,
            ) | {"sortindex": sortindex}

    # relations without an FK field: appended in declaration order
    sortindex = len(cls.model_fields)
    for rel_name, info in relations.items():
        if info["fk"] is not None:
            continue
        meta = getattr(cls, "viur_relation_meta", {}).get(rel_name, {})
        if marker := info.get("crossstore"):
            entry = _crossstore_structure(marker, meta, resolve_refs=resolve_refs)
            entry["descr"] = meta.get("descr") or rel_name.replace("_", " ").title()
            if info.get("using_fields"):
                entry["using"] = _using_structure(
                    info["target"], info["using_fields"], resolve_refs=resolve_refs,
                )
            entry = {
                "params": meta.get("params") or {},
                "tags": _tags(meta),
                "required": False,
                "visible": bool(meta.get("visible", True)),
                "readonly": bool(meta.get("readonly", False)),
                "unique": False,
                "languages": None,
                "indexed": True,
                "clone_behavior": dict(CLONE_BEHAVIOR),
            } | entry
        else:
            entry = _relational_structure(
                rel_name, None, info, cls, resolve_refs=resolve_refs,
            )
        structure[rel_name] = entry | {"sortindex": sortindex}
        sortindex += 1

    # computed fields: appended last
    for name, computed_info in cls.model_computed_fields.items():
        structure[name] = _computed_bone_structure(name, computed_info) \
            | {"sortindex": sortindex}
        sortindex += 1
    return structure
