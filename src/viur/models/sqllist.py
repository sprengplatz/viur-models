"""``SQLList`` — module prototype serving one ``Model`` over envelope v2 with the viur-actions
hook chain (``can<X>``/``on<X>``/``then<X>``/``<X>Skel``); suffix-less defaults are fail-closed."""
import base64
import datetime
import decimal
import json
import typing as t

from sqlalchemy import and_, false, nullslast, or_
from sqlalchemy.orm import load_only, selectinload
from sqlmodel import select

from viur.actions import ActionModule, action
from viur.actions.runtime import get_hook_method, get_resolved_hooks
from viur.core import Module, current, errors
from viur.core.decorators import exposed, force_post, force_ssl, skey

from .base import Model, _dump_value, _utcnow
from .structure import MODULE_BY_MODEL
from . import crossstore as _crossstore
from .db import get_engine, get_session

DEFAULT_LIMIT = 30
MAX_LIMIT = 100
X_VIUR_BONELIST = "X-VIUR-BONELIST"


def _encode_cursor(orderby: str | None, descending: bool, row: t.Any) -> str:
    """Keyset cursor ``[sort value, id]`` bound to its order; opaque to clients."""
    values = [row.id]
    if orderby:
        values.insert(0, _dump_value(getattr(row, orderby)))
    payload = json.dumps(
        {"o": orderby, "d": "desc" if descending else "asc", "v": values},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: t.Any) -> dict | None:
    """Cursor → keyset payload; malformed cursors restart at the beginning."""
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
    """JSON-roundtripped cursor value → the column's python type."""
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
    """Truthy client strings (``utils.parse.bool``)."""
    return str(value).strip().lower() in ("true", "yes", "1")


def _is_post_request() -> bool:
    """``request.isPostRequest``; ``True`` outside a request context."""
    return bool(getattr(current.request.get(), "isPostRequest", True))


def _client_bones(structure: dict, model_cls: type[Model]) -> frozenset | None:
    """Bones requested via ``X-VIUR-BONELIST`` (core's client-defined subskel): the header's
    names known to the structure, plus ``key`` and ``model_cls.viur_bones_always``. ``None``
    without the header. Sets ``Vary`` like core."""
    request = current.request.get()
    headers = getattr(getattr(request, "request", None), "headers", None)
    raw = headers.get(X_VIUR_BONELIST) if headers is not None else None
    if not raw:
        return None
    names = {name.strip() for name in raw.split(",")} | {"key", *model_cls.viur_bones_always}
    if (response := getattr(request, "response", None)) is not None:
        response.vary = (X_VIUR_BONELIST, *(getattr(response, "vary", None) or ()))
    return frozenset(name for name in names if name in structure)


def _escape_like(value: str) -> str:
    """Backslash-escape LIKE wildcards (see ``_ilike``)."""
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _ilike(column: t.Any, pattern: str, model_cls: type[Model]) -> t.Any:
    """Case-insensitive LIKE with backslash escaping; BigQuery's LIKE has no ``ESCAPE`` clause."""
    if get_engine(model_cls).dialect.name == "bigquery":
        return column.ilike(pattern)
    return column.ilike(pattern, escape="\\")


def _coerce_numeric(value: t.Any) -> t.Any:
    """Client filter string → number; ``None`` when unusable (the filter is ignored)."""
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value)
        return float(text) if "." in text else int(text)
    except ValueError:
        return None


def _drop_structure_caches(cls: type = Model) -> None:
    """Drop cached structures in the Model tree (they may carry the kind fallback as ``module``)."""
    for sub in cls.__subclasses__():
        if "_viur_structure" in sub.__dict__:
            del sub._viur_structure
        _drop_structure_caches(sub)


class ModelList(list):
    """Instances plus ``getCursor()``/``get_orders()`` for ``render_list``."""

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
    """Module prototype serving one ``Model``."""

    handler = "list"

    #: Renderer families (``__build_app`` reads truthy class attributes).
    json = True
    vi = True

    #: Envelope version; ``viur.actions.install()`` upgrades every mount carrying it.
    json_version = 2

    model: t.ClassVar[type[Model] | None] = None

    def __init__(self, moduleName: str, modulePath: str, *args: t.Any, **kwargs: t.Any):
        if type(self).model is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set the ``model`` class attribute "
                "to the Model it serves."
            )
        super().__init__(moduleName, modulePath, *args, **kwargs)
        # first mount wins; caches built before it carry the kind fallback
        if MODULE_BY_MODEL.setdefault(type(self).model, moduleName) == moduleName:
            _drop_structure_caches()

    # --- suffix-less default hooks (viur-actions fallback chain) -----------

    def can(self, instance: Model | None) -> bool:
        """Fail-closed default."""
        return False

    def on(self, instance: Model) -> None:
        """Pre-commit hook default."""

    def then(self, instance: Model) -> None:
        """Post-commit hook default."""

    def skel(self, *args: t.Any, **kwargs: t.Any) -> type[Model]:
        """Model factory slot (``<x>Skel`` may narrow the model)."""
        return type(self).model

    def sqlFilter(self, stmt: t.Any) -> t.Any:
        """``listFilter`` analogue."""
        return stmt

    # --- helpers ------------------------------------------------------------

    def _require_v2_render(self) -> None:
        """``NotAcceptable`` unless the render reports ``version >= 2`` (duck-typed)."""
        if getattr(getattr(self, "render", None), "version", 0) < 2:
            raise errors.NotAcceptable(
                f"module {self.moduleName!r} serves envelope v2 only, but its "
                "render is not v2-capable. Call viur.actions.install() at app "
                "boot — SQLList's json_version = 2 pin upgrades every mount "
                "from there."
            )

    def _check(self, hooks: t.Any, instance: Model | None) -> None:
        if not get_hook_method(self, hooks, "can")(instance):
            raise errors.Forbidden()

    def _with_relations(
        self, stmt: t.Any, model_cls: type[Model], only: frozenset | None = None,
    ) -> t.Any:
        """Eager-load relations (dumps run detached); ``only`` limits them to the requested
        bones. Association links chain-load ``dest``."""
        for rel_name, info in model_cls.viur_relations().items():
            if only is not None and rel_name not in only:
                continue
            loader = selectinload(getattr(model_cls, rel_name))
            if info.get("link"):
                loader = loader.selectinload(getattr(info["link"], info["dest_rel"]))
            stmt = stmt.options(loader)
        return stmt

    def _restrict(
        self, stmt: t.Any, model_cls: type[Model], bones: frozenset | None,
        extra: t.Iterable[str] = (),
    ) -> t.Any:
        """Fetch only what a client bonelist needs — core unserializes a bone on access, here
        it is not even read: ``load_only`` on the requested columns (plus ``extra``, e.g. the
        sort column the cursor reads), ``selectinload`` on the requested relations. A computed
        bone reads arbitrary columns, so any of them keeps the full row. ``None``: full load."""
        if bones is None:
            return self._with_relations(stmt, model_cls)
        relations = model_cls.viur_relations()
        if not bones & set(model_cls.model_computed_fields):
            names = {"id"}
            for name in (*bones, *extra):
                if name in relations:
                    if (fk := relations[name]["fk"]) is not None:
                        names.add(fk)
                elif name in model_cls.model_fields:
                    names.add(name)
            stmt = stmt.options(load_only(*(getattr(model_cls, name) for name in names)))
        return self._with_relations(stmt, model_cls, only=bones)

    def _load(
        self, model_cls: type[Model], session: t.Any, key: t.Any,
        bones: frozenset | None = None,
    ) -> Model:
        primary_key = model_cls.viur_parse_key(str(key))
        if primary_key is None:
            raise errors.NotFound()
        stmt = self._restrict(
            select(model_cls).where(model_cls.id == primary_key), model_cls, bones,
        )
        instance = session.exec(stmt).one_or_none()
        if instance is None:
            raise errors.NotFound()
        return instance

    def _verify_relations(
        self, model_cls: type[Model], instance: Model, session: t.Any,
    ) -> list:
        """Existence check of relational input (``viur_from_client`` validates the key format only)."""
        from .client import relation_error

        pending = instance.__dict__.get("_viur_pending_relations", {})
        errs = []
        for rel_name, info in model_cls.viur_relations().items():
            if info.get("crossstore"):
                continue  # checked by read_dest
            if info.get("link"):
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
        model_cls: type[Model],
        instance: Model,
        source: Model,
        session: t.Any,
    ) -> None:
        """Resolve parked many-to-many keys into instances and assign them; ``source`` carries
        the pending map (the validated instance on edit, the instance itself on add)."""
        pending = source.__dict__.get("_viur_pending_relations")
        if not pending:
            return
        relations = model_cls.viur_relations()
        for rel_name, primary_keys in pending.items():
            info = relations[rel_name]
            if info.get("crossstore") or info.get("link"):
                targets = list(primary_keys)  # ready link rows; parent FK set on flush
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
        self._require_v2_render()
        hooks = get_resolved_hooks(self, "list")
        self._check(hooks, None)
        model_cls = get_hook_method(self, hooks, "skel")()
        structure = model_cls._viur_structure_shared()  # read-only hot path
        bones = _client_bones(structure, model_cls)

        limit = _clamp_limit(kwargs.pop("limit", DEFAULT_LIMIT))
        cursor_payload = _decode_cursor(kwargs.pop("cursor", None))
        orderby = kwargs.pop("orderby", None)
        descending = str(kwargs.pop("orderdir", "0")).lower() in ("1", "desc", "descending")

        stmt = select(model_cls)

        # search: OR-LIKE over string-family fields; nothing searchable → unsatisfiable
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
                    _ilike(column, pattern, model_cls) for column in searchable
                ]))
            else:
                stmt = stmt.where(false())

        # filters: field=value (lists → IN), $lt/$le/$gt/$ge/$lk on structure-known
        # writable scalars. Relations, cross-store refs and write-only bones have no
        # usable SQL expression — gates filters AND orderby.
        unqueryable = (
            write_only
            | set(model_cls.viur_relations())
            | set(model_cls.viur_crossstore())
        )
        for raw_key, value in kwargs.items():
            name, _, operator = raw_key.partition("$")
            if name not in structure or structure[name]["readonly"] \
                    or name in unqueryable or not hasattr(model_cls, name):
                continue
            if operator and isinstance(value, (list, tuple)):
                continue  # operators take scalars
            if structure[name]["type"].startswith("numeric"):
                if isinstance(value, (list, tuple)):
                    value = [v for v in map(_coerce_numeric, value) if v is not None]
                elif (value := _coerce_numeric(value)) is None:
                    continue
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
                    _ilike(column, f"{_escape_like(str(value))}%", model_cls),
                )
            elif operator:  # unknown suffix: equality
                stmt = stmt.where(column == value)
            elif isinstance(value, (list, tuple)):
                stmt = stmt.where(column.in_(value))
            else:
                stmt = stmt.where(column == value)

        # total order (sort column NULLS LAST, id tiebreaker); the cursor seeks past
        # the last row instead of an OFFSET
        orders = []
        sort_column = None
        if orderby and orderby in structure and not structure[orderby]["readonly"] \
                and orderby not in unqueryable and hasattr(model_cls, orderby):
            sort_column = getattr(model_cls, orderby)
            stmt = stmt.order_by(
                nullslast(sort_column.desc() if descending else sort_column.asc()),
                model_cls.id.asc(),
            )
            orders.append((orderby, "desc" if descending else "asc"))
        else:
            orderby = None
            stmt = stmt.order_by(model_cls.id.asc())

        # a cursor is only valid for its order
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

        stmt = self._restrict(stmt, model_cls, bones, extra=(orderby,) if orderby else ())
        stmt = self.sqlFilter(stmt).limit(limit + 1)
        with get_session(model_cls) as session:
            rows = list(session.exec(stmt).all())

        has_more = len(rows) > limit
        rows = rows[:limit]
        cursor = _encode_cursor(orderby, descending, rows[-1]) if has_more else None
        if bones:
            for row in rows:
                row.__dict__["_viur_bones"] = bones
        return self.render.list(ModelList(rows, cursor=cursor, orders=orders))

    @action
    @exposed
    def view(self, key: str, **kwargs: t.Any) -> t.Any:
        self._require_v2_render()
        hooks = get_resolved_hooks(self, "view")
        model_cls = get_hook_method(self, hooks, "skel")()
        bones = _client_bones(model_cls._viur_structure_shared(), model_cls)
        with get_session(model_cls) as session:
            instance = self._load(model_cls, session, key, bones)
            self._check(hooks, instance)  # inside the session, like edit/delete
        if bones:
            instance.__dict__["_viur_bones"] = bones
        return self.render.view(instance)

    @action
    @force_ssl
    @exposed
    @skey(allow_empty=True)
    def add(self, **kwargs: t.Any) -> t.Any:
        self._require_v2_render()
        hooks = get_resolved_hooks(self, "add")
        self._check(hooks, None)
        model_cls = get_hook_method(self, hooks, "skel")()
        kwargs.pop("skey", None)
        bounce = _truthy(kwargs.pop("bounce", None))  # validated re-render, never writes

        if not kwargs:  # fresh form
            return self.render.add(model_cls())

        instance, client_errors = model_cls.viur_from_client(kwargs)
        if client_errors:  # rejected re-render
            instance.errors = client_errors
            return self.render.add(instance)

        with get_session(model_cls) as session:
            if relation_errors := self._verify_relations(model_cls, instance, session):
                instance.errors = relation_errors
                return self.render.add(instance)  # unknown relation target
            if bounce or not _is_post_request():
                return self.render.add(instance)  # validated preview
            self._assign_pending_relations(model_cls, instance, instance, session)
            get_hook_method(self, hooks, "on")(instance)  # onAdd
            session.add(instance)
            session.flush()
            _crossstore.sync_index(instance, session)
        get_hook_method(self, hooks, "then")(instance)  # thenAdd
        return self.render.addSuccess(instance)

    @action
    @force_ssl
    @exposed
    @skey(allow_empty=True)
    def edit(self, key: str, **kwargs: t.Any) -> t.Any:
        self._require_v2_render()
        hooks = get_resolved_hooks(self, "edit")
        model_cls = get_hook_method(self, hooks, "skel")()
        kwargs.pop("skey", None)
        bounce = _truthy(kwargs.pop("bounce", None))
        structure = model_cls._viur_structure_shared()  # read-only hot path
        relations = model_cls.viur_relations()
        editable = [
            name for name, bone in structure.items()
            if name != "key" and not bone["readonly"]
        ]
        # to-one relations are assigned via their FK column, many-to-many from the pending map
        assignable = [name for name in editable if name not in relations]
        assignable += [
            info["fk"] for name, info in relations.items()
            if name in editable and info["fk"] is not None
        ]

        with get_session(model_cls) as session:
            instance = self._load(model_cls, session, key)
            self._check(hooks, instance)

            if not kwargs:  # fresh form
                return self.render.edit(instance)

            # merge over the stored dump; write-only excluded (masked dump), multiples
            # cleared (browsers post nothing for an empty selection)
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
            if client_errors:  # rejected re-render, keyed like the stored row
                validated.id = instance.id
                validated.errors = client_errors
                return self.render.edit(validated)

            if relation_errors := self._verify_relations(model_cls, validated, session):
                validated.id = instance.id
                validated.errors = relation_errors
                return self.render.edit(validated)  # unknown relation target

            if bounce or not _is_post_request():  # validated preview; validated is transient
                validated.id = instance.id
                return self.render.edit(validated)

            for name in assignable:
                if name in write_only and getattr(validated, name) in (None, ""):
                    continue  # empty write-only input keeps the stored value
                setattr(instance, name, getattr(validated, name))
            # expire loaded to-one relations (an FK may have changed); many-to-many are assigned below
            if single := [n for n, i in relations.items() if not i["multiple"]]:
                session.expire(instance, single)
            self._assign_pending_relations(model_cls, instance, validated, session)
            _crossstore.sync_index(instance, session)
            instance.changedate = _utcnow()
            get_hook_method(self, hooks, "on")(instance)  # onEdit

        get_hook_method(self, hooks, "then")(instance)  # thenEdit
        return self.render.editSuccess(instance)

    @action
    @force_ssl
    @force_post
    @exposed
    @skey
    def delete(self, key: str, **kwargs: t.Any) -> t.Any:
        self._require_v2_render()
        hooks = get_resolved_hooks(self, "delete")
        model_cls = get_hook_method(self, hooks, "skel")()
        with get_session(model_cls) as session:
            instance = self._load(model_cls, session, key)
            self._check(hooks, instance)
            get_hook_method(self, hooks, "on")(instance)  # onDelete
            _crossstore.drop_index(instance, session)
            session.delete(instance)
        get_hook_method(self, hooks, "then")(instance)  # thenDelete
        return self.render.deleteSuccess(instance)

    @exposed
    def structure(self, action: str = "view") -> t.Any:
        """Structure per action (``<action>Skel`` + ``can<action>``), like ``List.structure``."""
        self._require_v2_render()
        try:
            hooks = get_resolved_hooks(self, action)
        except KeyError:
            raise errors.NotImplemented(f"The action {action!r} is not implemented.")
        model_cls = get_hook_method(self, hooks, "skel")()
        self._check(hooks, None)
        return self.render.render(f"structure.{action}", model_cls())

