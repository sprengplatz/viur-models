"""Cross-store relations — SQL models referencing **datastore skeletons**.

``SkeletonRef(kind, …)`` builds a field type whose value is the ``dest``
snapshot of a referenced skeleton entity (a plain JSON dict: the encoded
datastore key + the ``ref_keys`` values), stored in a JSON column — the
same denormalization the real ``RelationalBone`` writes into its entity::

    author: UserRef() | None = ViURField(default=None, sa_type=JSON, descr="Autor")

On client input (an opaque datastore key or a ``{"dest": {"key": …}}``
shape) the target skeleton is read from the datastore and the snapshot is
(re)built — actively setting an unknown key is rejected, while a
roundtripped full snapshot whose target has vanished is kept (a deleted
target must not block unrelated edits). Like viur-core between
``updateRelations`` runs, snapshots can go stale when the target changes;
:func:`refresh_crossstore` is the repair job — run it from a project cron
or deferred task (``missing="keep"`` or ``"set_null"``).

The core lookups (``RefSkel.fromSkel`` / ``skeletonByKind``) are imported
lazily and isolated in :func:`resolve_relskel` / :func:`read_dest`, so
unit tests can substitute them and plain model imports stay core-free.
"""
import dataclasses
import typing as t

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


@dataclasses.dataclass(frozen=True)
class SkeletonRefMarker:
    """Annotation marker produced by :func:`SkeletonRef`."""

    kind: str
    ref_keys: tuple[str, ...]
    module: str
    type_suffix: str | None
    multiple: bool
    format: str | None = None
    extras: t.Any = None  # extra structure keys (e.g. FileBone's hints)


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
    """Build a cross-store reference type for a datastore skeleton kind.

    :param kind: The target skeleton's ``kindName``.
    :param ref_keys: Target bones carried in the ``dest`` snapshot /
        ``relskel`` (``key`` and ``shortkey`` are always included, like
        ``RelationalBone``).
    :param module: The viur module serving the target (defaults to *kind*).
    :param type_suffix: Inserted into the type string —
        ``relational.<suffix>.<kind>`` (``FileBone`` style); plain
        references emit ``relational.<kind>``.
    :param multiple: List of references instead of a single one.
    :param format: Display-format default (``ViURField(format=…)`` wins).
    :param extras: Extra structure keys emitted verbatim.
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
    """``UserBone`` analogue — a reference into the ``user`` module
    (``relational.user``, with the bone's display format)."""
    kwargs.setdefault("format", "$(dest.lastname), $(dest.firstname) ($(dest.name))")
    return SkeletonRef("user", ref_keys, **kwargs)


def FileRef(
    ref_keys: t.Sequence[str] = (
        "name", "mimetype", "size", "width", "height",
        "dlkey", "serving_url", "derived", "public",
    ),
    **kwargs: t.Any,
) -> t.Any:
    """``FileBone`` analogue — a reference into the file store
    (``relational.tree.leaf.file.file``).

    .. note::
        ``FileBone``'s own structure hints (``valid_mime_types``,
        ``public``) are not emitted by default — pass them via
        ``extras=`` if your client needs them.
    """
    kwargs.setdefault("type_suffix", "tree.leaf.file")
    return SkeletonRef("file", ref_keys, **kwargs)


def resolve_relskel(marker: SkeletonRefMarker) -> dict:
    """The ``relskel`` structure of the target — from the REAL skeleton
    registry (``RefSkel.fromSkel``), exactly like ``RelationalBone``
    (which always includes ``key`` and ``shortkey`` in its refKeys)."""
    from viur.core.skeleton import RefSkel

    ref_cls = RefSkel.fromSkel(marker.kind, "key", "shortkey", *marker.ref_keys)
    return ref_cls().structure()


def read_dest(marker: SkeletonRefMarker, key: str) -> dict | None:
    """Read the referenced entity and build its ``dest`` snapshot.

    Returns ``None`` for unknown keys (→ ``Invalid`` on the field)."""
    from viur.core.skeleton import skeletonByKind

    skel = skeletonByKind(marker.kind)()
    reader = getattr(skel, "read", None) or skel.fromDB  # 3.8 fallback
    if not reader(key):
        return None
    return skel.dump(bones=("key", "shortkey", *marker.ref_keys))

class SkeletonLink(SQLModel):
    """Base for **link-table-backed** multiple cross-store references —
    the ``link_model`` shape for datastore targets: one row per reference
    (queryable/joinable), instead of a JSON array column::

        class EntryFeedbackLink(SkeletonLink, table=True):
            __tablename__ = "example_entry_feedback"
            viur_kind = "feedback"
            viur_link_ref_keys = ("subject",)

            entry_id: int | None = Field(
                default=None, foreign_key="example_entry.id", primary_key=True,
            )

        class ExampleEntry(ViURModel, table=True):
            feedback_history: list[EntryFeedbackLink] = Relationship(
                sa_relationship_kwargs={"cascade": "all, delete-orphan"},
            )

    The base carries the datastore ``key`` (part of the primary key) and
    the ``dest`` snapshot; subclasses add their parent FK and the marker
    ClassVars (``viur_kind`` — required — plus optional
    ``viur_link_ref_keys`` / ``viur_link_module`` / ``viur_link_type_suffix``
    / ``viur_link_format`` / ``viur_link_extras``).

    .. note::
        The parent relationship needs ``cascade="all, delete-orphan"`` —
        replacing the reference list must delete the detached rows (their
        FK is part of the primary key).
    """

    key: str = Field(primary_key=True)
    dest: dict = Field(default_factory=dict, sa_type=JSON)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: t.Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        _register_link(cls)  # for targeted refresh_for_target lookups

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
    """Refresh the stored ``dest`` snapshots — the ``updateRelations``
    analogue for cross-store references. Call it from a project cron or
    deferred task::

        from viur.models import refresh_crossstore
        refresh_crossstore(ExampleEntry, missing="set_null")

    Re-reads every referenced datastore target and rewrites stale
    snapshots. Vanished targets follow *missing*:

    - ``"keep"`` (default): the stale snapshot stays (core's
      ``RelationalConsistency.Ignore``),
    - ``"set_null"``: single references clear to ``None``, entries drop
      out of JSON lists, ``SkeletonLink`` rows are deleted.

    JSON-column references require a full table scan per model;
    ``SkeletonLink`` tables refresh row-by-row over the indexed ``key``
    column — prefer the link-table shape for large data sets.

    :returns: ``{"checked": …, "refreshed": …, "cleared": …}``
    """
    from sqlmodel import select

    from .db import get_session

    if missing not in ("keep", "set_null"):
        raise ValueError('missing must be "keep" or "set_null"')

    stats = {"checked": 0, "refreshed": 0, "cleared": 0}
    seen_link_tables: set[type] = set()

    def _fresh(marker: SkeletonRefMarker, dest: dict) -> dict | None:
        stats["checked"] += 1
        return read_dest(marker, dest.get("key"))

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


# --------------------------------------------------------------------------- #
# relations index — the viur-relations analogue                               #
# --------------------------------------------------------------------------- #

class CrossStoreIndex(SQLModel, table=True):
    """Reverse index of cross-store references — the ``viur-relations``
    analogue: one row per (datastore target key → referencing table/row/
    field). Maintained by ``SQLList`` on every write, so
    :func:`refresh_for_target` can update exactly the affected rows
    instead of scanning tables. ``SkeletonLink`` tables are not indexed
    here — their indexed ``key`` column IS the reverse index.
    """

    __tablename__ = "viur_models_relations"

    id: int | None = Field(default=None, primary_key=True)
    target_key: str = Field(index=True)
    model_table: str = Field(index=True)
    row_id: int
    field: str


#: All table models with JSON-column cross-store fields (auto-registered
#: at class definition) — the scan set for refresh_for_target.
MODEL_REGISTRY: list[type] = []

#: All SkeletonLink table classes (auto-registered at class definition).
LINK_REGISTRY: list[type] = []


def _register_model(cls: type) -> None:
    if cls not in MODEL_REGISTRY:
        MODEL_REGISTRY.append(cls)


def _register_link(cls: type) -> None:
    if cls not in LINK_REGISTRY:
        LINK_REGISTRY.append(cls)


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
    """Targeted refresh: update every stored snapshot referencing ONE
    datastore entity — the incremental ``updateRelations`` path.

    Wire it into the target module's skeleton hooks (deferred, like core)::

        from viur.core.tasks import CallDeferred
        from viur.models import refresh_for_target

        _refresh = CallDeferred(refresh_for_target)

        class user(User):
            def onEdited(self, skel):
                super().onEdited(skel)
                _refresh(str(skel["key"]))

            def onDeleted(self, skel):
                super().onDeleted(skel)
                _refresh(str(skel["key"]), missing="set_null")

    JSON-column references resolve over the ``viur_models_relations``
    index; ``SkeletonLink`` tables are updated directly over their ``key``
    column. Policies as in :func:`refresh_crossstore`.
    """
    from sqlmodel import select

    from .db import get_session

    if missing not in ("keep", "set_null"):
        raise ValueError('missing must be "keep" or "set_null"')

    stats = {"checked": 0, "refreshed": 0, "cleared": 0}
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
            fresh = read_dest(marker, key)
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
                fresh = read_dest(marker, key)
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
    """Automatic change propagation — call once at app boot (next to
    ``viur.actions.install()``)::

        import viur.models
        viur.models.install_refresh_hooks()

    Wraps ``Skeleton.postSavedHandler`` / ``postDeletedHandler`` — the seam
    every skeleton write/delete passes — so that changes to a **referenced**
    kind defer :func:`refresh_for_target`, exactly like core defers its
    ``update_relations`` task. Writes to unreferenced kinds cost one set
    lookup and nothing else.

    :param missing_on_delete: Policy when the referenced entity is deleted
        (``"set_null"`` clears references, ``"keep"`` leaves snapshots).
    :param countdown: Task delay in seconds (core uses 10 for
        ``update_relations``).

    .. note::
        A skeleton class overriding ``postSavedHandler`` **without calling
        super()** bypasses this — wire such modules manually via their
        ``onEdited``/``onDeleted`` hooks (see :func:`refresh_for_target`).
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
        kinds = set()
        for model_cls in MODEL_REGISTRY:
            kinds.update(marker.kind for marker in model_cls.viur_crossstore().values())
        kinds.update(link.viur_kind for link in LINK_REGISTRY if link.viur_kind)
        return kind in kinds

    @classmethod
    def postSavedHandler(cls, skel, key, dbObj):  # noqa: ANN001 — core signature
        original_saved(cls, skel, key, dbObj)
        if _is_referenced(getattr(skel, "kindName", None)):
            _deferred_refresh(str(key), missing="keep", _countdown=countdown)

    @classmethod
    def postDeletedHandler(cls, skel, key):  # noqa: ANN001 — core signature
        original_deleted(cls, skel, key)
        if _is_referenced(getattr(skel, "kindName", None)):
            _deferred_refresh(str(key), missing=missing_on_delete, _countdown=countdown)

    Skeleton.postSavedHandler = postSavedHandler
    Skeleton.postDeletedHandler = postDeletedHandler
    Skeleton._viur_models_refresh_hooks = True
