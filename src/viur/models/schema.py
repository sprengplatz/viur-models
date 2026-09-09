"""Structure snapshots and bone-level diffing; snapshots live in
``<script_location>/structures/<revision>.json``."""
from __future__ import annotations

import dataclasses
import json
import pathlib
import typing as t

#: Diff noise: ``sortindex`` shifts on insert, ``relskel`` mirrors the target.
IGNORED_BONE_KEYS = frozenset({"sortindex", "relskel"})


@dataclasses.dataclass(frozen=True)
class Transition:
    """One bone-level change.

    :param kind: See ``diff``.
    :param old: Previous bone entry (``None`` for additions).
    :param new: Current bone entry (``None`` for removals).
    :param detail: Facts the operation needs (link table, columns, languages, …).
    """

    kind: str
    table: str
    field: str
    old: dict | None = None
    new: dict | None = None
    detail: dict = dataclasses.field(default_factory=dict)


# --- snapshot ---------------------------------------------------------------

def _relation_shape(model: type, rel_name: str, info: dict) -> dict:
    """Physical relation facts for a data migration: link table and its parent/dest FK
    columns, from the mapper."""
    shape = {
        "multiple": bool(info["multiple"]),
        "target_table": info["target"]._viur_kind()
        if hasattr(info["target"], "_viur_kind") else None,
        "fk": info["fk"],
        "using_fields": list(info.get("using_fields") or ()),
        "crossstore": bool(info.get("crossstore")),
    }
    prop = model.__mapper__.relationships[rel_name]
    parent_table = model.__mapper__.local_table

    link_table = None
    if prop.secondary is not None:            # Relationship(link_model=…)
        link_table = prop.secondary
    elif info.get("link") is not None:        # association object
        link_table = info["link"].__mapper__.local_table
    elif info.get("crossstore"):              # SkeletonLink table
        link_table = info["target"].__mapper__.local_table

    if link_table is not None:
        shape["link_table"] = link_table.name
        shape["link_parent_fk"] = next(
            (column.key for column in link_table.columns
             if any(fk.references(parent_table) for fk in column.foreign_keys)),
            None,
        )
        shape["link_dest_fk"] = info.get("dest_fk") or next(
            (column.key for column in link_table.columns
             if column.foreign_keys and not any(
                 fk.references(parent_table) for fk in column.foreign_keys)),
            None,
        )
    return shape


def describe(model: type) -> dict:
    """Snapshot entry of one model; ``resolve_refs=False`` (no skeleton registry in a shell),
    bypasses the structure cache."""
    from .structure import structure_for_model

    relations = {}
    for rel_name, info in model.viur_relations().items():
        relations[rel_name] = _relation_shape(model, rel_name, info)
    return {
        "table": model._viur_kind(),
        "structure": structure_for_model(model, resolve_refs=False),
        "relations": relations,
    }


def _table_models() -> list[type]:
    from .base import Model

    def _walk(cls: type) -> t.Iterator[type]:
        for sub in cls.__subclasses__():
            if getattr(sub, "__table__", None) is not None:
                yield sub
            yield from _walk(sub)

    # first definition per table name
    seen: dict[str, type] = {}
    for model in _walk(Model):
        seen.setdefault(model._viur_kind(), model)
    return list(seen.values())


def snapshot() -> dict:
    """Describe every imported ``Model`` table (``migrations.import_models``)."""
    return {
        model._viur_kind(): describe(model)
        for model in sorted(_table_models(), key=lambda cls: cls._viur_kind())
    }


# --- storage ----------------------------------------------------------------

def snapshot_dir(script_location: str | pathlib.Path) -> pathlib.Path:
    """Snapshot directory of an Alembic script directory."""
    return pathlib.Path(script_location) / "structures"


def save(directory: str | pathlib.Path, revision: str, data: dict) -> pathlib.Path:
    path = pathlib.Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{revision}.json"
    target.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
    return target


def prune(directory: str | pathlib.Path, keep: t.Iterable[str]) -> list[str]:
    """Delete snapshots of revisions that no longer exist. Returns the removed ids."""
    path = pathlib.Path(directory)
    if not path.is_dir():
        return []
    keep = set(keep)
    removed = []
    for snapshot_file in sorted(path.glob("*.json")):
        if snapshot_file.stem not in keep:
            snapshot_file.unlink()
            removed.append(snapshot_file.stem)
    return removed


def load(directory: str | pathlib.Path, revision: str | None) -> dict:
    """One snapshot; ``{}`` for an unknown/missing revision (``diff`` then reports only additions)."""
    if not revision:
        return {}
    path = pathlib.Path(directory) / f"{revision}.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


# --- diff -------------------------------------------------------------------

def _bone_changed(old: dict, new: dict) -> bool:
    stripped_old = {k: v for k, v in old.items() if k not in IGNORED_BONE_KEYS}
    stripped_new = {k: v for k, v in new.items() if k not in IGNORED_BONE_KEYS}
    return stripped_old != stripped_new


def _numeric_precision(bone: dict) -> tuple:
    return bone.get("precision"), bone.get("decimal")


def diff(old: dict, new: dict) -> list[Transition]:
    """Bone-level transitions between two snapshots, by table then field.

    Kinds: ``field_added``/``field_removed``, ``multiple_collapsed``/``multiple_expanded``
    (link table ↔ FK column), ``languages_reduced``/``languages_expanded``, ``type_changed``
    (``detail["from"]``/``["to"]``), ``select_values_changed``, ``precision_changed``,
    ``using_field_added``/``using_field_removed`` (link model payload).
    """
    transitions: list[Transition] = []

    for table in sorted(new):
        current, previous = new[table], old.get(table)
        if previous is None:
            continue  # new table: plain CREATE TABLE
        old_structure = previous.get("structure", {})
        new_structure = current.get("structure", {})
        old_relations = previous.get("relations", {})
        new_relations = current.get("relations", {})

        for field in sorted(set(old_structure) | set(new_structure)):
            old_bone = old_structure.get(field)
            new_bone = new_structure.get(field)

            if old_bone is None:
                transitions.append(Transition(
                    "field_added", table, field, None, new_bone,
                    {"relation": field in new_relations},
                ))
                continue
            if new_bone is None:
                transitions.append(Transition("field_removed", table, field, old_bone, None))
                continue
            if not _bone_changed(old_bone, new_bone):
                # unchanged bone; its using payload may differ
                transitions.extend(_using_transitions(
                    table, field, old_relations.get(field), new_relations.get(field),
                    new_bone=new_bone,
                ))
                continue

            old_multiple = bool(old_bone.get("multiple"))
            new_multiple = bool(new_bone.get("multiple"))
            is_relation = field in old_relations or field in new_relations

            if is_relation and old_multiple != new_multiple:
                old_shape = old_relations.get(field, {})
                new_shape = new_relations.get(field, {})
                if old_multiple:
                    transitions.append(Transition(
                        "multiple_collapsed", table, field, old_bone, new_bone,
                        {
                            "link_table": old_shape.get("link_table"),
                            "link_parent_fk": old_shape.get("link_parent_fk"),
                            "link_dest_fk": old_shape.get("link_dest_fk"),
                            "target_column": new_shape.get("fk"),
                            "target_table": new_shape.get("target_table")
                            or old_shape.get("target_table"),
                        },
                    ))
                else:
                    transitions.append(Transition(
                        "multiple_expanded", table, field, old_bone, new_bone,
                        {
                            "link_table": new_shape.get("link_table"),
                            "link_parent_fk": new_shape.get("link_parent_fk"),
                            "link_dest_fk": new_shape.get("link_dest_fk"),
                            "source_column": old_shape.get("fk"),
                        },
                    ))
                continue

            old_langs, new_langs = old_bone.get("languages"), new_bone.get("languages")
            if bool(old_langs) != bool(new_langs):
                if old_langs:
                    transitions.append(Transition(
                        "languages_reduced", table, field, old_bone, new_bone,
                        {"languages": list(old_langs)},
                    ))
                else:
                    transitions.append(Transition(
                        "languages_expanded", table, field, old_bone, new_bone,
                        {"languages": list(new_langs)},
                    ))
                continue

            if old_bone.get("type") != new_bone.get("type"):
                transitions.append(Transition(
                    "type_changed", table, field, old_bone, new_bone,
                    {"from": old_bone.get("type"), "to": new_bone.get("type")},
                ))
                continue

            if new_bone.get("type", "").startswith("select"):
                old_values = set((old_bone.get("values") or {}))
                new_values = set((new_bone.get("values") or {}))
                if old_values != new_values:
                    transitions.append(Transition(
                        "select_values_changed", table, field, old_bone, new_bone,
                        {
                            "added": sorted(new_values - old_values),
                            "removed": sorted(old_values - new_values),
                        },
                    ))
                    continue

            if new_bone.get("type", "").startswith("numeric") \
                    and _numeric_precision(old_bone) != _numeric_precision(new_bone):
                transitions.append(Transition(
                    "precision_changed", table, field, old_bone, new_bone,
                    {"precision": new_bone.get("precision", 0)},
                ))
                continue

            transitions.extend(_using_transitions(
                table, field, old_relations.get(field), new_relations.get(field),
                new_bone=new_bone,
            ))

        transitions = _pair_renamed_relations(
            transitions, table, old_relations, new_relations,
        )

    return transitions


def _pair_renamed_relations(
    transitions: list[Transition], table: str,
    old_relations: dict, new_relations: dict,
) -> list[Transition]:
    """Pair a removed and an added relation onto the same target table with opposite
    ``multiple`` (``tags`` → ``tag``) into one collapse/expand transition; unambiguous pairs only."""
    removed = [
        item for item in transitions
        if item.kind == "field_removed" and item.table == table
        and item.field in old_relations
    ]
    added = [
        item for item in transitions
        if item.kind == "field_added" and item.table == table
        and item.field in new_relations
    ]
    if not removed or not added:
        return transitions

    paired: list[Transition] = []
    consumed: set = set()
    for gone in removed:
        old_shape = old_relations[gone.field]
        matches = [
            item for item in added
            if item.field not in consumed
            and new_relations[item.field].get("target_table")
            == old_shape.get("target_table")
            and bool(new_relations[item.field].get("multiple"))
            != bool(old_shape.get("multiple"))
        ]
        if len(matches) != 1:
            continue
        arrived = matches[0]
        new_shape = new_relations[arrived.field]
        consumed.add(arrived.field)
        consumed.add(gone.field)
        name = f"{gone.field} -> {arrived.field}"
        if old_shape.get("multiple"):
            paired.append(Transition(
                "multiple_collapsed", table, name, gone.old, arrived.new,
                {
                    "link_table": old_shape.get("link_table"),
                    "link_parent_fk": old_shape.get("link_parent_fk"),
                    "link_dest_fk": old_shape.get("link_dest_fk"),
                    "target_column": new_shape.get("fk"),
                    "target_table": new_shape.get("target_table"),
                },
            ))
        else:
            paired.append(Transition(
                "multiple_expanded", table, name, gone.old, arrived.new,
                {
                    "link_table": new_shape.get("link_table"),
                    "link_parent_fk": new_shape.get("link_parent_fk"),
                    "link_dest_fk": new_shape.get("link_dest_fk"),
                    "source_column": old_shape.get("fk"),
                },
            ))

    if not consumed:
        return transitions
    kept = [
        item for item in transitions
        if not (item.table == table and item.field in consumed
                and item.kind in ("field_added", "field_removed"))
    ]
    return kept + paired


def _using_transitions(
    table: str, field: str, old_shape: dict | None, new_shape: dict | None,
    *, new_bone: dict | None = None,
) -> list[Transition]:
    """``using`` payload columns added/removed on a link model; ``new`` carries the added field's bone."""
    if not old_shape or not new_shape:
        return []
    old_fields = set(old_shape.get("using_fields") or ())
    new_fields = set(new_shape.get("using_fields") or ())
    link_table = new_shape.get("link_table") or old_shape.get("link_table")
    using_bones = (new_bone or {}).get("using") or {}
    out = []
    for name in sorted(new_fields - old_fields):
        out.append(Transition(
            "using_field_added", table, field, None, using_bones.get(name),
            {"link_table": link_table, "using_field": name},
        ))
    for name in sorted(old_fields - new_fields):
        out.append(Transition(
            "using_field_removed", table, field, None, None,
            {"link_table": link_table, "using_field": name},
        ))
    return out
