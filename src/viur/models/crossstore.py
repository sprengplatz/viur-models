"""Cross-store references (SQL models → datastore skeletons): field types, ``SkeletonLink``,
snapshot refresh."""
import copy
import dataclasses
import typing as t

from sqlalchemy import JSON
from sqlmodel import Field as SQLModelField, SQLModel


@dataclasses.dataclass(frozen=True)
class SkeletonRefMarker:
    """Marker produced by ``SkeletonRef``."""

    kind: str
    ref_keys: tuple[str, ...]
    module: str
    type_suffix: str | None
    multiple: bool
    format: str | None = None
    extras: t.Any = None  # extra structure keys


def SkeletonRef(
    kind: str,
    ref_keys: t.Sequence[str] = ("name",),
    *,
    module: str | None = None,
    type_suffix: str | None = None,
    multiple: bool = False,
    format: str | None = None,
    extras: dict | None = None,
) -> t.Any:
    """Cross-store reference type for skeleton ``kind``.

    :param ref_keys: Target bones in ``dest``/``relskel`` (``key``, ``shortkey`` always included).
    :param module: Serving module (default: ``kind``).
    :param type_suffix: ``relational.<suffix>.<kind>`` (``FileBone`` style).
    :param format: Display format default; ``Field(format=…)`` wins.
    :param extras: Extra structure keys, emitted verbatim.
    """
    marker = SkeletonRefMarker(
        kind=kind,
        ref_keys=tuple(ref_keys),
        module=module or kind,
        type_suffix=type_suffix,
        multiple=multiple,
        format=format,
        extras=dict(extras) if extras else None,
    )
    if multiple:
        return t.Annotated[list[dict], marker]
    return t.Annotated[dict, marker]


def UserRef(ref_keys: t.Sequence[str] = ("name", "firstname", "lastname"), **kwargs: t.Any) -> t.Any:
    """``UserBone`` analogue (``relational.user``)."""
    kwargs.setdefault("format", "$(dest.lastname), $(dest.firstname) ($(dest.name))")
    return SkeletonRef("user", ref_keys, **kwargs)


def FileRef(
    ref_keys: t.Sequence[str] = (
        "name", "mimetype", "size", "width", "height",
        "dlkey", "serving_url", "derived", "public",
    ),
    **kwargs: t.Any,
) -> t.Any:
    """``FileBone`` analogue (``relational.tree.leaf.file.file``); ``valid_mime_types``/``public`` via ``extras``."""
    kwargs.setdefault("type_suffix", "tree.leaf.file")
    return SkeletonRef("file", ref_keys, **kwargs)


def resolve_relskel(marker: SkeletonRefMarker) -> dict:
    """Target ``relskel`` via ``RefSkel.fromSkel`` (``key``, ``shortkey`` + ``ref_keys``)."""
    from viur.core.skeleton import RefSkel

    ref_cls = RefSkel.fromSkel(marker.kind, "key", "shortkey", *marker.ref_keys)
    return ref_cls().structure()


def read_dest(marker: SkeletonRefMarker, key: str) -> dict | None:
    """Read the target and build its ``dest`` snapshot; ``None`` for unknown keys."""
    from viur.core.skeleton import skeletonByKind

    skel = skeletonByKind(marker.kind)()
    reader = getattr(skel, "read", None) or skel.fromDB  # 3.8 fallback
    if not reader(key):
        return None
    return skel.dump(bones=("key", "shortkey", *marker.ref_keys))


def _dest_reader() -> t.Callable[[SkeletonRefMarker, str], dict | None]:
    """``read_dest`` cached per refresh run, keyed ``(kind, ref_keys, key)`` (the marker is
    unhashable); returns deep copies."""
    cache: dict[tuple, dict | None] = {}

    def read(marker: SkeletonRefMarker, key: str) -> dict | None:
        cache_key = (marker.kind, marker.ref_keys, key)
        if cache_key not in cache:
            # module global on purpose: tests patch read_dest
            cache[cache_key] = read_dest(marker, key)
        dest = cache[cache_key]
        return copy.deepcopy(dest) if dest is not None else None

    return read


class SkeletonLink(SQLModel):
    """Base for link-table-backed multiple cross-store references (one row per target).

    Carries ``key`` (datastore key, part of the PK) and the ``dest`` snapshot; subclasses add
    the parent FK and set ``viur_kind`` (required) plus the ``viur_link_*`` ClassVars. The
    parent relationship needs ``cascade="all, delete-orphan"``.
    """

    key: str = SQLModelField(primary_key=True)
    dest: dict = SQLModelField(default_factory=dict, sa_type=JSON)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        _register_link(cls)

    viur_kind: t.ClassVar[str | None] = None
    viur_link_ref_keys: t.ClassVar[tuple[str, ...]] = ("name",)
    viur_link_module: t.ClassVar[str | None] = None
    viur_link_type_suffix: t.ClassVar[str | None] = None
    viur_link_format: t.ClassVar[str | None] = None
    viur_link_extras: t.ClassVar[dict | None] = None

    @classmethod
    def viur_marker(cls) -> SkeletonRefMarker:
        if not cls.viur_kind:
            raise TypeError(
                f"{cls.__name__} must set the ``viur_kind`` ClassVar to the "
                "referenced skeleton kind"
            )
        return SkeletonRefMarker(
            kind=cls.viur_kind,
            ref_keys=tuple(cls.viur_link_ref_keys),
            module=cls.viur_link_module or cls.viur_kind,
            type_suffix=cls.viur_link_type_suffix,
            multiple=True,
            format=cls.viur_link_format,
            extras=dict(cls.viur_link_extras) if cls.viur_link_extras else None,
        )


def refresh_crossstore(*model_classes: type, missing: str = "keep") -> dict:
    """Re-read every referenced target and rewrite stale snapshots (full scan per model;
    ``SkeletonLink`` tables via ``key``). ``missing="set_null"`` clears vanished targets.
    Returns ``{"checked", "refreshed", "cleared"}``."""
    from sqlmodel import select

    from .db import get_session

    if missing not in ("keep", "set_null"):
        raise ValueError('missing must be "keep" or "set_null"')

    stats = {"checked": 0, "refreshed": 0, "cleared": 0}
    seen_link_tables: set[type] = set()
    read = _dest_reader()

    def _fresh(marker: SkeletonRefMarker, dest: dict) -> dict | None:
        stats["checked"] += 1  # rows, not reads
        return read(marker, dest.get("key"))

    for model_cls in model_classes:
        json_fields = model_cls.viur_crossstore()
        link_relations = {
            name: info for name, info in model_cls.viur_relations().items()
            if info.get("crossstore") and info["target"] not in seen_link_tables
        }
        if not json_fields and not link_relations:
            continue

        with get_session() as session:
            if json_fields:
                for row in session.exec(select(model_cls)).all():
                    changed = False
                    for name, marker in json_fields.items():
                        value = getattr(row, name)
                        if not value:
                            continue
                        if marker.multiple:
                            fresh_list = []
                            for dest in value:
                                fresh = _fresh(marker, dest)
                                if fresh is None:
                                    if missing == "set_null":
                                        stats["cleared"] += 1
                                        changed = True
                                        continue
                                    fresh = dest
                                elif fresh != dest:
                                    stats["refreshed"] += 1
                                    changed = True
                                fresh_list.append(fresh)
                            if changed:
                                setattr(row, name, fresh_list)
                        else:
                            fresh = _fresh(marker, value)
                            if fresh is None:
                                if missing == "set_null":
                                    setattr(row, name, None)
                                    stats["cleared"] += 1
                                    changed = True
                            elif fresh != value:
                                setattr(row, name, fresh)
                                stats["refreshed"] += 1
                                changed = True
                    if changed:
                        session.add(row)

            for info in link_relations.values():
                link_cls = info["target"]
                seen_link_tables.add(link_cls)
                marker = info["crossstore"]
                for link in session.exec(select(link_cls)).all():
                    fresh = _fresh(marker, {"key": link.key})
                    if fresh is None:
                        if missing == "set_null":
                            session.delete(link)
                            stats["cleared"] += 1
                    elif fresh != link.dest:
                        link.dest = fresh
                        session.add(link)
                        stats["refreshed"] += 1

    return stats


# --- relations index (viur-relations analogue) -----------------------------

class CrossStoreIndex(SQLModel, table=True):
    """Reverse index target key → (table, row, field) of JSON-column references; maintained by
    ``SQLList`` on write. ``SkeletonLink`` tables index themselves via ``key``."""

    __tablename__ = "viur_models_relations"

    id: int | None = SQLModelField(default=None, primary_key=True)
    target_key: str = SQLModelField(index=True)
    model_table: str = SQLModelField(index=True)
    row_id: int
    field: str


#: Table models with JSON-column cross-store fields (auto-registered).
MODEL_REGISTRY: list[type] = []

#: ``SkeletonLink`` classes (auto-registered).
LINK_REGISTRY: list[type] = []


def _register_model(cls: type) -> None:
    if cls not in MODEL_REGISTRY:
        MODEL_REGISTRY.append(cls)


def _register_link(cls: type) -> None:
    if cls not in LINK_REGISTRY:
        LINK_REGISTRY.append(cls)


#: ``(registry sizes, kinds)`` cache of ``referenced_kinds``.
_REFERENCED_KINDS: tuple[tuple[int, int], frozenset] | None = None


def referenced_kinds() -> frozenset:
    """Skeleton kinds referenced by any model or link table. Cached; rebuilt when the registries
    grew (they only grow). Published as one immutable tuple — no lock, nothing restored."""
    global _REFERENCED_KINDS
    stamp = (len(MODEL_REGISTRY), len(LINK_REGISTRY))
    cached = _REFERENCED_KINDS
    if cached is None or cached[0] != stamp:
        kinds = set()
        for model_cls in MODEL_REGISTRY:
            kinds.update(marker.kind for marker in model_cls.viur_crossstore().values())
        kinds.update(link.viur_kind for link in LINK_REGISTRY if link.viur_kind)
        cached = (stamp, frozenset(kinds))
        _REFERENCED_KINDS = cached
    return cached[1]


def sync_index(instance: t.Any, session: t.Any) -> None:
    """Rewrite the index rows for one referencing row (delete + insert)."""
    from sqlalchemy import delete

    model_cls = type(instance)
    fields = model_cls.viur_crossstore()
    if not fields or instance.id is None:
        return
    table = model_cls._viur_kind()
    session.execute(delete(CrossStoreIndex).where(
        CrossStoreIndex.model_table == table,
        CrossStoreIndex.row_id == instance.id,
    ))
    for name, marker in fields.items():
        value = getattr(instance, name)
        dests = value or [] if marker.multiple else ([value] if value else [])
        for dest in dests:
            session.add(CrossStoreIndex(
                target_key=dest["key"], model_table=table,
                row_id=instance.id, field=name,
            ))


def drop_index(instance: t.Any, session: t.Any) -> None:
    """Remove the index rows for one referencing row (on delete)."""
    from sqlalchemy import delete

    if not type(instance).viur_crossstore() or instance.id is None:
        return
    session.execute(delete(CrossStoreIndex).where(
        CrossStoreIndex.model_table == type(instance)._viur_kind(),
        CrossStoreIndex.row_id == instance.id,
    ))


def refresh_for_target(key: str, *, missing: str = "keep") -> dict:
    """Update every snapshot referencing ``key``: JSON columns via the index, ``SkeletonLink`` rows
    via ``key``. ``missing`` as in ``refresh_crossstore``. Called by the refresh hooks."""
    from sqlmodel import select

    from .db import get_session

    if missing not in ("keep", "set_null"):
        raise ValueError('missing must be "keep" or "set_null"')

    stats = {"checked": 0, "refreshed": 0, "cleared": 0}
    read = _dest_reader()
    models_by_table = {
        cls._viur_kind(): cls
        for cls in MODEL_REGISTRY
        if getattr(cls, "__table__", None) is not None
    }

    with get_session() as session:
        index_rows = session.exec(
            select(CrossStoreIndex).where(CrossStoreIndex.target_key == key),
        ).all()
        for entry in index_rows:
            model_cls = models_by_table.get(entry.model_table)
            if model_cls is None:
                continue  # stale index row of an unregistered model
            row = session.get(model_cls, entry.row_id)
            marker = model_cls.viur_crossstore().get(entry.field)
            if row is None or marker is None:
                session.delete(entry)  # referencing row/field is gone
                continue
            stats["checked"] += 1
            fresh = read(marker, key)
            value = getattr(row, entry.field)
            if marker.multiple:
                if fresh is None and missing == "keep":
                    continue
                new_list = []
                for dest in value or []:
                    if dest.get("key") != key:
                        new_list.append(dest)
                    elif fresh is None:  # set_null: drop the entry
                        stats["cleared"] += 1
                        session.delete(entry)
                    else:
                        if fresh != dest:
                            stats["refreshed"] += 1
                        new_list.append(fresh)
                setattr(row, entry.field, new_list)
            else:
                if fresh is None:
                    if missing == "set_null":
                        setattr(row, entry.field, None)
                        stats["cleared"] += 1
                        session.delete(entry)
                elif fresh != value:
                    setattr(row, entry.field, fresh)
                    stats["refreshed"] += 1
            session.add(row)

        for link_cls in LINK_REGISTRY:
            if getattr(link_cls, "__table__", None) is None:
                continue
            marker = link_cls.viur_marker()
            for link in session.exec(select(link_cls).where(link_cls.key == key)).all():
                stats["checked"] += 1
                fresh = read(marker, key)
                if fresh is None:
                    if missing == "set_null":
                        session.delete(link)
                        stats["cleared"] += 1
                elif fresh != link.dest:
                    link.dest = fresh
                    session.add(link)
                    stats["refreshed"] += 1

    return stats


def install_refresh_hooks(*, missing_on_delete: str = "set_null", countdown: int = 10) -> None:
    """Wrap ``Skeleton.postSavedHandler``/``postDeletedHandler`` to defer ``refresh_for_target``
    for referenced kinds. Once at boot; idempotent. Skeletons overriding the handlers without
    ``super()`` must call it from their own ``onEdited``/``onDeleted``.

    :param missing_on_delete: ``"set_null"`` clears references on delete, ``"keep"`` leaves them.
    :param countdown: Task delay in seconds.
    """
    from viur.core import tasks
    from viur.core.skeleton import Skeleton

    if missing_on_delete not in ("keep", "set_null"):
        raise ValueError('missing_on_delete must be "keep" or "set_null"')
    if getattr(Skeleton, "_viur_models_refresh_hooks", False):
        return  # idempotent

    original_saved = Skeleton.postSavedHandler.__func__
    original_deleted = Skeleton.postDeletedHandler.__func__

    @tasks.CallDeferred
    def _deferred_refresh(key: str, missing: str) -> None:
        refresh_for_target(key, missing=missing)

    def _is_referenced(kind: t.Any) -> bool:
        return kind in referenced_kinds()

    @classmethod
    def postSavedHandler(cls, skel, key, dbObj):  # noqa: ANN001
        original_saved(cls, skel, key, dbObj)
        if _is_referenced(getattr(skel, "kindName", None)):
            _deferred_refresh(str(key), missing="keep", _countdown=countdown)

    @classmethod
    def postDeletedHandler(cls, skel, key):  # noqa: ANN001
        original_deleted(cls, skel, key)
        if _is_referenced(getattr(skel, "kindName", None)):
            _deferred_refresh(str(key), missing=missing_on_delete, _countdown=countdown)

    Skeleton.postSavedHandler = postSavedHandler
    Skeleton.postDeletedHandler = postDeletedHandler
    Skeleton._viur_models_refresh_hooks = True
