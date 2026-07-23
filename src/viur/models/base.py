"""``ViURModel`` — the common base class of all viur-models.

Provides the system fields (``id`` → ``key``, ``creationdate``,
``changedate``), the cached skeleton-compatible ``viur_structure()`` and the
opaque key encoding. Concrete models live in the project's ``models/``
folder and add ``table=True``.
"""
import base64
import datetime as dt
import decimal
import enum
import typing as t
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlmodel import Field, SQLModel

from .client import map_validation_error, relation_error
from .fields import ViURField
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
    """Normalize one cross-store input item to ``(key, stale_snapshot)``.

    A dict carrying more than just the key is a full ``dest`` snapshot —
    the shape edit-merges roundtrip. It is returned as fallback so a
    vanished target keeps its stored snapshot instead of rejecting the
    whole edit. Bare keys (a client actively setting the reference) get
    no fallback and stay strictly validated.
    """
    if not isinstance(item, dict):
        return item, None
    dest = item.get("dest") or item
    if not isinstance(dest, dict):
        return None, None
    snapshot = dict(dest) if set(dest) - {"key"} else None
    return dest.get("key"), snapshot


def _dump_value(value: t.Any) -> t.Any:
    """One value in the JSON-serializable dump shape of the real bones."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dt.datetime):
        # viur-models always writes UTC; backends without tz support
        # (SQLite) return naive datetimes — re-attach UTC so dumps are
        # identical before and after a database roundtrip.
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, SQLModel):
        # Nested record — plain pydantic nesting; dumps as its values dict
        # (the RecordBone dump shape), computed fields included.
        cls = type(value)
        return {
            name: _dump_value(getattr(value, name))
            for name in (*cls.model_fields, *cls.model_computed_fields)
        }
    if isinstance(value, (list, tuple)):
        return [_dump_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool, dict)):
        return value
    # Non-str semantic objects (pydantic AnyUrl, Color, …) stringify.
    return str(value)


def _normalize_empty_values(structure: dict, values: dict) -> None:
    """In-place client-input normalization (core-bone parity).

    HTML forms send cleared inputs as ``""`` — for bones whose emptyvalue
    is not ``""`` (date/time/numeric/select/spatial/relational/record/…)
    that is an *empty submission* and clears the value: multiple bones to
    ``[]`` (an empty multi-selection), everything else to ``None``.
    String-family bones keep ``""`` — it IS their emptyvalue.

    Spatial input may arrive as a ``{"lat": …, "lng": …}`` dict (JSON
    clients) and counts as empty when both coordinates are empty; record
    values recurse into their ``using`` structure (``zip_code=""`` inside
    an address must clear like a top-level bone).
    """
    for name, value in values.items():
        bone = structure.get(name)
        if bone is None:
            continue  # stray sub-key inside a record — pydantic ignores it
        if bone["type"] == "spatial" and isinstance(value, dict):
            value = values[name] = [value.get("lat"), value.get("lng")]
        if bone["type"] == "record" and (using := bone.get("using")):
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict):
                    _normalize_empty_values(using, item)
        if bone["type"].startswith("relational") and (using := bone.get("using")):
            # using payloads: empty fields fall back to their defaults —
            # the link row is validated as a whole, None would reject there
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict) and isinstance(item.get("rel"), dict):
                    _normalize_empty_values(using, item["rel"])
                    item["rel"] = {
                        key: val for key, val in item["rel"].items()
                        if val is not None
                    }
        if bone["emptyvalue"] == "":
            continue
        if value == "" or (
            bone["type"] == "spatial"
            and isinstance(value, (list, tuple))
            and all(item in ("", None) for item in value)
        ):
            values[name] = [] if bone.get("multiple") else None


class ViURRecord(SQLModel):
    """Base class for nested record values — the ``RelSkel`` analogue.

    A record has no identity: no table, no key, no system fields — it
    lives as JSON inside a parent field (``RecordBone`` mapping). This is
    :class:`ViURModel` **without** ``table=True`` and without the system
    fields; deriving from it makes the role explicit and adds the same
    fail-fast structure validation. Any plain non-table ``SQLModel``
    keeps working as a record target too.

        class Address(ViURRecord):
            street: str = ViURField(descr="Straße", max_length=100)
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        cls.viur_structure()  # fail fast on unmappable fields

    @classmethod
    def viur_structure(cls) -> dict:
        """The record's ``using``-structure (see analysis/01 §6), cached per class."""
        if "_viur_structure" not in cls.__dict__:
            cls._viur_structure = structure_for_model(cls)
        return cls._viur_structure


class ViURModel(SQLModel):
    """Base class for SQL-backed ViUR models.

    Importing or subclassing has no side effects beyond validation: the
    structure mapping runs once at class-definition time, so unmappable
    field types fail fast (same philosophy as the hook wiring in
    viur-actions), and is cached per class afterwards.
    """

    #: Fields of THIS model that appear in a referencing model's ``relskel`` /
    #: ``dest`` payload (``key`` is always included) — the ``refKeys`` analogue.
    viur_ref_keys: t.ClassVar[tuple[str, ...]] = ("name",)

    #: Bone-parameter overrides per relation name (``descr``, ``visible``,
    #: ``params``, …) — the only metadata source for many-to-many relations
    #: (they have no FK field to carry a ViURField).
    viur_relation_meta: t.ClassVar[dict[str, dict]] = {}

    id: int | None = Field(default=None, primary_key=True)
    creationdate: datetime | None = ViURField(
        default_factory=_utcnow, readonly=True, visible=False,
        descr="created at", compute={"method": "Once"},
    )
    changedate: datetime | None = ViURField(
        default_factory=_utcnow, readonly=True, visible=False,
        descr="updated at", compute={"method": "OnWrite"},
    )

    @property
    def errors(self) -> list:
        """Client-input errors attached to this instance.

        The render protocol's counterpart of ``SkeletonInstance.errors`` —
        the envelope render reads it to serialize errors and derive the
        ``rejected`` status on form re-renders.

        Stored directly in ``__dict__`` (not as a pydantic private attr):
        SQLAlchemy materializes loaded rows without calling ``__init__``,
        so pydantic's private-attribute storage may not exist on them.
        """
        return self.__dict__.get("_viur_errors", [])

    @errors.setter
    def errors(self, value: t.Iterable) -> None:
        self.__dict__["_viur_errors"] = list(value)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # Fail fast on unmappable fields. Deferred to first use for models
        # with relationships (their SQLAlchemy mapper is not configured yet
        # at this point) and with cross-store references (their relskel
        # needs the skeleton registry, which fills at app boot).
        if crossstore := crossstore_fields(cls):
            _crossstore._register_model(cls)  # for refresh_for_target
        if not getattr(cls, "__sqlmodel_relationships__", None) and not crossstore:
            cls.viur_structure()

    @classmethod
    def viur_structure(cls) -> dict:
        """Skeleton-compatible structure dict (see analysis/01 §6), cached per class."""
        if "_viur_structure" not in cls.__dict__:
            cls._viur_structure = structure_for_model(cls)
        return cls._viur_structure

    @classmethod
    def viur_crossstore(cls) -> dict:
        """Cross-store reference fields (``name → SkeletonRefMarker``),
        cached per class."""
        if "_viur_crossstore" not in cls.__dict__:
            cls._viur_crossstore = crossstore_fields(cls)
        return cls._viur_crossstore

    @classmethod
    def viur_relations(cls) -> dict:
        """To-one relationships (``rel_name → {"fk", "target", "required"}``),
        cached per class — see :func:`viur.models.structure.relations_for_model`."""
        if "_viur_relations" not in cls.__dict__:
            cls._viur_relations = relations_for_model(cls)
        return cls._viur_relations

    @classmethod
    def viur_write_only(cls) -> frozenset:
        """Field names that never appear in dumps (Password/Credential),
        cached per class."""
        if "_viur_write_only" not in cls.__dict__:
            cls._viur_write_only = write_only_fields(cls)
        return cls._viur_write_only

    def viur_dump(self, *, bones: t.Iterable[str] = ()) -> dict:
        """JSON-serializable bone values, shaped like ``SkeletonInstance.dump()``.

        ``key`` is the opaque encoded string (never the raw primary key);
        datetimes dump as ISO strings, enums as their values; to-one
        relations dump in the ``RelationalBone`` shape
        (``{"dest": {...ref keys...}, "rel": None}``).

        :param bones: Iterable of bone names to include. Empty = all bones.
        """
        structure = self.viur_structure()
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
                # Password/Credential semantics: the value never leaves
                # the model — reads see the empty value, like viur-core.
                out[name] = structure[name]["emptyvalue"]
            elif languages := structure[name]["languages"]:
                value = getattr(self, name)
                if not isinstance(value, dict):  # raw invalid input on a
                    value = {}                   # best-effort form
                out[name] = {lang: value.get(lang) for lang in languages}
            elif self.__dict__.get("_viur_unvalidated") \
                    and name in type(self).model_computed_fields:
                # computed fields run user code over RAW, unvalidated values
                # on a rejected re-render — degrade to the emptyvalue instead
                # of failing the whole form (valid instances still raise).
                try:
                    out[name] = _dump_value(getattr(self, name))
                except Exception:
                    out[name] = structure[name]["emptyvalue"]
            else:
                out[name] = _dump_value(getattr(self, name))
        return out

    @staticmethod
    def _viur_dest(related: "ViURModel") -> dict:
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
        """RelationalBone value shape for one relation.

        Reads related objects from ``__dict__`` only — a loaded relationship
        lives there; touching the attribute itself would trigger lazy IO,
        which fails on detached instances (dumps run after the session
        closed). Unloaded to-one relations fall back to a ``dest`` carrying
        just the encoded key from the FK column; unloaded many-to-many
        relations fall back to the parked client input
        (``_viur_pending_relations`` — so rejected re-renders roundtrip the
        selection) and dump empty otherwise (SQLList always eager-loads).
        """
        using_fields = info.get("using_fields")
        pending = self.__dict__.get("_viur_pending_relations", {}).get(rel_name)

        def _rel_payload(row: t.Any) -> dict | None:
            if not using_fields:
                return None
            return {name: _dump_value(getattr(row, name)) for name in using_fields}

        if info.get("crossstore"):
            # link-table-backed cross-store reference: one row per target,
            # each carrying its dest snapshot (+ optional edge payload).
            rows = self.__dict__.get(rel_name) or pending or []
            return [{"dest": row.dest, "rel": _rel_payload(row)} for row in rows]
        if info.get("link"):
            # association object: dest via the link's to-one relation, the
            # payload fields are the using values.
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
                # unassigned client input (rejected re-render): the parked
                # primary keys become key-only dests
                return [
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
        """Validate client data — the counterpart of ``skel.fromClient()``.

        Unknown fields and read-only bones (``key``, ``creationdate``, …)
        are dropped before validation, mirroring skeleton behavior. Returns
        ``(instance, [])`` on success, or ``(form, errors)`` with the errors
        in viur-core's own ``ReadFromClientError`` shape (analysis/01 §7) —
        *form* is a best-effort **unvalidated** instance carrying the
        submitted values (see :meth:`_viur_form`), so rejected re-renders
        roundtrip the client's input. Never persist it.
        """
        structure = cls.viur_structure()
        cleaned = {
            name: value
            for name, value in data.items()
            if name in structure and not structure[name]["readonly"]
        }

        # Dotted sub-keys, like core's fromClient: ``title.de=…`` fills a
        # language dict, ``pos.lat=…&pos.lng=…`` a spatial pair.
        dotted: dict[str, dict] = {}
        for key, value in data.items():
            base_name, separator, suffix = key.partition(".")
            if separator and base_name in structure and not structure[base_name]["readonly"]:
                dotted.setdefault(base_name, {})[suffix] = value
        for base_name, subs in dotted.items():
            if structure[base_name]["languages"]:
                cleaned[base_name] = subs
            elif structure[base_name]["type"] == "spatial":
                cleaned[base_name] = [subs.get("lat"), subs.get("lng")]
            elif structure[base_name]["type"] == "record" \
                    and not structure[base_name]["multiple"]:
                cleaned[base_name] = subs  # address.street=… → nested dict
            elif structure[base_name].get("multiple") \
                    and subs and all(key.partition(".")[0].isdigit() for key in subs):
                # Indexed client input — ``name.<idx>.<field>=…``: vi/admin4
                # posts multiple records and relations/references WITH a
                # using payload this way. Rebuilt in index order; relational
                # entries take the bone's wire shape (dest key + payload).
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

        # Empty submissions clear the bone, like core's fromClient —
        # recursing into records, normalizing spatial dicts, multiples → [].
        _normalize_empty_values(structure, cleaned)

        # Write-only bones (Password/Credential) follow core's PasswordBone:
        # an EMPTY submission is ignored entirely — the form always renders
        # them empty (masked), so "" must not overwrite the stored value.
        for name in cls.viur_write_only():
            if name in cleaned and cleaned[name] in ("", None):
                del cleaned[name]

        relation_errors = []

        # Cross-store references: the client sends an opaque datastore key
        # (or the dump's ``{"dest": {"key": …}}`` shape) — the target
        # skeleton is read and the dest snapshot (re)built; unknown keys
        # are rejected here (the datastore read IS the existence check).
        # Exception: when the input carries a FULL snapshot (the dump shape
        # that edit-merges roundtrip) and the target has vanished, the old
        # snapshot is kept — a deleted target must not block unrelated
        # edits. Only actively setting a bare unknown key rejects.
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
                    dests.append(stale_snapshot)  # target gone → keep as-is
                else:
                    relation_errors.append(relation_error(name, "Unknown key"))
            if marker.multiple:
                cleaned[name] = dests
            else:
                cleaned[name] = dests[0] if dests else None

        # Relational input arrives as opaque key strings (or the dump's
        # ``{"dest": {"key": …}}`` shape). To-one keys map onto the FK
        # column; many-to-many key lists cannot be applied without a
        # session, so their parsed primary keys are parked on the instance
        # (``_viur_pending_relations``) for SQLList to resolve and assign.
        # Whether the targets exist is checked by SQLList inside its
        # session; here only the key format is validated.
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
                    """Validate one link row (dest + edge payload) — errors
                    keep the bone's wire path (``[name, "rel", field]``).

                    The returned row is built with only the SUBMITTED keys:
                    ``model_validate`` would pin every unset column
                    (incl. the parent FK) to an explicit ``None``, which
                    breaks SQLAlchemy's flush ordering on list replacement.
                    """
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
                        # link-backed datastore reference — the read builds
                        # the snapshot and IS the existence check; roundtripped
                        # full snapshots survive a vanished target (see
                        # _split_dest_input).
                        dest = _crossstore.read_dest(marker, str(item))
                        if dest is None:
                            dest = stale_snapshot
                        if dest is None:
                            relation_errors.append(relation_error(rel_name, "Unknown key"))
                        elif (link := _validated_link(
                            info["target"], {"key": dest["key"], "dest": dest} | payload,
                        )) is not None:
                            primary_keys.append(link)  # ready link rows
                        continue
                    primary_key = info["target"].viur_parse_key(str(item))
                    if primary_key is None:
                        relation_errors.append(relation_error(rel_name))
                    elif link_cls := info.get("link"):
                        # association object: dest FK + validated payload
                        if (link := _validated_link(
                            link_cls, {info["dest_fk"]: primary_key} | payload,
                        )) is not None:
                            primary_keys.append(link)
                    else:
                        primary_keys.append(primary_key)
                # MultipleConstraints enforcement (min/max/duplicates from
                # viur_relation_meta), mirroring the bone's validation.
                # Like core, DUPLICATES ARE FORBIDDEN BY DEFAULT — with or
                # without declared constraints: the SQL link tables cannot
                # even represent them (their composite primary key collapses
                # duplicates silently), so submitting one must be an error,
                # not a silent loss.
                constraints = getattr(cls, "viur_relation_meta", {}) \
                    .get(rel_name, {}).get("multiple")
                if not isinstance(constraints, dict):
                    constraints = {}

                def _identity(item: t.Any) -> t.Any:
                    if isinstance(item, SQLModel):  # link rows
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
        """Best-effort UNVALIDATED instance carrying the submitted values.

        The rejected re-render roundtrips the client's input like a skeleton
        does (``skel.fromClient`` fills the skel even when it fails) — losing
        every field on a validation error is not acceptable form UX. Table
        models skip validation in ``__init__``; plain models go through
        ``model_construct``. **Never persist such an instance.**
        """
        if getattr(cls, "__table__", None) is not None:
            form = cls(**cleaned)
        else:
            form = cls.model_construct(**cleaned)
        form.__dict__["_viur_unvalidated"] = True
        if pending_relations:
            form.__dict__["_viur_pending_relations"] = pending_relations
        return form

    # --- Renderable protocol (analysis/01 §3.1) ---------------------------
    # SkeletonInstance-compatible aliases, so the same envelope code renders
    # both worlds.

    def dump(self, *, bones: t.Iterable[str] = ()) -> dict:
        return self.viur_dump(bones=bones)

    def structure(self) -> dict:
        return self.viur_structure()

    @classmethod
    def _viur_kind(cls) -> str:
        table_name = getattr(cls, "__tablename__", None)
        # On non-table models __tablename__ is SQLAlchemy's declared_attr
        # descriptor, not a string — fall back to the class name.
        return table_name if isinstance(table_name, str) else cls.__name__.lower()

    @classmethod
    def viur_encode_key(cls, primary_key: int | str) -> str:
        """Encode a primary-key value into the opaque key string."""
        raw = f"{cls._viur_kind()}{KEY_SEPARATOR}{primary_key}".encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @property
    def viur_key(self) -> str | None:
        """Opaque, urlsafe key string — clients must never parse it."""
        if self.id is None:
            return None
        return type(self).viur_encode_key(self.id)

    @classmethod
    def viur_parse_key(cls, key: str) -> int | str | None:
        """Decode a key produced by :attr:`viur_key`.

        Returns the primary-key value, or ``None`` for malformed keys and
        keys of other models (no exception, no table-name leak).
        """
        try:
            raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            return None
        table_name, separator, primary_key = raw.partition(KEY_SEPARATOR)
        if not separator or table_name != cls._viur_kind() or not primary_key:
            return None
        return int(primary_key) if primary_key.isdigit() else primary_key
