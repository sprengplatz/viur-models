"""``SQLList`` — the SQL counterpart of viur-core's ``List`` prototype.

Serves a :class:`~viur.models.ViURModel` over the same endpoints
(``list``/``view``/``add``/``edit``/``delete``/``structure``) and the same
envelope-v2 API as a skeleton module (design: analysis/02). The hook system
(``can<X>``/``on<X>``/``then<X>``/``<X>Skel``) comes unchanged from
viur-actions; the suffix-less defaults on this class are **fail-closed** —
a concrete module must open up access via ``can`` / ``can<X>`` overrides.

This module imports viur-core and viur-actions — it is deliberately **not**
re-exported from ``viur.models`` so that plain model definitions stay free
of the framework import.

    from viur.models.sqllist import SQLList
    from models.feedback import Feedback

    class feedback(SQLList):
        model = Feedback

        def can(self, instance):    # or per-action canView/canEdit/…
            return True
"""
import base64
import datetime
import decimal
import json
import typing as t

from sqlalchemy import and_, false, nullslast, or_
from sqlalchemy.orm import selectinload
from sqlmodel import select

from viur.actions import ActionModule, action
from viur.actions.runtime import get_hook_method, get_resolved_hooks
from viur.core import Module, current, errors
from viur.core.decorators import exposed, force_post, force_ssl, skey

from .base import ViURModel, _dump_value, _utcnow
from .structure import MODULE_BY_MODEL
from . import crossstore as _crossstore
from .db import get_session

DEFAULT_LIMIT = 30
MAX_LIMIT = 100


def _encode_cursor(orderby: str | None, descending: bool, row: t.Any) -> str:
    """Keyset cursor: the last row's sort-key values, bound to the order.

    Opaque to clients; ``[sort value, id]`` (or ``[id]`` without orderby)
    plus the order it belongs to — a cursor is only valid for the query
    that produced it, like core's datastore cursors.
    """
    values = [row.id]
    if orderby:
        values.insert(0, _dump_value(getattr(row, orderby)))
    payload = json.dumps(
        {"o": orderby, "d": "desc" if descending else "asc", "v": values},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: t.Any) -> dict | None:
    """Opaque cursor → keyset payload. Malformed cursors restart at the
    beginning (no error leak)."""
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4))
        payload = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("v"), list):
        return None
    return payload


def _coerce_cursor_value(column: t.Any, value: t.Any) -> t.Any:
    """JSON-roundtripped cursor values back into the column's python type
    (datetimes travel as ISO strings, Decimals as floats)."""
    if value is None:
        return None
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return value
    if python_type is datetime.datetime and isinstance(value, str):
        return datetime.datetime.fromisoformat(value)
    if python_type is datetime.date and isinstance(value, str):
        return datetime.date.fromisoformat(value)
    if python_type is datetime.time and isinstance(value, str):
        return datetime.time.fromisoformat(value)
    if python_type is decimal.Decimal:
        return decimal.Decimal(str(value))
    return value


def _clamp_limit(raw: t.Any) -> int:
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return min(max(limit, 1), MAX_LIMIT)


def _truthy(value: t.Any) -> bool:
    """Truthy client strings, like core's ``utils.parse.bool``."""
    return str(value).strip().lower() in ("true", "yes", "1")


def _is_post_request() -> bool:
    """Core parity: writes require a real POST. Outside a request context
    (library/test use) there is nothing to gate — treated as POST."""
    return bool(getattr(current.request.get(), "isPostRequest", True))


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards in client input (used with ``escape="\\\\"``)."""
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _coerce_numeric(value: t.Any) -> t.Any:
    """Coerce a client filter string for a numeric column — backends like
    Postgres reject string comparisons on numeric columns. Returns ``None``
    for unusable values (the filter is then ignored, like core)."""
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value)
        return float(text) if "." in text else int(text)
    except ValueError:
        return None


def _drop_structure_caches(cls: type = ViURModel) -> None:
    """Drop every cached structure in the ViURModel tree — structures built
    BEFORE a module registered its model would carry the kind fallback as
    the relational ``module``; they rebuild lazily with the registry."""
    for sub in cls.__subclasses__():
        if "_viur_structure" in sub.__dict__:
            del sub._viur_structure
        _drop_structure_caches(sub)


class ModelList(list):
    """A list of model instances + cursor/orders.

    Satisfies the protocol ``render_list`` reads (``getCursor()`` /
    ``get_orders()``), so the envelope carries pagination and sorting
    exactly like a ``SkelList``.
    """

    def __init__(self, items: t.Iterable = (), *, cursor: str | None = None,
                 orders: t.Iterable = ()):
        super().__init__(items)
        self._cursor = cursor
        self._orders = list(orders)

    def getCursor(self) -> str | None:
        return self._cursor

    def get_orders(self) -> list:
        return self._orders


class SQLList(ActionModule, Module):
    """Module prototype serving one ViURModel — see the module docstring."""

    #: Admin handler — clients treat an SQLList like any list module
    #: (that is the parity promise; the wire format is identical).
    handler = "list"

    #: Renderer opt-in — viur-core's ``__build_app`` only instantiates a
    #: module for a renderer family when its class carries a truthy attribute
    #: of that name (same mechanism as ``List.vi = True`` in core). SQLList
    #: targets the JSON/vi envelope APIs; subclasses can opt out
    #: (``json = False``) or add families (``html = True``).
    json = True
    vi = True

    model: t.ClassVar[type[ViURModel] | None] = None

    def __init__(self, moduleName: str, modulePath: str, *args: t.Any, **kwargs: t.Any):
        if type(self).model is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set the ``model`` class attribute "
                "to the ViURModel it serves."
            )
        super().__init__(moduleName, modulePath, *args, **kwargs)
        # Announce "this module serves that model" — relational bones
        # referencing the model emit this name as their ``module`` (admin
        # clients query it for selections). First module wins; structures
        # cached before registration would still carry the kind fallback,
        # so all cached structures are dropped and rebuild lazily.
        if MODULE_BY_MODEL.setdefault(type(self).model, moduleName) == moduleName:
            _drop_structure_caches()

    # --- suffix-less default hooks (viur-actions fallback chain) -----------

    def can(self, instance: ViURModel | None) -> bool:
        """Fail-closed default — override ``can`` / ``can<X>`` to open up."""
        return False

    def on(self, instance: ViURModel) -> None:
        """Pre-commit hook default (no-op)."""

    def then(self, instance: ViURModel) -> None:
        """Post-commit hook default (no-op)."""

    def skel(self, *args: t.Any, **kwargs: t.Any) -> type[ViURModel]:
        """Model factory slot — ``<x>Skel`` overrides may narrow the model."""
        return type(self).model

    def sqlFilter(self, stmt: t.Any) -> t.Any:
        """``listFilter`` analogue — restrict the list statement (tenant
        filters, soft-delete, …). Default: unchanged."""
        return stmt

    # --- helpers ------------------------------------------------------------

    def _check(self, hooks: t.Any, instance: ViURModel | None) -> None:
        if not get_hook_method(self, hooks, "can")(instance):
            raise errors.Forbidden()

    def _with_relations(self, stmt: t.Any, model_cls: type[ViURModel]) -> t.Any:
        """Eager-load all relations — dumps run after the session closed,
        where lazy loading would fail on detached instances. Association
        links chain-load their dest side."""
        for rel_name, info in model_cls.viur_relations().items():
            loader = selectinload(getattr(model_cls, rel_name))
            if info.get("link"):
                loader = loader.selectinload(getattr(info["link"], info["dest_rel"]))
            stmt = stmt.options(loader)
        return stmt

    def _load(self, model_cls: type[ViURModel], session: t.Any, key: t.Any) -> ViURModel:
        primary_key = model_cls.viur_parse_key(str(key))
        if primary_key is None:
            raise errors.NotFound()
        stmt = self._with_relations(
            select(model_cls).where(model_cls.id == primary_key), model_cls,
        )
        instance = session.exec(stmt).one_or_none()
        if instance is None:
            raise errors.NotFound()
        return instance

    def _verify_relations(
        self, model_cls: type[ViURModel], instance: ViURModel, session: t.Any,
    ) -> list:
        """Existence check for relational input — ``viur_from_client`` only
        validates the key *format*; whether the target rows exist needs the
        session and is checked here, before anything is committed."""
        from .client import relation_error

        pending = instance.__dict__.get("_viur_pending_relations", {})
        errs = []
        for rel_name, info in model_cls.viur_relations().items():
            if info.get("crossstore"):
                continue  # datastore read in viur_from_client was the check
            if info.get("link"):
                # association rows: verify their dest FK targets exist
                if any(
                    session.get(info["target"], getattr(link, info["dest_fk"])) is None
                    for link in pending.get(rel_name, ())
                ):
                    errs.append(relation_error(rel_name, "Unknown key"))
            elif info["multiple"]:
                if any(
                    session.get(info["target"], primary_key) is None
                    for primary_key in pending.get(rel_name, ())
                ):
                    errs.append(relation_error(rel_name, "Unknown key"))
            elif (foreign_key := getattr(instance, info["fk"])) is not None \
                    and session.get(info["target"], foreign_key) is None:
                errs.append(relation_error(rel_name, "Unknown key"))
        return errs

    def _assign_pending_relations(
        self,
        model_cls: type[ViURModel],
        instance: ViURModel,
        source: ViURModel,
        session: t.Any,
    ) -> None:
        """Resolve parked many-to-many key lists (``viur_from_client``) into
        target instances and assign them — SQLAlchemy syncs the link table
        on commit. ``source`` carries the pending map (the validated
        instance on edit, the new instance itself on add)."""
        pending = source.__dict__.get("_viur_pending_relations")
        if not pending:
            return
        relations = model_cls.viur_relations()
        for rel_name, primary_keys in pending.items():
            info = relations[rel_name]
            if info.get("crossstore") or info.get("link"):
                # ready link rows (validated in viur_from_client); the
                # parent FK is populated through the relationship on flush.
                targets = list(primary_keys)
            else:
                targets = [
                    session.get(info["target"], primary_key)
                    for primary_key in primary_keys
                ]
            setattr(instance, rel_name, targets)

    # --- actions ------------------------------------------------------------

    @action
    @exposed
    def list(self, **kwargs: t.Any) -> t.Any:
        hooks = get_resolved_hooks(self, "list")
        self._check(hooks, None)
        model_cls = get_hook_method(self, hooks, "skel")()
        structure = model_cls.viur_structure()

        limit = _clamp_limit(kwargs.pop("limit", DEFAULT_LIMIT))
        cursor_payload = _decode_cursor(kwargs.pop("cursor", None))
        orderby = kwargs.pop("orderby", None)
        descending = str(kwargs.pop("orderdir", "0")).lower() in ("1", "desc", "descending")

        stmt = self._with_relations(select(model_cls), model_cls)

        # Fulltext search — the SQL take on core's ``search`` parameter:
        # OR-LIKE over the string-family fields (write-only and language
        # fields excluded). Without any searchable field the query is
        # unsatisfiable, exactly like core without a fulltext adapter.
        write_only = model_cls.viur_write_only()
        if (term := kwargs.pop("search", None)) not in (None, ""):
            searchable = [
                getattr(model_cls, name)
                for name, bone in structure.items()
                if bone["type"] in ("str", "text") or bone["type"].startswith("str.")
                if not bone["readonly"] and not bone["languages"]
                and name not in write_only and hasattr(model_cls, name)
            ]
            if searchable:
                pattern = f"%{_escape_like(str(term))}%"
                stmt = stmt.where(or_(*[
                    column.ilike(pattern, escape="\\") for column in searchable
                ]))
            else:
                stmt = stmt.where(false())

        # Filters — core's query language: ``field=value`` (equality, lists
        # become IN), plus the operator suffixes ``$lt``/``$le``/``$gt``/
        # ``$ge`` and ``$lk`` (case-insensitive prefix match, like
        # StringBone). Only structure-known, writable scalar fields filter;
        # the column lookup goes through the model class, so unknown
        # parameters are ignored (core behavior) and nothing is
        # string-interpolated.
        unfilterable = (
            write_only
            | set(model_cls.viur_relations())
            | set(model_cls.viur_crossstore())
        )
        for raw_key, value in kwargs.items():
            name, _, operator = raw_key.partition("$")
            if name not in structure or structure[name]["readonly"] \
                    or name in unfilterable or not hasattr(model_cls, name):
                continue
            if operator and isinstance(value, (list, tuple)):
                continue  # operators take scalars only (core behavior)
            if structure[name]["type"].startswith("numeric"):
                if isinstance(value, (list, tuple)):
                    value = [v for v in map(_coerce_numeric, value) if v is not None]
                elif (value := _coerce_numeric(value)) is None:
                    continue  # unusable numeric filter → ignored
            column = getattr(model_cls, name)
            if operator == "lt":
                stmt = stmt.where(column < value)
            elif operator == "le":
                stmt = stmt.where(column <= value)
            elif operator == "gt":
                stmt = stmt.where(column > value)
            elif operator == "ge":
                stmt = stmt.where(column >= value)
            elif operator == "lk":
                stmt = stmt.where(
                    column.ilike(f"{_escape_like(str(value))}%", escape="\\"),
                )
            elif operator:  # unknown suffix falls back to equality, like core
                stmt = stmt.where(column == value)
            elif isinstance(value, (list, tuple)):
                stmt = stmt.where(column.in_(value))
            else:
                stmt = stmt.where(column == value)

        # Ordering + keyset pagination. The result always has a total order
        # (``id`` is the tiebreaker; NULLS LAST on the sort column, so the
        # placement is backend-independent) — the cursor then encodes the
        # last row's sort-key values and the next page SEEKS past them
        # instead of counting an OFFSET: stable under concurrent inserts/
        # deletes and O(1) regardless of page depth.
        orders = []
        sort_column = None
        if orderby and orderby in structure and not structure[orderby]["readonly"] \
                and hasattr(model_cls, orderby):
            sort_column = getattr(model_cls, orderby)
            stmt = stmt.order_by(
                nullslast(sort_column.desc() if descending else sort_column.asc()),
                model_cls.id.asc(),
            )
            orders.append((orderby, "desc" if descending else "asc"))
        else:
            orderby = None
            stmt = stmt.order_by(model_cls.id.asc())

        # A cursor is only valid for the order that produced it — on a
        # mismatch (or garbage) the listing restarts from the beginning.
        if cursor_payload is not None and (
            cursor_payload.get("o") != orderby
            or cursor_payload.get("d") != ("desc" if descending else "asc")
        ):
            cursor_payload = None
        if cursor_payload is not None:
            values = cursor_payload["v"]
            if orderby is None:
                stmt = stmt.where(model_cls.id > values[-1])
            elif (last_value := _coerce_cursor_value(sort_column, values[0])) is None:
                # the previous page ended inside the NULL tail
                stmt = stmt.where(and_(
                    sort_column.is_(None), model_cls.id > values[-1],
                ))
            else:
                past_value = (
                    and_(sort_column < last_value, sort_column.is_not(None))
                    if descending else sort_column > last_value
                )
                stmt = stmt.where(or_(
                    past_value,
                    and_(sort_column == last_value, model_cls.id > values[-1]),
                    sort_column.is_(None),  # NULLS LAST: the tail comes after
                ))

        stmt = self.sqlFilter(stmt).limit(limit + 1)
        with get_session() as session:
            rows = list(session.exec(stmt).all())

        # limit+1 fetch decides whether a next page exists; the cursor is
        # derived from the LAST returned row's sort keys.
        has_more = len(rows) > limit
        rows = rows[:limit]
        cursor = _encode_cursor(orderby, descending, rows[-1]) if has_more else None
        return self.render.list(ModelList(rows, cursor=cursor, orders=orders))

    @action
    @exposed
    def view(self, key: str, **kwargs: t.Any) -> t.Any:
        hooks = get_resolved_hooks(self, "view")
        model_cls = get_hook_method(self, hooks, "skel")()
        with get_session() as session:
            instance = self._load(model_cls, session, key)
        self._check(hooks, instance)
        return self.render.view(instance)

    @action
    @force_ssl
    @exposed
    @skey(allow_empty=True)
    def add(self, **kwargs: t.Any) -> t.Any:
        hooks = get_resolved_hooks(self, "add")
        self._check(hooks, None)
        model_cls = get_hook_method(self, hooks, "skel")()
        kwargs.pop("skey", None)
        # core-List parity: ``bounce`` requests a validated re-render
        # (review before saving) and never writes; vi/admin4 opens the add
        # form this way (POST with skey + bounce=true).
        bounce = _truthy(kwargs.pop("bounce", None))

        if not kwargs:  # fresh form
            return self.render.add(model_cls())

        instance, client_errors = model_cls.viur_from_client(kwargs)
        if client_errors:
            # rejected re-render — the form carries the submitted values
            instance.errors = client_errors
            return self.render.add(instance)

        with get_session() as session:
            if relation_errors := self._verify_relations(model_cls, instance, session):
                instance.errors = relation_errors
                return self.render.add(instance)  # unknown relation target
            if bounce or not _is_post_request():
                # validated preview — nothing is saved (core-List parity:
                # writes happen only on a real POST submit without bounce)
                return self.render.add(instance)
            self._assign_pending_relations(model_cls, instance, instance, session)
            get_hook_method(self, hooks, "on")(instance)      # onAdd — before commit
            session.add(instance)
            session.flush()                     # assigns the id …
            _crossstore.sync_index(instance, session)  # … for the relations index
        get_hook_method(self, hooks, "then")(instance)        # thenAdd — after commit
        return self.render.addSuccess(instance)

    @action
    @force_ssl
    @exposed
    @skey(allow_empty=True)
    def edit(self, key: str, **kwargs: t.Any) -> t.Any:
        hooks = get_resolved_hooks(self, "edit")
        model_cls = get_hook_method(self, hooks, "skel")()
        kwargs.pop("skey", None)
        # ``bounce`` = validated re-render, never writes (core-List parity)
        bounce = _truthy(kwargs.pop("bounce", None))
        structure = model_cls.viur_structure()
        relations = model_cls.viur_relations()
        editable = [
            name for name, bone in structure.items()
            if name != "key" and not bone["readonly"]
        ]
        # To-one relations are merged via their bone name but assigned via
        # their FK column — assigning the relationship attribute itself
        # (always unset on the validated instance) would null the relation.
        # Many-to-many relations are assigned from the pending map instead.
        assignable = [name for name in editable if name not in relations]
        assignable += [
            info["fk"] for name, info in relations.items()
            if name in editable and info["fk"] is not None
        ]

        with get_session() as session:
            instance = self._load(model_cls, session, key)
            self._check(hooks, instance)

            if not kwargs:  # fresh form
                return self.render.edit(instance)

            # Merge, not replace: unsubmitted fields keep their stored value —
            # like ``skel.fromClient`` on a loaded skeleton instance.
            # Write-only bones are excluded: their dump is the masked
            # emptyvalue, feeding it back would blank the stored secret.
            # Multiple bones are excluded too — browsers submit NOTHING for
            # an empty multi-selection, so absent means "empty", not "keep
            # stored" (the form always posts its full state); unsubmitted
            # multiples therefore clear.
            write_only = model_cls.viur_write_only()
            multiple = {
                name for name in editable if structure[name].get("multiple")
            }
            merged = (
                {
                    name: value for name, value in instance.viur_dump().items()
                    if name in editable
                    and name not in write_only and name not in multiple
                }
                | {name: [] for name in multiple}
                | kwargs
            )
            validated, client_errors = model_cls.viur_from_client(merged)
            if client_errors:
                # rejected re-render — show the submitted (merged) values,
                # keyed like the stored row; the row itself stays untouched
                validated.id = instance.id
                validated.errors = client_errors
                return self.render.edit(validated)

            if relation_errors := self._verify_relations(model_cls, validated, session):
                validated.id = instance.id
                validated.errors = relation_errors
                return self.render.edit(validated)  # unknown relation target

            if bounce or not _is_post_request():
                # validated preview of the merged values — the stored row is
                # untouched (``validated`` is transient, never in the session)
                validated.id = instance.id
                return self.render.edit(validated)

            for name in assignable:
                if name in write_only and getattr(validated, name) in (None, ""):
                    continue  # empty write-only input keeps the stored value
                setattr(instance, name, getattr(validated, name))
            # Drop stale loaded to-one relations — an FK may have changed;
            # the dump then falls back to the key-only dest from the new FK.
            # Many-to-many collections are assigned below, not expired —
            # expiring them would discard the pending change.
            if single := [n for n, i in relations.items() if not i["multiple"]]:
                session.expire(instance, single)
            self._assign_pending_relations(model_cls, instance, validated, session)
            _crossstore.sync_index(instance, session)
            instance.changedate = _utcnow()
            get_hook_method(self, hooks, "on")(instance)      # onEdit — before commit

        get_hook_method(self, hooks, "then")(instance)        # thenEdit — after commit
        return self.render.editSuccess(instance)

    @action
    @force_ssl
    @force_post
    @exposed
    @skey
    def delete(self, key: str, **kwargs: t.Any) -> t.Any:
        hooks = get_resolved_hooks(self, "delete")
        model_cls = get_hook_method(self, hooks, "skel")()
        with get_session() as session:
            instance = self._load(model_cls, session, key)
            self._check(hooks, instance)
            get_hook_method(self, hooks, "on")(instance)      # onDelete — before commit
            _crossstore.drop_index(instance, session)
            session.delete(instance)
        get_hook_method(self, hooks, "then")(instance)        # thenDelete — after commit
        return self.render.deleteSuccess(instance)

    @exposed
    def structure(self, action: str = "view") -> t.Any:
        """Structure of the served model, per action — mirrors core's
        ``List.structure`` (``<action>Skel`` + ``can<action>`` gate)."""
        try:
            hooks = get_resolved_hooks(self, action)
        except KeyError:
            raise errors.NotImplemented(f"The action {action!r} is not implemented.")
        model_cls = get_hook_method(self, hooks, "skel")()
        self._check(hooks, None)
        return self.render.render(f"structure.{action}", model_cls())

