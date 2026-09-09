"""The transition generator — correcting Alembic's DDL diff.

Autogenerate's own operations are wrong or incomplete for bone-level
changes; the generator replaces them with the matching
:mod:`viur.models.migrate` operation. Here it is driven directly with
hand-built ``UpgradeOps`` and :class:`~viur.models.schema.Transition`
objects, so each branch is exercised in isolation and the assertions say
exactly which operations survived.
"""
import pytest
import sqlalchemy as sa
from alembic.operations import ops as alembic_ops
from sqlmodel import Field, Relationship, SQLModel

from viur.models import Field, Model, migrate, migrations
from viur.models.schema import Transition


# --------------------------------------------------------------------------- #
# models — the generator reads column types/nullability from the metadata     #
# --------------------------------------------------------------------------- #

class GenTag(Model, table=True):
    __tablename__ = "gen_tag"
    name: str = Field(descr="Name", max_length=30)


class GenPostTag(SQLModel, table=True):
    __tablename__ = "gen_post_tag"
    post_id: int | None = Field(default=None, foreign_key="gen_post.id", primary_key=True)
    tag_id: int | None = Field(default=None, foreign_key="gen_tag.id", primary_key=True)
    weight: int = Field(default=0, descr="Gewicht")


class GenPost(Model, table=True):
    __tablename__ = "gen_post"
    title: str = Field(descr="Titel", max_length=100)
    body: str = Field(default="", required=False, max_length=200)
    score: float = Field(default=0.0)
    flag: bool = Field(default=False)
    optional: str | None = Field(default=None, max_length=10)
    tag_id: int | None = Field(default=None, foreign_key="gen_tag.id")
    tag: GenTag | None = Relationship()


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #

def _bone(**overrides):
    bone = {"type": "str", "multiple": False, "languages": None, "required": False,
            "emptyvalue": "", "readonly": False}
    bone.update(overrides)
    return bone


def _upgrade_ops(*table_ops):
    return alembic_ops.UpgradeOps(ops=list(table_ops))


def _modify(table, *column_ops):
    return alembic_ops.ModifyTableOps(table, ops=list(column_ops))


def _add_column(table, name, type_=sa.String(10), nullable=True):
    return alembic_ops.AddColumnOp(table, sa.Column(name, type_, nullable=nullable))


def _alter_column(table, name):
    return alembic_ops.AlterColumnOp(table, name, modify_type=sa.String(20))


def _generated(upgrade_ops, transitions):
    notes = migrations._generate_transition_ops(upgrade_ops, transitions)
    produced = [op for op in upgrade_ops.ops if isinstance(op, migrate._ViURMigrateOp)]
    return produced, notes


def _op_names(upgrade_ops):
    """Every remaining operation, flattened out of the batch groups."""
    names = []
    for op in upgrade_ops.ops:
        if isinstance(op, alembic_ops.ModifyTableOps):
            names.extend(type(inner).__name__ for inner in op.ops)
        else:
            names.append(type(op).__name__)
    return names


# --------------------------------------------------------------------------- #
# metadata lookups                                                            #
# --------------------------------------------------------------------------- #

def test_column_type_and_nullability_come_from_the_column():
    assert isinstance(migrations._column_type("gen_post", "score"), sa.Float)
    # NOT the bone's ``required`` flag: ``body`` is required=False for the
    # client but NOT NULL in the database
    assert migrations._column_nullable("gen_post", "body") is False
    assert migrations._column_nullable("gen_post", "optional") is True


@pytest.mark.parametrize(
    ("table", "column"),
    [("gen_post", "nope"), ("nope", "score")],
)
def test_metadata_lookups_degrade_for_unknown_names(table, column):
    assert migrations._column_type(table, column) is None
    assert migrations._column_nullable(table, column) is True


@pytest.mark.parametrize(
    ("bone", "expected"),
    [
        (None, None),
        ({"emptyvalue": ""}, ""),
        ({"emptyvalue": "", "defaultvalue": "x"}, "x"),
        ({"emptyvalue": 0, "defaultvalue": None}, 0),   # None default → emptyvalue
    ],
)
def test_default_fill_value(bone, expected):
    assert migrations._default_fill_value(bone) == expected


@pytest.mark.parametrize(
    ("revision", "expected"),
    [((), None), (("abc",), "abc"), (("a", "b"), "a"), ("xyz", "xyz"), (None, None)],
)
def test_parent_revision(revision, expected):
    assert migrations._parent_revision(revision) == expected


# --------------------------------------------------------------------------- #
# op-list surgery                                                             #
# --------------------------------------------------------------------------- #

def test_empty_batch_groups_are_removed():
    upgrade_ops = _upgrade_ops(_modify("gen_post", _add_column("gen_post", "x")))
    migrations._drop_column_ops(
        upgrade_ops, "gen_post", {"x"}, (alembic_ops.AddColumnOp,))
    assert upgrade_ops.ops == []


def test_drop_column_ops_leaves_other_tables_alone():
    upgrade_ops = _upgrade_ops(
        _modify("gen_post", _add_column("gen_post", "x")),
        _modify("gen_tag", _add_column("gen_tag", "x")),
    )
    migrations._drop_column_ops(
        upgrade_ops, "gen_post", {"x"}, (alembic_ops.AddColumnOp,))
    assert _op_names(upgrade_ops) == ["AddColumnOp"]


def test_drop_fk_ops_removes_only_the_matching_constraint():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post",
        alembic_ops.CreateForeignKeyOp(None, "gen_post", "gen_tag", ["tag_id"], ["id"]),
        alembic_ops.CreateForeignKeyOp(None, "gen_post", "gen_tag", ["other"], ["id"]),
    ))
    migrations._drop_fk_ops(upgrade_ops, "gen_post", {"tag_id"})
    remaining = upgrade_ops.ops[0].ops
    assert len(remaining) == 1 and remaining[0].local_cols == ["other"]


def test_drop_table_op():
    upgrade_ops = _upgrade_ops(
        alembic_ops.DropTableOp("gen_post_tag"),
        alembic_ops.DropTableOp("other"),
    )
    migrations._drop_table_op(upgrade_ops, "gen_post_tag")
    assert [op.table_name for op in upgrade_ops.ops] == ["other"]


def test_make_nullable_relaxes_a_not_null_column():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post", _add_column("gen_post", "slug", nullable=False)))
    assert migrations._make_nullable(upgrade_ops, "gen_post", "slug") is True
    assert upgrade_ops.ops[0].ops[0].column.nullable is True


def test_make_nullable_reports_when_there_is_nothing_to_relax():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post", _add_column("gen_post", "slug", nullable=True)))
    assert migrations._make_nullable(upgrade_ops, "gen_post", "slug") is False


# --------------------------------------------------------------------------- #
# transitions → operations                                                    #
# --------------------------------------------------------------------------- #

COLLAPSE_DETAIL = {
    "link_table": "gen_post_tag", "link_parent_fk": "post_id",
    "link_dest_fk": "tag_id", "target_column": "tag_id", "target_table": "gen_tag",
}


def test_collapse_replaces_the_drop_table_and_the_unnamed_fk():
    """Alembic would drop the link table FIRST (losing every row) and create
    an unnamed FK (which SQLite's batch mode rejects)."""
    upgrade_ops = _upgrade_ops(
        alembic_ops.DropTableOp("gen_post_tag"),
        _modify(
            "gen_post",
            _add_column("gen_post", "tag_id", sa.Integer()),
            alembic_ops.CreateForeignKeyOp(
                None, "gen_post", "gen_tag", ["tag_id"], ["id"]),
        ),
    )
    produced, notes = _generated(upgrade_ops, [Transition(
        "multiple_collapsed", "gen_post", "tags -> tag", detail=COLLAPSE_DETAIL)])

    assert [type(op).__name__ for op in produced] == ["CollapseMultipleOp"]
    assert produced[0].kwargs["keep"] == "first"      # the bone's rule
    assert produced[0].kwargs["drop_link_table"] is True
    # the FK column's type comes from the model, not from a hardcoded Integer —
    # GenPost.tag_id is an int here, a string PK model would yield a string type
    assert isinstance(produced[0].kwargs["target_type"], sa.Integer)
    assert "DropTableOp" not in _op_names(upgrade_ops)
    assert "AddColumnOp" not in _op_names(upgrade_ops)
    assert "CreateForeignKeyOp" not in _op_names(upgrade_ops)
    assert "keeps the first target" in notes[0]


def test_expansion_keeps_the_create_table_and_absorbs_the_column_drop():
    upgrade_ops = _upgrade_ops(
        alembic_ops.CreateTableOp("gen_post_tag", []),
        _modify("gen_post", alembic_ops.DropColumnOp("gen_post", "tag_id")),
    )
    produced, notes = _generated(upgrade_ops, [Transition(
        "multiple_expanded", "gen_post", "tag -> tags",
        detail={
            "link_table": "gen_post_tag", "link_parent_fk": "post_id",
            "link_dest_fk": "tag_id", "source_column": "tag_id",
        },
    )])

    assert [type(op).__name__ for op in produced] == ["ExpandMultipleOp"]
    assert "CreateTableOp" in _op_names(upgrade_ops)   # the link table must be created
    assert "DropColumnOp" not in _op_names(upgrade_ops)
    assert "one link row per value" in notes[0]


EXPAND_DETAIL = {
    "link_table": "gen_post_tag", "link_parent_fk": "post_id",
    "link_dest_fk": "tag_id", "source_column": "tag_id",
}


def test_expansion_fills_not_null_payload_from_the_model_default():
    """GenPostTag.weight is NOT NULL with default 0 — the using-bone carries
    that default, so the generated operation fills it; nullable payload is
    left alone."""
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "multiple_expanded", "gen_post", "tag -> tags",
        new=_bone(type="relational.gen_tag", multiple=True, using={
            "weight": _bone(type="numeric", emptyvalue=0, defaultvalue=0),
            "note": _bone(defaultvalue="n/a"),   # nullable -> left NULL, default or not
        }),
        detail=EXPAND_DETAIL,
    )])
    assert produced[0].kwargs["payload_defaults"] == {"weight": 0}   # no "note"
    assert any("filled from the model's defaults: {'weight': 0}" in n for n in notes)


def test_expansion_stubs_a_not_null_payload_without_a_default():
    """Nothing to derive -> an Ellipsis stub plus a WARNING, never a guess."""
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "multiple_expanded", "gen_post", "tag -> tags",
        new=_bone(type="relational.gen_tag", multiple=True, using={
            "weight": _bone(type="date", emptyvalue=None),   # no defaultvalue
        }),
        detail=EXPAND_DETAIL,
    )])
    assert produced[0].kwargs["payload_defaults"] == {"weight": ...}
    assert any(n.startswith("WARNING gen_post_tag") and "'weight'" in n for n in notes)


@pytest.mark.parametrize("kind", ["multiple_collapsed", "multiple_expanded"])
def test_incomplete_relation_details_are_skipped(kind):
    """Without the link table and column names there is nothing to generate —
    better the plain (lossy but honest) DDL than a broken operation."""
    produced, notes = _generated(_upgrade_ops(), [Transition(
        kind, "gen_post", "tags", detail={"link_table": None})])
    assert produced == [] and notes == []


def test_language_reduction_replaces_the_alter_column():
    upgrade_ops = _upgrade_ops(_modify("gen_post", _alter_column("gen_post", "body")))
    produced, notes = _generated(upgrade_ops, [Transition(
        "languages_reduced", "gen_post", "body", new=_bone(),
        detail={"languages": ["de", "en"]})])

    assert produced[0].kwargs["keep"] == "de"        # the first declared language
    assert produced[0].kwargs["nullable"] is False  # from the COLUMN, not the bone
    assert "AlterColumnOp" not in _op_names(upgrade_ops)
    assert "keeps de" in notes[0]


def test_language_reduction_without_languages_has_no_keep():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "languages_reduced", "gen_post", "body", new=_bone(), detail={"languages": []})])
    assert produced[0].kwargs["keep"] is None
    assert "keeps ?" in notes[0]


def test_language_expansion():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "languages_expanded", "gen_post", "body", new=_bone(),
        detail={"languages": ["de", "en"]})])
    assert produced[0].kwargs["languages"] == ["de", "en"]
    assert "like languages[0]" in notes[0]


def test_type_change_to_bool_uses_the_parse_bool_rule():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "type_changed", "gen_post", "flag", new=_bone(type="bool"),
        detail={"from": "select", "to": "bool"})])
    assert produced[0].kwargs["truthy"] == list(migrate.BOOLEAN_TRUTHY)
    assert "parse.bool rules" in notes[0]


def test_type_change_from_bool_requires_a_mapping():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "type_changed", "gen_post", "flag",
        new=_bone(type="select", values={"yes": "Yes", "no": "No"}),
        detail={"from": "bool", "to": "select"})])

    assert produced[0].kwargs["mapping"] == {True: ..., False: ...}
    assert "MAPPING REQUIRED" in notes[0]
    # gen_post.flag is a plain bool column — no enum labels to offer,
    # so the note points at the model (the enum-column case is covered by
    # test_bool_to_select_stub_offers_the_stored_labels)
    assert "see the model" in notes[0]


def test_type_change_from_bool_without_declared_values():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "type_changed", "gen_post", "flag", new=_bone(type="select"),
        detail={"from": "bool", "to": "select"})])
    assert produced[0].kwargs["mapping"] == {True: ..., False: ...}
    assert "see the model" in notes[0]
    assert "STORED labels" in notes[0]   # the stub asks for column labels


def test_type_change_between_string_flavours():
    """str → text: the transition Alembic reports nothing for."""
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "type_changed", "gen_post", "body", new=_bone(type="text"),
        detail={"from": "str", "to": "text"})])
    assert [type(op).__name__ for op in produced] == ["CoerceTextOp"]
    assert notes[0].endswith("str -> text")


@pytest.mark.parametrize(
    ("precision", "expected_note"),
    [(2, "(rounds)"), (0, "(truncates, like the bone)")],
)
def test_precision_change(precision, expected_note):
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "precision_changed", "gen_post", "score", new=_bone(type="numeric"),
        detail={"precision": precision})])
    assert produced[0].kwargs["precision"] == precision
    assert expected_note in notes[0]


def test_new_required_field_is_added_nullable_then_filled():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post", _add_column("gen_post", "slug", nullable=False)))
    produced, notes = _generated(upgrade_ops, [Transition(
        "field_added", "gen_post", "slug",
        new=_bone(emptyvalue="", defaultvalue=""), detail={"relation": False})])

    assert [type(op).__name__ for op in produced] == ["FillColumnOp"]
    assert produced[0].kwargs == {"column": "slug", "value": "", "nullable": False}
    assert upgrade_ops.ops[0].ops[0].column.nullable is True
    assert "from the model's default" in notes[0]


def test_new_optional_field_needs_no_operation():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post", _add_column("gen_post", "opt", nullable=True)))
    produced, notes = _generated(upgrade_ops, [Transition(
        "field_added", "gen_post", "opt", new=_bone(), detail={"relation": False})])
    assert produced == [] and notes == []
    assert "AddColumnOp" in _op_names(upgrade_ops)


def test_new_relation_field_is_not_filled():
    """A relation has no plain column to put a default into."""
    produced, _ = _generated(_upgrade_ops(), [Transition(
        "field_added", "gen_post", "tag", new=_bone(), detail={"relation": True})])
    assert produced == []


def test_new_using_field_is_filled_on_the_link_table():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post_tag", _add_column("gen_post_tag", "weight", sa.Integer(),
                                    nullable=False)))
    produced, notes = _generated(upgrade_ops, [Transition(
        "using_field_added", "gen_post", "tags", new=_bone(emptyvalue=0),
        detail={"link_table": "gen_post_tag", "using_field": "weight"})])

    assert produced[0].table == "gen_post_tag"
    assert produced[0].kwargs["column"] == "weight"
    assert "gen_post_tag.weight" in notes[0]


def test_field_removed_needs_no_operation():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "field_removed", "gen_post", "gone", old=_bone())])
    assert produced == [] and notes == []


# --------------------------------------------------------------------------- #
# the hook itself                                                             #
# --------------------------------------------------------------------------- #

class _Config:
    def __init__(self, location=None):
        self._location = location

    def get_main_option(self, key, default=None):
        return self._location if key == "script_location" else default


class _Context:
    def __init__(self, location=None):
        self.config = _Config(location)


class _Script:
    def __init__(self, upgrade_ops, rev_id="rev1"):
        self.upgrade_ops = upgrade_ops
        self.rev_id = rev_id


def test_hook_does_nothing_without_directives():
    assert migrations.process_revision_directives(_Context(), (), []) is None


def test_hook_does_nothing_without_upgrade_ops():
    assert migrations.process_revision_directives(
        _Context(), (), [_Script(None)]) is None


def test_hook_does_nothing_without_a_script_location():
    assert migrations.process_revision_directives(
        _Context(None), (), [_Script(_upgrade_ops())]) is None


def test_hook_writes_the_snapshot_for_a_first_revision(tmp_path):
    """No parent revision → no previous snapshot → only the new one is
    written and nothing is falsely detected as a transition."""
    script = _Script(_upgrade_ops())

    migrations.process_revision_directives(_Context(str(tmp_path)), (), [script])

    written = tmp_path / "structures" / "rev1.json"
    assert written.is_file()
    assert "gen_post" in written.read_text()
    assert script.upgrade_ops.ops == []


def test_hook_diffs_against_the_parent_snapshot_and_rewrites(tmp_path, capsys):
    from viur.models import schema

    directory = schema.snapshot_dir(str(tmp_path))
    # a parent snapshot in which gen_post has no ``slug`` yet
    parent = schema.snapshot()
    del parent["gen_post"]["structure"]["title"]
    schema.save(directory, "parent", parent)

    script = _Script(
        _upgrade_ops(_modify(
            "gen_post", _add_column("gen_post", "title", nullable=False))),
        rev_id="child",
    )
    migrations.process_revision_directives(
        _Context(str(tmp_path)), ("parent",), [script])

    produced = [
        op for op in script.upgrade_ops.ops if isinstance(op, migrate._ViURMigrateOp)
    ]
    assert [type(op).__name__ for op in produced] == ["FillColumnOp"]
    assert "viur-models:" in capsys.readouterr().out
    assert (tmp_path / "structures" / "child.json").is_file()


# --------------------------------------------------------------------------- #
# dry runs and snapshot housekeeping                                          #
# --------------------------------------------------------------------------- #

class _RevisionContext:
    def __init__(self, **command_args):
        self.command_args = command_args


class _ContextWithOpts(_Context):
    def __init__(self, location=None, **command_args):
        super().__init__(location)
        self.opts = {"revision_context": _RevisionContext(**command_args)}


@pytest.mark.parametrize(
    ("command_args", "expected"),
    [
        ({"head": "head", "message": None}, True),    # alembic check
        ({"head": None, "message": "add slug"}, False),   # alembic revision -m
        ({"head": None, "message": None}, False),     # alembic revision, no -m
        ({}, False),
    ],
)
def test_is_dry_run(command_args, expected):
    assert migrations._is_dry_run(_ContextWithOpts(**command_args)) is expected


def test_is_dry_run_without_opts():
    assert migrations._is_dry_run(_Context()) is False


def test_check_run_rewrites_ops_but_writes_no_snapshot(tmp_path):
    """`alembic check` must judge the CORRECTED diff (so the rewriting runs)
    without leaving an orphan snapshot behind in CI."""
    from viur.models import schema

    directory = schema.snapshot_dir(str(tmp_path))
    parent = schema.snapshot()
    del parent["gen_post"]["structure"]["title"]
    schema.save(directory, "parent", parent)

    script = _Script(
        _upgrade_ops(_modify(
            "gen_post", _add_column("gen_post", "title", nullable=False))),
        rev_id="throwaway",
    )
    migrations.process_revision_directives(
        _ContextWithOpts(str(tmp_path), head="head", message=None),
        ("parent",), [script],
    )

    produced = [
        op for op in script.upgrade_ops.ops if isinstance(op, migrate._ViURMigrateOp)
    ]
    assert [type(op).__name__ for op in produced] == ["FillColumnOp"]   # rewritten
    assert not (directory / "throwaway.json").exists()                  # not written
    assert (directory / "parent.json").exists()                         # untouched


def test_known_revisions_of_an_unreadable_script_directory(tmp_path):
    assert migrations._known_revisions(_Config(str(tmp_path))) == set()


def test_known_revisions_reads_the_script_directory(tmp_path):
    from alembic import command
    from alembic.config import Config

    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    (scripts / "env.py").write_text("")
    (scripts / "script.py.mako").write_text(
        'revision = ${repr(up_revision)}\n'
        'down_revision = ${repr(down_revision)}\n'
        'def upgrade(): pass\n'
        'def downgrade(): pass\n',
    )
    config = Config()
    config.set_main_option("script_location", str(scripts))
    command.revision(config, message="one", rev_id="aaa111")

    assert migrations._known_revisions(config) == {"aaa111"}


# --------------------------------------------------------------------------- #
# constraint naming and UNIQUE awareness                                      #
# --------------------------------------------------------------------------- #

class GenUnique(Model, table=True):
    __tablename__ = "gen_unique"
    email: str = Field(default="", required=False, max_length=60, unique=True)
    plain: str = Field(default="", required=False, max_length=10)


class GenTableUnique(Model, table=True):
    """UNIQUE declared as a table-level constraint rather than on the column."""

    __tablename__ = "gen_table_unique"
    __table_args__ = (sa.UniqueConstraint("code"),)
    code: str = Field(default="", required=False, max_length=10)


def test_unnamed_constraints_get_a_name():
    """SQLite's batch mode refuses an unnamed constraint outright, so an
    unnamed one makes the whole revision unapplicable."""
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post",
        alembic_ops.CreateUniqueConstraintOp(None, "gen_post", ["body", "score"]),
        alembic_ops.CreateForeignKeyOp(None, "gen_post", "gen_tag", ["tag_id"], ["id"]),
    ))

    assigned = migrations._name_constraints(upgrade_ops)

    assert assigned == ["uq_gen_post_body_score", "fk_gen_post_tag_id_gen_tag"]
    assert [op.constraint_name for op in upgrade_ops.ops[0].ops] == assigned


def test_named_constraints_are_left_alone():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_post",
        alembic_ops.CreateUniqueConstraintOp("my_name", "gen_post", ["body"]),
    ))
    assert migrations._name_constraints(upgrade_ops) == []
    assert upgrade_ops.ops[0].ops[0].constraint_name == "my_name"


def test_name_constraints_also_visits_top_level_ops():
    upgrade_ops = _upgrade_ops(
        alembic_ops.CreateUniqueConstraintOp(None, "gen_post", ["body"]),
    )
    assert migrations._name_constraints(upgrade_ops) == ["uq_gen_post_body"]


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ("gen_unique", "email", True),          # unique=True on the column
        ("gen_unique", "plain", False),
        ("gen_table_unique", "code", True),     # table-level UniqueConstraint
        ("gen_post", "nope", False),
        ("nope", "email", False),
    ],
)
def test_column_unique(table, column, expected):
    assert migrations._column_unique(table, column) is expected


def test_filling_a_unique_column_warns_instead_of_failing_at_commit():
    """One value for every row cannot satisfy UNIQUE — nothing can invent
    distinct values, so the generator says so before the revision runs."""
    upgrade_ops = _upgrade_ops(_modify(
        "gen_unique", _add_column("gen_unique", "email", nullable=False)))

    produced, notes = _generated(upgrade_ops, [Transition(
        "field_added", "gen_unique", "email",
        new=_bone(emptyvalue="", defaultvalue=""), detail={"relation": False})])

    assert [type(op).__name__ for op in produced] == ["FillColumnOp"]
    assert any("is UNIQUE" in note for note in notes)


def test_filling_a_plain_column_does_not_warn():
    upgrade_ops = _upgrade_ops(_modify(
        "gen_unique", _add_column("gen_unique", "plain", nullable=False)))
    _, notes = _generated(upgrade_ops, [Transition(
        "field_added", "gen_unique", "plain",
        new=_bone(emptyvalue=""), detail={"relation": False})])
    assert not any("UNIQUE" in note for note in notes)


def test_hook_names_constraints_and_reports_it(tmp_path, capsys):
    """The naming runs for every autogenerate call, independent of whether
    any bone-level transition was detected."""
    script = _Script(_upgrade_ops(_modify(
        "gen_post",
        alembic_ops.CreateUniqueConstraintOp(None, "gen_post", ["body"]),
    )))

    migrations.process_revision_directives(_Context(str(tmp_path)), (), [script])

    assert upgrade_constraint_name(script) == "uq_gen_post_body"
    assert "named the constraint uq_gen_post_body" in capsys.readouterr().out


def upgrade_constraint_name(script):
    return script.upgrade_ops.ops[0].ops[0].constraint_name


def test_select_values_change_generates_the_enum_sync():
    """A new select option must reach the database type — on Postgres via
    ALTER TYPE, on SQLite as a no-op. Without it the revision is empty and
    the app fails at runtime with 'invalid input value for enum'."""
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "select_values_changed", "gen_post", "flag",
        new=_bone(type="select", values={"yes": "Yes", "no": "No", "maybe": "Maybe"}),
        detail={"added": ["maybe"], "removed": []})])

    # gen_post.flag is a bool column (no enum type), so nothing is produced —
    # the guard requires a real Enum column type
    assert produced == []


class GenEnumHost(Model, table=True):
    __tablename__ = "gen_enum_host"
    kind: __import__("enum").Enum("GenKind", {"YES": "yes", "NO": "no"}) = \
        Field(descr="Art")


def test_select_values_change_on_a_real_enum_column():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "select_values_changed", "gen_enum_host", "kind",
        new=_bone(type="select", values={"yes": "Yes", "no": "No", "maybe": "M"}),
        detail={"added": ["maybe"], "removed": []})])

    assert [type(op).__name__ for op in produced] == ["AddEnumValuesOp"]
    assert produced[0].table == "genkind"                      # the TYPE name
    assert produced[0].kwargs["labels"] == ["YES", "NO"]       # the column's labels
    assert "ALTER TYPE on Postgres, no-op on SQLite" in notes[0]


def test_removed_select_option_warns_instead_of_guessing():
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "select_values_changed", "gen_enum_host", "kind",
        new=_bone(type="select", values={"yes": "Yes"}),
        detail={"added": [], "removed": ["no"]})])
    assert any("cannot" in note and "removed" in note for note in notes)


def test_bool_to_select_stub_offers_the_stored_labels():
    """The stored labels are the enum member NAMES, not the wire values —
    filling in the wire value writes data the ORM cannot read back."""
    produced, notes = _generated(_upgrade_ops(), [Transition(
        "type_changed", "gen_enum_host", "kind",
        new=_bone(type="select", values={"yes": "Yes", "no": "No"}),
        detail={"from": "bool", "to": "select"})])

    assert "STORED labels" in notes[0]
    assert "['YES', 'NO']" in notes[0]      # names, not {'no','yes'}
