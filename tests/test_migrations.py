"""Alembic integration — URL resolution, autogenerate filters/renderers, run modes.

The individual pieces are pinned in isolation against a fake Alembic
context; the last section drives a **real** Alembic round-trip (autogenerate
→ upgrade → check → model change → downgrade) against real SQLite. That
belongs here rather than in the integration suite: it exercises Alembic and
SQLModel, not viur-core, so there is no mock-vs-core drift to catch.
"""
import contextlib
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy import JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator
from sqlmodel import SQLModel
from sqlmodel.sql.sqltypes import AutoString

from viur.models import RecordJSON, Field, Model, db, install_config
from viur.models import migrations


# --------------------------------------------------------------------------- #
# fakes                                                                       #
# --------------------------------------------------------------------------- #

class FakeConfig:
    """Stand-in for ``alembic.config.Config``."""

    config_ini_section = "alembic"

    def __init__(self, main_options=None, section=None):
        self._main = main_options or {}
        self._section = section if section is not None else {}

    def get_main_option(self, key, default=None):
        return self._main.get(key, default)

    def get_section(self, name, default=None):
        return self._section if name == self.config_ini_section else default


class FakeContext:
    """Records what ``env.py`` would drive on the Alembic context."""

    def __init__(self, *, offline=False, x_args=None, config=None):
        self.config = config or FakeConfig()
        self._offline = offline
        self._x_args = x_args or {}
        self.configured = None
        self.migrations_ran = 0
        self.transactions = 0

    def is_offline_mode(self):
        return self._offline

    def get_x_argument(self, as_dictionary=False):
        assert as_dictionary is True
        return dict(self._x_args)

    def configure(self, **kwargs):
        self.configured = kwargs

    @contextlib.contextmanager
    def begin_transaction(self):
        self.transactions += 1
        yield

    def run_migrations(self):
        self.migrations_ran += 1


class _EngineWithUrl:
    """Minimal ready-made "engine" — ``db.configure()`` accepts one, and
    ``resolve_url`` only reads its ``.url``. Needed because
    ``create_engine("postgresql+pg8000://…")`` imports the pg8000 driver
    eagerly (absent here) and ``create_mock_engine`` exposes no ``.url``.
    """

    class _Dialect:
        name = "postgresql"

    dialect = _Dialect()

    def __init__(self, url: str):
        from sqlalchemy.engine import make_url

        self.url = make_url(url)

    def dispose(self) -> None:
        """Called by ``db.reset()``."""


@pytest.fixture()
def fake_context(monkeypatch):
    def _install(**kwargs):
        ctx = FakeContext(**kwargs)
        monkeypatch.setattr(migrations, "context", ctx)
        return ctx

    return _install


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No inherited DSN, no configured engine, no conf preset."""
    monkeypatch.delenv(migrations.DSN_ENV_VAR, raising=False)
    db.reset()
    install_config().engine = None
    yield
    db.reset()


# --------------------------------------------------------------------------- #
# target_metadata / import_models                                             #
# --------------------------------------------------------------------------- #

def test_target_metadata_is_the_shared_sqlmodel_metadata():
    assert migrations.target_metadata() is SQLModel.metadata


def test_import_models_imports_package_and_public_submodules(tmp_path, monkeypatch):
    package = tmp_path / "probe_models"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "thing.py").write_text(
        "from sqlmodel import SQLModel, Field\n"
        "class ProbeThing(SQLModel, table=True):\n"
        "    __tablename__ = 'probe_thing'\n"
        "    id: int | None = Field(default=None, primary_key=True)\n",
    )
    (package / "_private.py").write_text("raise AssertionError('must not be imported')")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [m for m in sys.modules if m.startswith("probe_models")]:
        del sys.modules[name]

    imported = migrations.import_models("probe_models")

    assert imported == ["probe_models", "probe_models.thing"]
    # the point of the exercise: the table is now in the metadata
    assert "probe_thing" in SQLModel.metadata.tables
    SQLModel.metadata.remove(SQLModel.metadata.tables["probe_thing"])


# --------------------------------------------------------------------------- #
# resolve_url — the precedence chain                                          #
# --------------------------------------------------------------------------- #

def test_x_argument_wins_over_everything(monkeypatch):
    monkeypatch.setenv(migrations.DSN_ENV_VAR, "sqlite:///from-env.db")
    url = migrations.resolve_url(
        FakeConfig({"sqlalchemy.url": "sqlite:///from-ini.db"}),
        x_args={"url": "  sqlite:///from-x.db  "},
        fallback_url="sqlite:///from-fallback.db",
    )
    assert url == "sqlite:///from-x.db"


def test_env_var_wins_over_engine_and_fallback(monkeypatch):
    monkeypatch.setenv(migrations.DSN_ENV_VAR, "sqlite:///from-env.db")
    db.configure("sqlite://")
    assert migrations.resolve_url(fallback_url="sqlite:///x.db") \
        == "sqlite:///from-env.db"


def test_blank_x_argument_and_env_var_are_skipped(monkeypatch):
    monkeypatch.setenv(migrations.DSN_ENV_VAR, "   ")
    url = migrations.resolve_url(x_args={"url": "  "}, fallback_url="sqlite:///f.db")
    assert url == "sqlite:///f.db"


def test_configured_engine_is_used():
    db.configure("sqlite:///from-engine.db")
    assert migrations.resolve_url() == "sqlite:///from-engine.db"


def test_configured_engine_keeps_its_password():
    """The URL is handed to Alembic verbatim — a redacted password would
    make the migration fail to connect. (A mock engine, because
    ``create_engine`` imports the DBAPI driver eagerly.)"""
    db.configure(_EngineWithUrl("postgresql+pg8000://user:pw@host:5432/app"))
    assert migrations.resolve_url() == "postgresql+pg8000://user:pw@host:5432/app"


def test_conf_preset_is_used_when_an_engine_is_set():
    cfg = install_config()
    cfg.engine = "sqlite"
    cfg.sqlite_file = "/tmp/from-conf.sqlite3"
    assert migrations.resolve_url(fallback_url="sqlite:///f.db") \
        == "sqlite:////tmp/from-conf.sqlite3"


def test_misconfigured_conf_preset_surfaces_instead_of_falling_through():
    """A preset that cannot yield a URL is an error, not a reason to guess."""
    cfg = install_config()
    cfg.engine = "postgres"
    cfg.postgres_dsn = ""
    with pytest.raises(RuntimeError, match="postgres_dsn"):
        migrations.resolve_url(fallback_url="sqlite:///f.db")


def test_fallback_url_wins_over_alembic_ini():
    url = migrations.resolve_url(
        FakeConfig({"sqlalchemy.url": "sqlite:///from-ini.db"}),
        fallback_url="sqlite:///from-fallback.db",
    )
    assert url == "sqlite:///from-fallback.db"


def test_alembic_ini_is_the_last_resort():
    url = migrations.resolve_url(
        FakeConfig({"sqlalchemy.url": "sqlite:///from-ini.db"}), fallback_url="  ",
    )
    assert url == "sqlite:///from-ini.db"


def test_no_source_lists_what_was_tried():
    with pytest.raises(RuntimeError) as excinfo:
        migrations.resolve_url(FakeConfig({"sqlalchemy.url": ""}))
    message = str(excinfo.value)
    assert migrations.DSN_ENV_VAR in message and "alembic.ini" in message


def test_no_source_and_no_config_at_all_raises():
    """Library use without an Alembic config — nothing left to try."""
    with pytest.raises(RuntimeError, match="No database URL"):
        migrations.resolve_url()


def test_conf_absent_falls_through(monkeypatch):
    monkeypatch.setattr(migrations, "_models_conf", lambda: None)
    assert migrations.resolve_url(fallback_url="sqlite:///f.db") == "sqlite:///f.db"


# --------------------------------------------------------------------------- #
# include_object                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("type_", "name", "expected"),
    [
        ("table", "alembic_version", False),   # Alembic's own bookkeeping
        ("table", "viur_models_relations", True),   # ours — a real, migratable table
        ("table", "example_entry", True),
        ("column", "alembic_version", True),   # only the TABLE is excluded
    ],
)
def test_include_object(type_, name, expected):
    assert migrations.include_object(None, name, type_, False, None) is expected


# --------------------------------------------------------------------------- #
# render_item                                                                 #
# --------------------------------------------------------------------------- #

class _Address(SQLModel):
    street: str = "x"


class _StringDecorator(TypeDecorator):
    impl = String(50)
    cache_ok = True


class _JsonbDecorator(TypeDecorator):
    impl = JSONB
    cache_ok = True


class _AutogenContext:
    def __init__(self):
        self.imports = set()


def test_record_json_renders_as_its_ddl_type():
    """RecordJSON(Address) must not leak into a revision — the DDL is JSON."""
    ctx = _AutogenContext()
    assert migrations.render_item("type", RecordJSON(_Address), ctx) == "sa.JSON()"
    assert "import sqlalchemy as sa" in ctx.imports


def test_decorator_keeps_its_impl_parameters():
    assert migrations.render_item("type", _StringDecorator(), _AutogenContext()) \
        == "sa.String(length=50)"


def test_sqlmodel_own_types_are_left_to_alembic():
    """AutoString round-trips through Alembic's default rendering."""
    assert migrations.render_item("type", AutoString(50), _AutogenContext()) is False


def test_dialect_specific_impl_is_left_to_alembic():
    """``sa.JSONB`` does not exist — better the full path than something wrong."""
    assert migrations.render_item("type", _JsonbDecorator(), _AutogenContext()) is False


@pytest.mark.parametrize(
    ("type_", "obj"),
    [("type", JSON()), ("table", None), ("column", None)],
)
def test_render_item_passes_everything_else_through(type_, obj):
    assert migrations.render_item(type_, obj, _AutogenContext()) is False


# --------------------------------------------------------------------------- #
# run modes                                                                   #
# --------------------------------------------------------------------------- #

def test_offline_mode_configures_literal_binds(fake_context):
    ctx = fake_context(offline=True, x_args={"url": "sqlite:///offline.db"})

    assert migrations.run() == "sqlite:///offline.db"

    assert ctx.configured["url"] == "sqlite:///offline.db"
    assert ctx.configured["literal_binds"] is True
    assert ctx.configured["target_metadata"] is SQLModel.metadata
    assert ctx.configured["render_item"] is migrations.render_item
    assert ctx.migrations_ran == 1 and ctx.transactions == 1


def test_online_mode_connects_and_runs(fake_context):
    ctx = fake_context(x_args={"url": "sqlite://"})

    assert migrations.run() == "sqlite://"

    assert ctx.configured["connection"] is not None
    assert "url" not in ctx.configured
    assert ctx.migrations_ran == 1 and ctx.transactions == 1


def test_online_mode_injects_the_url_into_the_ini_section(fake_context):
    section = {"sqlalchemy.url": "sqlite:///stale.db"}
    ctx = fake_context(config=FakeConfig(section=section), x_args={"url": "sqlite://"})

    migrations.run()

    # the resolved URL replaces whatever alembic.ini carried
    assert section["sqlalchemy.url"] == "sqlite://"


def test_online_mode_tolerates_a_missing_ini_section(fake_context):
    ctx = fake_context(config=FakeConfig(section=None), x_args={"url": "sqlite://"})
    migrations.run()
    assert ctx.migrations_ran == 1


def test_sqlite_enables_batch_mode_and_type_comparison(fake_context):
    ctx = fake_context(offline=True, x_args={"url": "sqlite:///x.db"})
    migrations.run()
    assert ctx.configured["render_as_batch"] is True
    assert ctx.configured["compare_type"] is True


def test_postgres_does_not_use_batch_mode(fake_context):
    ctx = fake_context(offline=True, x_args={"url": "postgresql+pg8000://h/db"})
    migrations.run()
    assert ctx.configured["render_as_batch"] is False


def test_overrides_reach_context_configure(fake_context):
    ctx = fake_context(offline=True, x_args={"url": "sqlite:///x.db"})
    migrations.run(compare_server_default=True, render_as_batch=False)
    assert ctx.configured["compare_server_default"] is True
    assert ctx.configured["render_as_batch"] is False  # override wins


# --------------------------------------------------------------------------- #
# db.schema_revision                                                          #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def file_engine(tmp_path):
    """A file-backed SQLite engine — ``sqlite://`` hands out a FRESH
    in-memory database per connection, so a table created in one connection
    is invisible to the next (which is exactly what schema_revision opens)."""
    return db.configure(f"sqlite:///{tmp_path / 'probe.sqlite3'}")


def _create_version_table(engine, revision=None):
    from sqlalchemy import text

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        if revision is not None:
            connection.execute(text(f"INSERT INTO alembic_version VALUES ('{revision}')"))


def test_schema_revision_is_none_without_the_bookkeeping_table(file_engine):
    """An unmigrated database — what main.py warns about."""
    assert db.schema_revision(file_engine) is None


def test_schema_revision_reads_the_stamped_version(file_engine):
    _create_version_table(file_engine, "abc123")
    assert db.schema_revision(file_engine) == "abc123"   # explicit engine
    assert db.schema_revision() == "abc123"              # or the configured one


def test_schema_revision_is_none_when_the_table_is_empty(file_engine):
    _create_version_table(file_engine)
    assert db.schema_revision(file_engine) is None


# --------------------------------------------------------------------------- #
# end-to-end: a real Alembic round-trip                                       #
# --------------------------------------------------------------------------- #

class MigProbe(Model, table=True):
    """Model for the round-trip below — carries a ``RecordJSON`` column, the
    case that forces :func:`viur.models.migrations.render_item`."""

    __tablename__ = "probe_mig_entry"

    name: str = Field(descr="Name", max_length=40)
    address: _Address | None = Field(default=None, sa_type=RecordJSON(_Address))


ENV_PY = '''
from viur.models import migrations

def include_object(obj, name, type_, reflected, compare_to):
    """Scope the run to this test's tables — SQLModel.metadata is shared by
    the whole test session."""
    return type_ != "table" or (name or "").startswith("probe_mig")

migrations.run(fallback_url={url!r}, include_object=include_object)
'''

SCRIPT_MAKO = '''"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


@pytest.fixture()
def alembic_project(tmp_path):
    """A throwaway Alembic scaffold wired to viur.models.migrations."""
    from alembic.config import Config

    database = tmp_path / "probe.sqlite3"
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    (scripts / "env.py").write_text(ENV_PY.format(url=f"sqlite:///{database}"))
    (scripts / "script.py.mako").write_text(SCRIPT_MAKO)

    config = Config()
    config.set_main_option("script_location", str(scripts))
    config.set_main_option("sqlalchemy.url", "")
    return config, database, scripts


def _revisions(scripts):
    return sorted(p.name for p in (scripts / "versions").glob("*.py"))


def test_end_to_end_autogenerate_upgrade_check_downgrade(alembic_project):
    """The whole point of the module: model → revision → schema, and a
    later model change is detected as a diff rather than silently ignored.
    """
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected
    from sqlalchemy import create_engine, inspect

    config, database, scripts = alembic_project
    engine = create_engine(f"sqlite:///{database}")

    # 1. autogenerate from the models
    command.revision(config, message="initial", autogenerate=True)
    assert len(_revisions(scripts)) == 1

    # RecordJSON must have become plain JSON — not an unimportable
    # ``viur.models.db.RecordJSON()`` missing its record class.
    body = (scripts / "versions" / _revisions(scripts)[0]).read_text()
    assert "sa.JSON()" in body and "RecordJSON" not in body

    # 2. apply it
    command.upgrade(config, "head")
    assert "probe_mig_entry" in inspect(engine).get_table_names()
    assert db.schema_revision(engine) is not None      # stamped

    # 3. models and schema now agree — `alembic check` is the CI gate
    command.check(config)

    # 4. a model change IS detected
    MigProbe.__table__.append_column(sa.Column("extra", sa.Integer(), nullable=True))
    try:
        with pytest.raises(AutogenerateDiffsDetected):
            command.check(config)
        command.revision(config, message="add extra", autogenerate=True)
        assert len(_revisions(scripts)) == 2
        command.upgrade(config, "head")
        assert "extra" in {c["name"] for c in inspect(engine).get_columns("probe_mig_entry")}
        command.check(config)
    finally:
        MigProbe.__table__._columns.remove(MigProbe.__table__.c.extra)

    # 5. and it is reversible
    command.downgrade(config, "base")
    assert "probe_mig_entry" not in inspect(engine).get_table_names()
    engine.dispose()
