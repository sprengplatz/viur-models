"""Data-migration operations — the bone rules and the Alembic operations.

The rule functions are pinned against the viur-core behavior they mirror
(the docstrings in :mod:`viur.models.migrate` name the sources); the
operations run against real SQLite through a real Alembic ``Operations``
context, because their whole job is DDL plus data.
"""
import datetime
import json

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from viur.models import migrate


# --------------------------------------------------------------------------- #
# the bone rules                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("values", "keep", "expected"),
    [
        ([3, 1, 2], "first", 3),   # BaseBone.unserialize: loadVal[0]
        ([3, 1, 2], "last", 2),
        ([7], "first", 7),
        ([], "first", None),
    ],
)
def test_pick_from_multiple(values, keep, expected):
    assert migrate.pick_from_multiple(values, keep) == expected


@pytest.mark.parametrize(
    ("value", "keep", "expected"),
    [
        ({"de": "Hallo", "en": "Hi"}, "de", "Hallo"),      # preferred language
        ({"de": "Hallo", "en": "Hi"}, "fr", "Hallo"),      # absent → first value
        # core checks KEY PRESENCE, so a present-but-empty language wins
        ({"de": None, "en": "Hi"}, "de", None),
        # only when the key is absent does the fallback skip Nones
        ({"de": None, "en": "Hi"}, None, "Hi"),
        ({"_viurLanguageWrapper_": True, "de": "H"}, None, "H"),  # marker ignored
        ({"de": ["a", "b"]}, "de", "a"),                   # multiple+languages → first
        ('{"de": "Hallo"}', "de", "Hallo"),                # JSON text from the column
        ("plain string", "de", "plain string"),            # not a language dict
        (None, "de", None),
        ({}, "de", None),
    ],
)
def test_pick_language(value, keep, expected):
    assert migrate.pick_language(value, ("de", "en"), keep) == expected


@pytest.mark.parametrize(
    ("value", "languages", "expected"),
    [
        # going UP, viur-core uses languages[0] — NOT the default language
        ("Hallo", ("de", "en"), {"de": "Hallo", "en": None}),
        ("Hi", ("en", "de"), {"en": "Hi", "de": None}),
        (None, ("de", "en"), {"de": None, "en": None}),
        ("", ("de",), {"de": None}),
        ("x", (), {}),
    ],
)
def test_wrap_language(value, languages, expected):
    assert migrate.wrap_language(value, languages) == expected


@pytest.mark.parametrize(
    ("value", "precision", "expected"),
    [
        (3.7, 0, 3),          # NumericBone: int(float(v)) TRUNCATES
        (-3.7, 0, -3),        # …toward zero, not floor
        (3.14159, 2, 3.14),   # precision > 0 rounds
        ("7,5", 1, 7.5),      # comma decimal, like the bone
        ("12", 0, 12),
        (None, 2, None),
        ("nonsense", 2, None),
        ([], 2, None),
    ],
)
def test_coerce_number(value, precision, expected):
    assert migrate.coerce_number(value, precision) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("text", "text"),
        (True, "True"),
        (42, "42"),
        (1.5, "1.5"),
        (datetime.date(2026, 9, 2), "2026-09-02"),
        (datetime.time(8, 30), "08:30:00"),
        (datetime.datetime(2026, 9, 2, 8, 30), "2026-09-02T08:30:00"),
        (0, "0"),
        ([], ""),
        ({"a": 1}, "{'a': 1}"),
    ],
)
def test_coerce_text(value, expected):
    assert migrate.coerce_text(value) == expected


def test_coerce_text_never_truncates():
    """The bones only validate max_length on fromClient, never on read."""
    long = "x" * 500
    assert migrate.coerce_text(long) == long


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("yes", True), ("true", True), ("1", True), (" TRUE ", True),
        (True, True), (1, True),
        ("no", False), ("active", False), (False, False), (0, False),
        (None, False), ("", False),
    ],
)
def test_coerce_bool(value, expected):
    assert migrate.coerce_bool(value) is expected


def test_coerce_bool_honours_custom_truthy():
    assert migrate.coerce_bool("ja", ("ja",)) is True
    assert migrate.coerce_bool("yes", ("ja",)) is False


# --------------------------------------------------------------------------- #
# operation fixtures                                                          #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def db(tmp_path):
    """A file-backed SQLite database plus an Alembic ``Operations`` context.

    File-backed, not ``sqlite://``: batch operations reconnect, and an
    in-memory database is per-connection.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'op.sqlite3'}")
    connection = engine.connect()
    operations = Operations(MigrationContext.configure(connection))
    yield operations, connection
    connection.close()
    engine.dispose()


def _create(connection, ddl, rows=()):
    connection.execute(sa.text(ddl))
    for statement, params in rows:
        connection.execute(sa.text(statement), params)


def _fetch(connection, query):
    return connection.execute(sa.text(query)).all()


def _column(connection, table, name):
    return next(
        row for row in connection.execute(sa.text(f'PRAGMA table_info("{table}")'))
        if row[1] == name
    )


# --------------------------------------------------------------------------- #
# column transformations                                                      #
# --------------------------------------------------------------------------- #

def test_reduce_languages_keeps_one_language(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE post (id INTEGER PRIMARY KEY, title JSON)', [
        ('INSERT INTO post VALUES (1, :a)', {"a": json.dumps({"de": "Hallo", "en": "Hi"})}),
        # "de" present but empty: core's key-presence rule keeps it empty
        ('INSERT INTO post VALUES (2, :a)', {"a": json.dumps({"de": None, "en": "Only"})}),
    ])

    operations.reduce_languages(
        "post", "title", new_type=sa.String(200), languages=["de", "en"], keep="de",
    )

    assert _fetch(connection, "SELECT id, title FROM post ORDER BY id") == [
        (1, "Hallo"), (2, None),
    ]
    assert "VARCHAR(200)" in _column(connection, "post", "title")[2]


def test_expand_languages_puts_the_value_under_the_first_language(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE post (id INTEGER PRIMARY KEY, title VARCHAR(50))', [
        ('INSERT INTO post VALUES (1, :a)', {"a": "Hallo"}),
        ('INSERT INTO post VALUES (2, NULL)', {}),
    ])

    operations.expand_languages("post", "title", languages=["de", "en"])

    rows = _fetch(connection, "SELECT id, title FROM post ORDER BY id")
    assert json.loads(rows[0][1]) == {"de": "Hallo", "en": None}
    assert json.loads(rows[1][1]) == {"de": None, "en": None}


def test_coerce_numeric_rounds_or_truncates(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, v FLOAT)', [
        ('INSERT INTO m VALUES (1, 3.7)', {}),
        ('INSERT INTO m VALUES (2, -3.7)', {}),
    ])

    operations.coerce_numeric("m", "v", new_type=sa.Integer(), precision=0)

    assert _fetch(connection, "SELECT id, v FROM m ORDER BY id") == [(1, 3), (2, -3)]


def test_coerce_text_widens_a_string_column(db):
    """The transition Alembic cannot see: VARCHAR(n) → unbounded VARCHAR."""
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, body VARCHAR(20))', [
        ('INSERT INTO m VALUES (1, :a)', {"a": "kurz"}),
    ])

    operations.coerce_text("m", "body", new_type=sa.Text())

    assert _fetch(connection, "SELECT body FROM m") == [("kurz",)]
    assert _column(connection, "m", "body")[2] == "TEXT"


def test_coerce_bool_from_select_values(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, flag VARCHAR(10))', [
        ('INSERT INTO m VALUES (1, :a)', {"a": "yes"}),
        ('INSERT INTO m VALUES (2, :a)', {"a": "no"}),
        ('INSERT INTO m VALUES (3, :a)', {"a": "active"}),   # not truthy per parse.bool
    ])

    operations.coerce_bool("m", "flag")

    assert _fetch(connection, "SELECT id, flag FROM m ORDER BY id") == [
        (1, 1), (2, 0), (3, 0),
    ]


def test_transform_column_can_make_the_column_not_null(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, v VARCHAR(10))', [
        ('INSERT INTO m VALUES (1, :a)', {"a": "x"}),
    ])

    written = migrate.transform_column(
        operations, "m", "v", new_type=sa.String(20),
        transform=lambda value: (value or "") + "!", nullable=False,
    )

    assert written == 1
    assert _fetch(connection, "SELECT v FROM m") == [("x!",)]
    assert _column(connection, "m", "v")[3] == 1  # notnull


def test_transform_column_chunks(db, monkeypatch):
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, v INTEGER)')
    for index in range(5):
        connection.execute(sa.text("INSERT INTO m VALUES (:i, :i)"), {"i": index})
    monkeypatch.setattr(migrate, "CHUNK_SIZE", 2)

    written = migrate.transform_column(
        operations, "m", "v", new_type=sa.Integer(), transform=lambda v: (v or 0) * 10,
    )

    assert written == 5
    assert _fetch(connection, "SELECT v FROM m ORDER BY id") == [
        (0,), (10,), (20,), (30,), (40,)]


# --------------------------------------------------------------------------- #
# remap_values                                                                #
# --------------------------------------------------------------------------- #

def test_remap_values_maps_booleans_to_select_values(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, flag BOOLEAN)', [
        ('INSERT INTO m VALUES (1, 1)', {}),
        ('INSERT INTO m VALUES (2, 0)', {}),
        ('INSERT INTO m VALUES (3, NULL)', {}),
    ])

    operations.remap_values(
        "m", "flag", {True: "yes", False: "no", None: "unknown"},
        new_type=sa.String(10),
    )

    assert _fetch(connection, "SELECT id, flag FROM m ORDER BY id") == [
        (1, "yes"), (2, "no"), (3, "unknown"),
    ]


def test_remap_values_falls_back_to_none_entry(db):
    """A value the mapping does not mention takes the ``None`` entry — for an
    integer (via its truthiness) as well as for anything else."""
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, flag VARCHAR(10))', [
        ('INSERT INTO m VALUES (1, 7)', {}),           # int, bool(7) unmapped
        ('INSERT INTO m VALUES (2, :a)', {"a": "odd"}),  # not an int at all
    ])

    operations.remap_values("m", "flag", {None: "fallback"}, new_type=sa.String(10))

    assert _fetch(connection, "SELECT id, flag FROM m ORDER BY id") == [
        (1, "fallback"), (2, "fallback"),
    ]


def test_remap_values_refuses_an_unfilled_stub(db):
    """The generated stub must not run — viur-core has no rule here, so a
    silent guess would be the worst outcome."""
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, flag BOOLEAN)')

    with pytest.raises(RuntimeError, match="no target value given for"):
        operations.remap_values(
            "m", "flag", {True: ..., False: ...}, new_type=sa.String(10),
        )


# --------------------------------------------------------------------------- #
# fill_column                                                                 #
# --------------------------------------------------------------------------- #

def test_fill_column_fills_nulls_and_constrains(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, slug VARCHAR(10))', [
        ('INSERT INTO m VALUES (1, NULL)', {}),
        ('INSERT INTO m VALUES (2, :a)', {"a": "kept"}),
    ])

    operations.fill_column("m", "slug", "", nullable=False)

    assert _fetch(connection, "SELECT id, slug FROM m ORDER BY id") == [
        (1, ""), (2, "kept"),
    ]
    assert _column(connection, "m", "slug")[3] == 1  # notnull


def test_fill_column_can_leave_the_column_nullable(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, v JSON)', [
        ('INSERT INTO m VALUES (1, NULL)', {}),
    ])

    operations.fill_column("m", "v", {"de": None}, nullable=True)

    assert json.loads(_fetch(connection, "SELECT v FROM m")[0][0]) == {"de": None}
    assert _column(connection, "m", "v")[3] == 0


# --------------------------------------------------------------------------- #
# relation reshaping                                                          #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def relation_db(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE tag (id INTEGER PRIMARY KEY, name VARCHAR(20))')
    _create(connection, 'CREATE TABLE post (id INTEGER PRIMARY KEY, name VARCHAR(20))')
    _create(
        connection,
        'CREATE TABLE post_tag (post_id INTEGER, tag_id INTEGER, '
        'PRIMARY KEY (post_id, tag_id))',
    )
    for index, name in enumerate(("python", "sql", "viur"), start=1):
        connection.execute(sa.text("INSERT INTO tag VALUES (:i, :n)"), {"i": index, "n": name})
    connection.execute(sa.text("INSERT INTO post VALUES (1, 'a'), (2, 'b')"))
    connection.execute(sa.text(
        "INSERT INTO post_tag VALUES (1, 1), (1, 2), (1, 3), (2, 2)"))
    return operations, connection


def test_collapse_multiple_keeps_the_first_target(relation_db, capsys):
    operations, connection = relation_db

    operations.collapse_multiple(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
        foreign_table="tag", foreign_column="id",
    )

    assert _fetch(connection, "SELECT id, tag_id FROM post ORDER BY id") == [
        (1, 1), (2, 2),
    ]
    assert not _fetch(
        connection, "SELECT name FROM sqlite_master WHERE name='post_tag'")
    # the loss is reported, not silent
    assert "1 row(s) had several targets" in capsys.readouterr().out


def test_collapse_multiple_can_keep_the_link_table(relation_db):
    operations, connection = relation_db

    operations.collapse_multiple(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
        foreign_table="tag", keep="last", drop_link_table=False,
    )

    assert _fetch(connection, "SELECT id, tag_id FROM post ORDER BY id") == [
        (1, 3), (2, 2),
    ]
    assert _fetch(connection, "SELECT name FROM sqlite_master WHERE name='post_tag'")


def test_collapse_multiple_defaults_the_fk_column_to_integer(relation_db):
    """No target_type — the Integer default, which is what revisions
    generated before the argument existed rely on."""
    operations, connection = relation_db

    operations.collapse_multiple(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id", foreign_table="tag",
    )

    assert _column(connection, "post", "tag_id")[2] == "INTEGER"


def test_collapse_multiple_honours_a_string_target_type(db):
    """A model with a STRING primary key (BigQueryModel) needs a string FK
    column — a hardcoded Integer could not hold its own foreign keys."""
    operations, connection = db
    _create(connection, 'CREATE TABLE tag (id VARCHAR(26) PRIMARY KEY)')
    _create(connection, 'CREATE TABLE post (id VARCHAR(26) PRIMARY KEY)',
            [("INSERT INTO post VALUES ('01ABC'), ('01DEF')", {})])
    _create(
        connection,
        'CREATE TABLE post_tag (post_id VARCHAR(26), tag_id VARCHAR(26), '
        'PRIMARY KEY (post_id, tag_id))',
        [("INSERT INTO post_tag VALUES ('01ABC', 'T1'), ('01DEF', 'T2')", {})],
    )

    operations.collapse_multiple(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id", foreign_table="tag",
        target_type=sa.String(26),
    )

    assert _column(connection, "post", "tag_id")[2] == "VARCHAR(26)"
    # and the string keys actually survive the carry-over
    assert _fetch(connection, "SELECT id, tag_id FROM post ORDER BY id") == [
        ("01ABC", "T1"), ("01DEF", "T2"),
    ]


def test_expand_multiple_creates_one_link_row_per_value(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE tag (id INTEGER PRIMARY KEY)')
    _create(
        connection,
        'CREATE TABLE post (id INTEGER PRIMARY KEY, tag_id INTEGER)',
        [('INSERT INTO post VALUES (1, 5), (2, NULL), (3, 7)', {})],
    )
    _create(connection, 'CREATE TABLE post_tag (post_id INTEGER, tag_id INTEGER)')

    operations.expand_multiple(
        "post", link_table="post_tag", source_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
    )

    assert _fetch(connection, "SELECT post_id, tag_id FROM post_tag ORDER BY post_id") == [
        (1, 5), (3, 7),
    ]
    assert "tag_id" not in [
        row[1] for row in connection.execute(sa.text('PRAGMA table_info("post")'))
    ]


def test_expand_multiple_fills_not_null_payload_columns(db):
    """An association link's payload (its using-skel) may be NOT NULL — the
    copied FK values carry no payload, so without defaults every INSERT
    failed and the migration died on the first row."""
    operations, connection = db
    _create(connection, 'CREATE TABLE post (id INTEGER PRIMARY KEY, tag_id INTEGER)', [
        ('INSERT INTO post VALUES (1, 5), (2, 7)', {}),
    ])
    _create(connection, 'CREATE TABLE post_tag (post_id INTEGER, tag_id INTEGER, '
                        'weight INTEGER NOT NULL, note VARCHAR(20))')

    operations.expand_multiple(
        "post", link_table="post_tag", source_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
        payload_defaults={"weight": 0},   # note stays NULL: nullable
    )

    assert _fetch(connection, "SELECT post_id, tag_id, weight, note FROM post_tag ORDER BY post_id") == [
        (1, 5, 0, None), (2, 7, 0, None),
    ]


def test_expand_multiple_refuses_an_unfilled_payload_stub(db):
    """The generator leaves ``...`` where the model has no default — the
    operation must refuse rather than guess (same contract as remap_values)."""
    operations, connection = db
    _create(connection, 'CREATE TABLE post (id INTEGER PRIMARY KEY, tag_id INTEGER)')
    _create(connection, 'CREATE TABLE post_tag (post_id INTEGER, tag_id INTEGER, due DATE NOT NULL)')

    with pytest.raises(RuntimeError, match="payload column.*'due'.*payload_defaults"):
        operations.expand_multiple(
            "post", link_table="post_tag", source_column="tag_id",
            link_parent_fk="post_id", link_dest_fk="tag_id",
            payload_defaults={"due": ...},
        )


def test_expand_multiple_can_keep_the_source_column(db):
    operations, connection = db
    _create(connection, 'CREATE TABLE post (id INTEGER PRIMARY KEY, tag_id INTEGER)', [
        ('INSERT INTO post VALUES (1, 5)', {}),
    ])
    _create(connection, 'CREATE TABLE post_tag (post_id INTEGER, tag_id INTEGER)')

    operations.expand_multiple(
        "post", link_table="post_tag", source_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id", drop_source_column=False,
    )

    assert _fetch(connection, "SELECT tag_id FROM post") == [(5,)]


# --------------------------------------------------------------------------- #
# autogenerate renderers                                                      #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def render():
    """Render an operation through a REAL AutogenContext — Alembic's type
    renderer reaches into ``migration_context.impl`` and ``opts``, so a stub
    does not survive it."""
    from alembic.autogenerate.api import AutogenContext

    engine = sa.create_engine("sqlite://")
    connection = engine.connect()
    migration_context = MigrationContext.configure(
        connection,
        # the prefixes autogenerate normally injects
        opts={"sqlalchemy_module_prefix": "sa.", "alembic_module_prefix": "op."},
    )
    context = AutogenContext(migration_context, metadata=sa.MetaData())

    def _render(operation):
        return migrate._render_op(context, operation), context.imports

    yield _render
    connection.close()
    engine.dispose()


def test_render_reduce_languages(render):
    line, imports = render(migrate.ReduceLanguagesOp(
        "post", column="title", new_type=sa.String(200),
        languages=["de", "en"], keep="de", pk="id", nullable=True,
    ))
    assert line.startswith("op.reduce_languages('post', 'title', ")
    assert "new_type=sa.String(length=200)" in line
    assert "languages=['de', 'en']" in line and "keep='de'" in line
    assert "from viur.models import migrate  # noqa: F401" in imports


def test_render_fill_column_puts_the_value_positionally_once(render):
    line, _ = render(migrate.FillColumnOp(
        "post", column="slug", value="", nullable=False,
    ))
    assert line == "op.fill_column('post', 'slug', '', nullable=False)"


def test_render_remap_values_renders_the_stub_as_ellipsis(render):
    line, _ = render(migrate.RemapValuesOp(
        "post", column="flag", mapping={True: ..., False: "no"},
        new_type=sa.String(3), pk="id", nullable=True,
    ))
    assert "{True: ..., False: 'no'}" in line


def test_render_skips_a_none_type(render):
    line, _ = render(migrate.ExpandLanguagesOp(
        "post", column="title", languages=["de"], new_type=None, pk="id",
    ))
    assert "new_type" not in line


def test_render_collapse_multiple_has_no_column_argument(render):
    line, _ = render(migrate.CollapseMultipleOp(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
        foreign_table="tag", foreign_column="id", keep="first",
        pk="id", drop_link_table=True,
    ))
    assert line.startswith("op.collapse_multiple('post', link_table='post_tag'")


def test_render_collapse_multiple_emits_the_target_type(render):
    """Rendered as a SQL type (``sa.String(...)``), not through repr() —
    a bare ``String()`` in the revision would be a NameError."""
    line, _ = render(migrate.CollapseMultipleOp(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
        foreign_table="tag", foreign_column="id",
        target_type=sa.String(26), keep="first", pk="id", drop_link_table=True,
    ))
    assert "target_type=sa.String(length=26)" in line

    # None means "use the operation's default" and is left out entirely
    line, _ = render(migrate.CollapseMultipleOp(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
        foreign_table="tag", foreign_column="id", target_type=None,
    ))
    assert "target_type" not in line


def test_render_expand_multiple_emits_payload_defaults_with_stubs(render):
    line, _ = render(migrate.ExpandMultipleOp(
        "post", link_table="post_tag", source_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id",
        payload_defaults={"weight": 0, "due": ...}, pk="id", drop_source_column=True,
    ))
    assert "payload_defaults={'weight': 0, 'due': ...}" in line

    # no payload -> the argument is left out, as revisions before it looked
    line, _ = render(migrate.ExpandMultipleOp(
        "post", link_table="post_tag", source_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id", payload_defaults=None,
    ))
    assert "payload_defaults" not in line


def test_renderers_are_registered_for_every_operation():
    from alembic.autogenerate import renderers

    for cls in (
        migrate.ReduceLanguagesOp, migrate.ExpandLanguagesOp,
        migrate.CoerceNumericOp, migrate.CoerceTextOp, migrate.CoerceBoolOp,
        migrate.RemapValuesOp, migrate.FillColumnOp,
        migrate.CollapseMultipleOp, migrate.ExpandMultipleOp,
    ):
        assert renderers.dispatch(cls(  # a renderer exists for each
            "t", column="c", mapping={}, value=None, new_type=None,
            languages=[], keep=None, pk="id", nullable=True, precision=0,
            truthy=[], link_table="l", target_column="c", link_parent_fk="p",
            link_dest_fk="d", foreign_table="f", foreign_column="id",
            source_column="s", drop_link_table=True, drop_source_column=True,
        )) is migrate._render_op


def test_remap_values_maps_a_plain_int_through_its_truthiness(db):
    """SQLite hands back 0/1 for booleans, and Python's ``1 == True`` covers
    those. A different integer still resolves through ``bool(value)``."""
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, flag INTEGER)', [
        ('INSERT INTO m VALUES (1, 7)', {}),
        ('INSERT INTO m VALUES (2, 0)', {}),
    ])

    operations.remap_values(
        "m", "flag", {True: "yes", False: "no"}, new_type=sa.String(10),
    )

    assert _fetch(connection, "SELECT id, flag FROM m ORDER BY id") == [
        (1, "yes"), (2, "no"),
    ]


def test_collapse_multiple_stays_quiet_when_nothing_is_lost(db, capsys):
    operations, connection = db
    _create(connection, 'CREATE TABLE tag (id INTEGER PRIMARY KEY)')
    _create(connection, 'CREATE TABLE post (id INTEGER PRIMARY KEY)', [
        ('INSERT INTO post VALUES (1), (2)', {}),
    ])
    _create(connection, 'CREATE TABLE post_tag (post_id INTEGER, tag_id INTEGER)', [
        ('INSERT INTO post_tag VALUES (1, 5), (2, 6)', {}),   # one each
    ])

    operations.collapse_multiple(
        "post", link_table="post_tag", target_column="tag_id",
        link_parent_fk="post_id", link_dest_fk="tag_id", foreign_table="tag",
    )

    assert _fetch(connection, "SELECT id, tag_id FROM post ORDER BY id") == [
        (1, 5), (2, 6)]
    assert "had several targets" not in capsys.readouterr().out


def test_remap_values_int_whose_truthiness_is_also_unmapped(db):
    """An INTEGER column (so the value really arrives as an int) whose
    ``bool()`` the mapping does not cover either — the last fallback."""
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, flag INTEGER)', [
        ('INSERT INTO m VALUES (1, 7)', {}),
    ])

    operations.remap_values("m", "flag", {False: "no"}, new_type=sa.String(10))

    assert _fetch(connection, "SELECT flag FROM m") == [(None,)]


# --------------------------------------------------------------------------- #
# dialect-level enum types                                                    #
# --------------------------------------------------------------------------- #

def test_ensure_schema_type_creates_an_enum_type(db):
    """On Postgres sa.Enum is a real CREATE TYPE that must exist before any
    column uses it. SQLite renders it as VARCHAR, so create() is a no-op
    there — but the call path must exist and not fail."""
    operations, connection = db
    assert migrate.ensure_schema_type(connection, sa.Enum("A", "B", name="probe")) is True
    assert migrate.ensure_schema_type(connection, sa.String(10)) is False  # no create()


def test_transform_column_to_an_enum_type(db):
    """The bool→select path: the target type is an Enum whose TYPE (on
    Postgres) must be created before the temp column. Runs against SQLite
    here, which proves the call is dialect-safe."""
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY, flag BOOLEAN)', [
        ('INSERT INTO m VALUES (1, 1)', {}),
    ])

    operations.remap_values(
        "m", "flag", {True: "YES", False: "NO"},
        new_type=sa.Enum("YES", "NO", name="probe_kind"),
    )

    assert _fetch(connection, "SELECT flag FROM m") == [("YES",)]


def test_add_enum_values_is_a_noop_outside_postgres(db):
    """SQLite has no database-level enum type — the operation must pass
    through without touching anything."""
    operations, connection = db
    _create(connection, 'CREATE TABLE m (id INTEGER PRIMARY KEY)')
    operations.add_enum_values("kind", ["YES", "NO", "MAYBE"])  # must not raise


def test_add_enum_values_emits_alter_type_on_postgres():
    """Captured against a mock Postgres connection: one idempotent
    ALTER TYPE … ADD VALUE IF NOT EXISTS per label, values quoted as
    literals (DDL takes no bind parameters)."""
    executed = []

    class _Dialect:
        name = "postgresql"

    class _Conn:
        dialect = _Dialect()

        def execute(self, clause):
            executed.append(str(clause))

    class _Ops:
        def get_bind(self):
            return _Conn()

    operation = migrate.AddEnumValuesOp("kind", labels=["YES", "it's"])
    migrate._add_enum_values(_Ops(), operation)

    assert executed == [
        'ALTER TYPE "kind" ADD VALUE IF NOT EXISTS \'YES\'',
        'ALTER TYPE "kind" ADD VALUE IF NOT EXISTS \'it\'\'s\'',  # '' escaped
    ]
