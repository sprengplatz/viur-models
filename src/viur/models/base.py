"""``Model``/``Record`` base classes: system fields, structure cache, dumps, client input, key encoding."""
import base64
import copy
import datetime as dt
import decimal
import enum
import typing as t
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlmodel import Field as SQLModelField, SQLModel

from .client import map_validation_error, relation_error
from .fields import Field
from .structure import (
    crossstore_fields,
    relations_for_model,
    structure_for_model,
    write_only_fields,
)
from . import crossstore as _crossstore

if t.TYPE_CHECKING:  # pragma: no cover
    from viur.core.bones.base import ReadFromClientError

KEY_SEPARATOR = "\x1f"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _split_dest_input(item: t.Any) -> tuple[t.Any, dict | None]:
    """One cross-store input item → ``(key, stale_snapshot)``: a full ``dest`` dict yields the
    snapshot as fallback for a vanished target, a bare key none."""
    if not isinstance(item, dict):
        return item, None
    dest = item.get("dest") or item
    if not isinstance(dest, dict):
        return None, None
    snapshot = dict(dest) if set(dest) - {"key"} else None
    return dest.get("key"), snapshot


def _merge_sub_keys(stored: t.Any, subs: dict) -> dict:
    """Dotted sub-keys merged over the value already in ``cleaned`` (edit seeds the stored dump)."""
    return {**stored, **subs} if isinstance(stored, dict) else subs


def _dump_value(value: t.Any) -> t.Any:
    """One value in the bones' JSON dump shape."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dt.datetime):
        # written as UTC; SQLite returns naive datetimes
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, SQLModel):  # nested record
        cls = type(value)
        return {
            name: _dump_value(getattr(value, name))
            for name in (*cls.model_fields, *cls.model_computed_fields)
        }
    if isinstance(value, (list, tuple)):
        return [_dump_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool, dict)):
        return value
    return str(value)


#: What clients post for an untouched numeric field (vi-vue-utils: ``"" + value``).
_NULL_TOKENS = frozenset({"", "none", "null", "undefined"})


def _numeric_text(value: str) -> str | None:
    """Numeric client text: whitespace stripped, ``,`` as decimal separator. ``None`` for the
    null tokens, which count as empty like in core's ``NumericBone.isEmpty``. Unlike core,
    other non-numeric text is NOT swallowed as empty — pydantic rejects it."""
    text = value.strip()
    if text.lower() in _NULL_TOKENS:
        return None
    return text.replace(",", ".", 1)


def _normalize_empty_values(structure: dict, values: dict) -> None:
    """In place, like the core bones: an empty submission on a bone whose emptyvalue is not
    ``""`` is dropped (→ the field's default: ``None`` for Optional, ``missing`` for required)
    or, for multiples, set to ``[]``; numeric text that is no number counts as empty;
    spatial dicts → ``[lat, lng]``; records and ``using`` payloads recurse."""
    for name, value in list(values.items()):
        bone = structure.get(name)
        if bone is None:
            continue  # stray sub-key
        if bone["type"] == "spatial" and isinstance(value, dict):
            value = values[name] = [value.get("lat"), value.get("lng")]
        if bone["type"] == "record" and (using := bone.get("using")):
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict):
                    _normalize_empty_values(using, item)
        if bone["type"].startswith("relational") and (using := bone.get("using")):
            # using payload: empty fields fall back to the link model's defaults
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict) and isinstance(item.get("rel"), dict):
                    _normalize_empty_values(using, item["rel"])
                    item["rel"] = {
                        key: val for key, val in item["rel"].items()
                        if val is not None
                    }
        if bone["emptyvalue"] == "":
            continue
        empty = value == "" or (
            bone["type"] == "spatial"
            and isinstance(value, (list, tuple))
            and all(item in ("", None) for item in value)
        )
        if not empty and bone["type"].startswith("numeric") and isinstance(value, str):
            if (text := _numeric_text(value)) is None:
                empty = True
            else:
                values[name] = text
        if empty:
            if bone.get("multiple"):
                values[name] = []
            else:
                del values[name]


class Record(SQLModel):
    """Base for nested record values (``RelSkel`` analogue): no table, no key, no system fields."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        cls._viur_structure_shared()  # fail fast on unmappable fields

    @classmethod
    def _viur_structure_shared(cls) -> dict:
        """Cached structure, shared — read-only."""
        if "_viur_structure" not in cls.__dict__:
            cls._viur_structure = structure_for_model(cls)
        return cls._viur_structure

    @classmethod
    def viur_structure(cls) -> dict:
        """``using`` structure, fresh copy."""
        return copy.deepcopy(cls._viur_structure_shared())


def _check_relation_databases(cls: type, relations: dict) -> None:
    """Relation targets and link tables must live in ``cls.viur_database`` (no cross-database FK)."""
    for name, info in relations.items():
        for other in (info["target"], info.get("link")):
            if other is not None and getattr(other, "viur_database", "default") != cls.viur_database:
                raise TypeError(
                    f"{cls.__name__}.{name}: {other.__name__} lives in database "
                    f"{other.viur_database!r}, {cls.__name__} in {cls.viur_database!r}"
                )


class Model(SQLModel):
    """Base for SQL-backed models. The structure is built at class definition (unmappable types
    fail fast) and cached per class."""

    #: Fields carried in a referencing model's ``relskel``/``dest`` (``refKeys``); ``key`` always included.
    viur_ref_keys: t.ClassVar[tuple[str, ...]] = ("name",)

    #: Bone-parameter overrides per relation name; the only source for many-to-many relations.
    viur_relation_meta: t.ClassVar[dict[str, dict]] = {}

    #: Bones always included when a client bonelist restricts a response (``"*"``-subskel
    #: analogue); ``key`` always is.
    viur_bones_always: t.ClassVar[tuple[str, ...]] = ()

    #: Engine name (``viur.models.db.configure(..., name=)``); relation targets and link
    #: tables must share it.
    viur_database: t.ClassVar[str] = "default"

    id: int | None = SQLModelField(default=None, primary_key=True)
    creationdate: datetime | None = Field(
        default_factory=_utcnow, readonly=True, visible=False, tags=("technical",),
        descr="created at", compute={"method": "Once"},
    )
    changedate: datetime | None = Field(
        default_factory=_utcnow, readonly=True, visible=False, tags=("technical",),
        descr="updated at", compute={"method": "OnWrite"},
    )

    @property
    def errors(self) -> list:
        """Client-input errors (``SkeletonInstance.errors`` counterpart). Kept in ``__dict__``:
        loaded rows skip ``__init__``, so pydantic private attrs may not exist."""
        return self.__dict__.get("_viur_errors", [])

    @errors.setter
    def errors(self, value: t.Iterable) -> None:
        self.__dict__["_viur_errors"] = list(value)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # fail fast; deferred for relationships (mapper not configured yet) and
        # cross-store references (skeleton registry fills at boot)
        if crossstore := crossstore_fields(cls):
            _crossstore._register_model(cls)
        if not getattr(cls, "__sqlmodel_relationships__", None) and not crossstore:
            cls._viur_structure_shared()

    @classmethod
    def _viur_structure_shared(cls) -> dict:
        """Cached structure, shared by every request — never mutate; for read-only hot paths."""
        if "_viur_structure" not in cls.__dict__:
            cls._viur_structure = structure_for_model(cls)
        return cls._viur_structure

    @classmethod
    def viur_structure(cls) -> dict:
        """Skeleton-compatible structure dict, fresh deep copy (the caller owns it)."""
        return copy.deepcopy(cls._viur_structure_shared())

    @classmethod
    def viur_crossstore(cls) -> dict:
        """``name → SkeletonRefMarker``, cached."""
        if "_viur_crossstore" not in cls.__dict__:
            cls._viur_crossstore = crossstore_fields(cls)
        return cls._viur_crossstore

    @classmethod
    def viur_relations(cls) -> dict:
        """Relations per ``relations_for_model``, cached."""
        if "_viur_relations" not in cls.__dict__:
            relations = relations_for_model(cls)
            _check_relation_databases(cls, relations)
            cls._viur_relations = relations
        return cls._viur_relations

    @classmethod
    def viur_write_only(cls) -> frozenset:
        """Write-only field names, cached."""
        if "_viur_write_only" not in cls.__dict__:
            cls._viur_write_only = write_only_fields(cls)
        return cls._viur_write_only

    def viur_dump(self, *, bones: t.Iterable[str] = ()) -> dict:
        """``SkeletonInstance.dump()``-shaped values: opaque ``key``, ISO datetimes, enum values,
        relations in ``RelationalBone`` shape. ``bones`` restricts the output; without it, a
        client bonelist attached by ``SQLList`` (``_viur_bones``) does."""
        bones = set(bones) if bones else self.__dict__.get("_viur_bones", ())
        structure = self._viur_structure_shared()
        relations = self.viur_relations()
        crossstore = self.viur_crossstore()
        write_only = self.viur_write_only()
        out = {}
        for name in structure:
            if bones and name not in bones:
                continue
            if name == "key":
                out[name] = self.viur_key
            elif name in crossstore:
                out[name] = self._viur_dump_crossstore(name, crossstore[name])
            elif name in relations:
                out[name] = self._viur_dump_relation(name, relations[name])
            elif name in write_only:
                out[name] = structure[name]["emptyvalue"]
            elif languages := structure[name]["languages"]:
                value = getattr(self, name)
                if not isinstance(value, dict):  # unvalidated form
                    value = {}
                out[name] = {lang: value.get(lang) for lang in languages}
            elif self.__dict__.get("_viur_unvalidated") \
                    and name in type(self).model_computed_fields:
                # computed fields over raw values may raise on a rejected form
                try:
                    out[name] = _dump_value(getattr(self, name))
                except Exception:
                    out[name] = structure[name]["emptyvalue"]
            else:
                out[name] = _dump_value(getattr(self, name))
        return out

    @staticmethod
    def _viur_dest(related: "Model") -> dict:
        dest = {"key": related.viur_key}
        for ref in type(related).viur_ref_keys:
            if ref in type(related).model_fields \
                    or ref in type(related).model_computed_fields:
                dest[ref] = _dump_value(getattr(related, ref))
        return dest

    def _viur_dump_crossstore(self, name: str, marker: t.Any) -> dict | list | None:
        """RelationalBone value shape from the stored ``dest`` snapshot."""
        value = getattr(self, name)
        if marker.multiple:
            return [{"dest": dest, "rel": None} for dest in (value or [])]
        return {"dest": value, "rel": None} if value else None

    def _viur_dump_relation(self, rel_name: str, info: dict) -> dict | list | None:
        """``RelationalBone`` value shape. Related objects are read from ``__dict__`` only (no lazy
        IO on detached instances); unloaded to-one → key-only ``dest`` from the FK, unloaded
        many-to-many → parked client input."""
        using_fields = info.get("using_fields")
        pending = self.__dict__.get("_viur_pending_relations", {}).get(rel_name)

        def _rel_payload(row: t.Any) -> dict | None:
            if not using_fields:
                return None
            return {name: _dump_value(getattr(row, name)) for name in using_fields}

        if info.get("crossstore"):
            rows = self.__dict__.get(rel_name) or pending or []
            return [{"dest": row.dest, "rel": _rel_payload(row)} for row in rows]
        if info.get("link"):
            out = []
            for row in self.__dict__.get(rel_name) or pending or []:
                related = row.__dict__.get(info["dest_rel"])
                if related is not None:
                    dest = self._viur_dest(related)
                elif (foreign_key := getattr(row, info["dest_fk"])) is not None:
                    dest = {"key": info["target"].viur_encode_key(foreign_key)}
                else:
                    dest = None
                out.append({"dest": dest, "rel": _rel_payload(row)})
            return out
        if info["multiple"]:
            related_list = self.__dict__.get(rel_name)
            if related_list is None and pending is not None:
                return [  # parked client input (rejected re-render)
                    {"dest": {"key": info["target"].viur_encode_key(pk)}, "rel": None}
                    for pk in pending
                ]
            return [
                {"dest": self._viur_dest(related), "rel": None}
                for related in (related_list or [])
            ]
        related = self.__dict__.get(rel_name)
        if related is not None:
            return {"dest": self._viur_dest(related), "rel": None}
        foreign_key = getattr(self, info["fk"])
        if foreign_key is None:
            return None
        return {"dest": {"key": info["target"].viur_encode_key(foreign_key)}, "rel": None}

    @classmethod
    def viur_from_client(
        cls, data: dict,
    ) -> tuple[t.Self | None, list["ReadFromClientError"]]:
        """``skel.fromClient()`` counterpart. Unknown and read-only fields are dropped. Returns
        ``(instance, [])`` or ``(form, errors)`` — ``form`` is unvalidated (``_viur_form``),
        never persist it."""
        structure = cls._viur_structure_shared()
        cleaned = {
            name: value
            for name, value in data.items()
            if name in structure and not structure[name]["readonly"]
        }

        # dotted sub-keys: title.de=…, pos.lat=…, address.street=…, name.<idx>.<field>=…
        dotted: dict[str, dict] = {}
        for key, value in data.items():
            base_name, separator, suffix = key.partition(".")
            if separator and base_name in structure and not structure[base_name]["readonly"]:
                dotted.setdefault(base_name, {})[suffix] = value
        for base_name, subs in dotted.items():
            if structure[base_name]["languages"]:
                cleaned[base_name] = _merge_sub_keys(cleaned.get(base_name), subs)
            elif structure[base_name]["type"] == "spatial":
                cleaned[base_name] = [subs.get("lat"), subs.get("lng")]
            elif structure[base_name]["type"] == "record" \
                    and not structure[base_name]["multiple"]:
                cleaned[base_name] = _merge_sub_keys(cleaned.get(base_name), subs)
            elif structure[base_name].get("multiple") \
                    and subs and all(key.partition(".")[0].isdigit() for key in subs):
                items: dict[int, dict] = {}
                for key, value in subs.items():
                    index, _, field = key.partition(".")
                    items.setdefault(int(index), {})[field] = value
                ordered = [items[index] for index in sorted(items)]
                if structure[base_name]["type"] == "record":
                    cleaned[base_name] = ordered
                else:
                    cleaned[base_name] = [
                        {"dest": {"key": item.pop("key", None)}, "rel": item}
                        for item in ordered
                    ]

        # Language dicts keep only declared languages.
        for name, bone in structure.items():
            if (languages := bone["languages"]) and isinstance(cleaned.get(name), dict):
                cleaned[name] = {
                    lang: value
                    for lang, value in cleaned[name].items()
                    if lang in languages and value is not None
                }

        _normalize_empty_values(structure, cleaned)

        # empty write-only input keeps the stored value
        for name in cls.viur_write_only():
            if name in cleaned and cleaned[name] in ("", None):
                del cleaned[name]

        relation_errors = []

        # cross-store: read_dest is the existence check; a full stale snapshot survives a
        # vanished target, a bare unknown key rejects
        for name, marker in cls.viur_crossstore().items():
            if name not in cleaned:
                continue
            raw = cleaned.pop(name)
            values = raw if isinstance(raw, (list, tuple)) else [raw]
            dests = []
            for item in values:
                item, stale_snapshot = _split_dest_input(item)
                if item in (None, "", "None"):
                    continue
                dest = _crossstore.read_dest(marker, str(item))
                if dest is not None:
                    dests.append(dest)
                elif stale_snapshot is not None:
                    dests.append(stale_snapshot)
                else:
                    relation_errors.append(relation_error(name, "Unknown key"))
            if marker.multiple:
                cleaned[name] = dests
            else:
                cleaned[name] = dests[0] if dests else None

        # to-one → FK column; many-to-many parked in _viur_pending_relations for SQLList
        # (existence is checked there, only the key format here)
        pending_relations = {}
        for rel_name, info in cls.viur_relations().items():
            if rel_name not in cleaned:
                continue
            raw = cleaned.pop(rel_name)
            if info["multiple"]:
                values = raw if isinstance(raw, (list, tuple)) else [raw]
                using_fields = info.get("using_fields") or ()
                primary_keys = []

                def _validated_link(link_cls: type, data: dict) -> t.Any:
                    """Validate one link row; errors get the ``[name, "rel", field]`` path. Built from
                    the submitted keys only — ``model_validate`` would pin the parent FK to ``None``."""
                    try:
                        validated = link_cls.model_validate(data)
                    except ValidationError as exc:
                        for error in map_validation_error(exc):
                            error.fieldPath = [rel_name, "rel", *error.fieldPath]
                            relation_errors.append(error)
                        return None
                    return link_cls(**{name: getattr(validated, name) for name in data})

                for item in values:
                    payload = {}
                    stale_snapshot = None
                    if isinstance(item, dict):
                        payload = {
                            key: value
                            for key, value in (item.get("rel") or {}).items()
                            if key in using_fields
                        }
                        item, stale_snapshot = _split_dest_input(item)
                    if item in (None, "", "None"):
                        continue
                    if marker := info.get("crossstore"):
                        dest = _crossstore.read_dest(marker, str(item))
                        if dest is None:
                            dest = stale_snapshot
                        if dest is None:
                            relation_errors.append(relation_error(rel_name, "Unknown key"))
                        elif (link := _validated_link(
                            info["target"], {"key": dest["key"], "dest": dest} | payload,
                        )) is not None:
                            primary_keys.append(link)
                        continue
                    primary_key = info["target"].viur_parse_key(str(item))
                    if primary_key is None:
                        relation_errors.append(relation_error(rel_name))
                    elif link_cls := info.get("link"):
                        if (link := _validated_link(
                            link_cls, {info["dest_fk"]: primary_key} | payload,
                        )) is not None:
                            primary_keys.append(link)
                    else:
                        primary_keys.append(primary_key)
                # MultipleConstraints; duplicates forbidden by default (the composite PK
                # would collapse them silently)
                constraints = getattr(cls, "viur_relation_meta", {}) \
                    .get(rel_name, {}).get("multiple")
                if not isinstance(constraints, dict):
                    constraints = {}

                def _identity(item: t.Any) -> t.Any:
                    if isinstance(item, SQLModel):
                        return getattr(item, info.get("dest_fk") or "key")
                    return item

                identity = [_identity(item) for item in primary_keys]
                if not constraints.get("duplicates", False) \
                        and len(set(identity)) != len(identity):
                    relation_errors.append(
                        relation_error(rel_name, "Duplicate entries are not allowed"))
                if (minimum := int(constraints.get("min", 0))) \
                        and len(primary_keys) < minimum:
                    relation_errors.append(
                        relation_error(rel_name, f"Too few entries (min {minimum})"))
                if (maximum := int(constraints.get("max", 0))) \
                        and len(primary_keys) > maximum:
                    relation_errors.append(
                        relation_error(rel_name, f"Too many entries (max {maximum})"))
                pending_relations[rel_name] = primary_keys
                continue
            if isinstance(raw, dict):
                raw = (raw.get("dest") or raw).get("key")
            if raw in (None, "", "None"):
                cleaned[info["fk"]] = None
                continue
            primary_key = info["target"].viur_parse_key(str(raw))
            if primary_key is None:
                relation_errors.append(relation_error(rel_name))
            else:
                cleaned[info["fk"]] = primary_key
        if relation_errors:
            return cls._viur_form(cleaned, pending_relations), relation_errors

        try:
            instance = cls.model_validate(cleaned)
        except ValidationError as exc:
            return cls._viur_form(cleaned, pending_relations), map_validation_error(exc)
        if pending_relations:
            instance.__dict__["_viur_pending_relations"] = pending_relations
        return instance, []

    @classmethod
    def _viur_form(cls, cleaned: dict, pending_relations: dict) -> t.Self:
        """Unvalidated instance carrying the submitted values for a rejected re-render. Never persist."""
        if getattr(cls, "__table__", None) is not None:
            form = cls(**cleaned)
        else:
            form = cls.model_construct(**cleaned)
        form.__dict__["_viur_unvalidated"] = True
        if pending_relations:
            form.__dict__["_viur_pending_relations"] = pending_relations
        return form

    # --- Renderable protocol (SkeletonInstance aliases) ---------------------

    def dump(self, *, bones: t.Iterable[str] = ()) -> dict:
        return self.viur_dump(bones=bones)

    def structure(self) -> dict:
        full = self.viur_structure()
        if bones := self.__dict__.get("_viur_bones"):  # client bonelist → subskel-shaped
            return {name: bone for name, bone in full.items() if name in bones}
        return full

    @classmethod
    def _viur_kind(cls) -> str:
        table_name = getattr(cls, "__tablename__", None)
        # non-table models: __tablename__ is a declared_attr descriptor
        return table_name if isinstance(table_name, str) else cls.__name__.lower()

    @classmethod
    def viur_encode_key(cls, primary_key: int | str) -> str:
        """Primary key → opaque key string."""
        raw = f"{cls._viur_kind()}{KEY_SEPARATOR}{primary_key}".encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @property
    def viur_key(self) -> str | None:
        """Opaque urlsafe key; ``None`` before insert."""
        if self.id is None:
            return None
        return type(self).viur_encode_key(self.id)

    @classmethod
    def viur_parse_key(cls, key: str) -> int | str | None:
        """Primary key from a ``viur_key``; ``None`` for malformed or foreign keys."""
        try:
            raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            return None
        table_name, separator, primary_key = raw.partition(KEY_SEPARATOR)
        if not separator or table_name != cls._viur_kind() or not primary_key:
            return None
        return int(primary_key) if primary_key.isdigit() else primary_key
