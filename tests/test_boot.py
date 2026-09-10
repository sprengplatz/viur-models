"""``install()`` / ``setup()`` — the app-boot wiring (viur.models.boot)."""
import logging

import pytest
from sqlalchemy import MetaData, text
from sqlmodel import SQLModel, select

from viur.core import conf
from viur.core.skeleton import Skeleton
from viur.models import Field, Model, boot, db, install, scaffold, setup
from viur.models.config import ModelsConfig


class BootProbe(Model, table=True):
    __tablename__ = "viur_models_test_bootprobe"
    name: str = Field(default="", required=False)


@pytest.fixture(autouse=True)
def _clean_conf_engine_and_hooks():
    """``conf.models``, the shared engine and the ``Skeleton`` patch are all
    global state — every test starts from, and leaves behind, an unwired app."""
    saved = Skeleton.__dict__["postSavedHandler"]
    deleted = Skeleton.__dict__["postDeletedHandler"]

    def unwire():
        if hasattr(conf, "models"):
            delattr(conf, "models")
        db.reset()
        # install_refresh_hooks patches the Skeleton class itself and is
        # idempotent via this flag — undo both, or test order would matter.
        Skeleton.postSavedHandler = saved
        Skeleton.postDeletedHandler = deleted
        Skeleton._viur_models_refresh_hooks = False

    unwire()
    yield
    unwire()


# --------------------------------------------------------------------------- #
# install()                                                                    #
# --------------------------------------------------------------------------- #

def test_install_attaches_conf_and_applies_settings(tmp_path):
    engine = install(engine="sqlite", sqlite_file=str(tmp_path / "boot.sqlite3"))
    assert isinstance(conf.models, ModelsConfig)
    assert conf.models.databases["default"]["engine"] == "sqlite"
    assert engine.url.database == str(tmp_path / "boot.sqlite3")
    assert db.get_engine() is engine


def test_install_applies_every_conf_setting(monkeypatch):
    # No postgres driver in the unit env — capture instead of connecting.
    monkeypatch.setattr(db, "configure", lambda url, **kw: "engine-sentinel")
    options = {"echo": True}
    assert install(
        engine="postgres",
        postgres_dsn="postgresql+pg8000://user:pw@10.0.0.1:5432/app",
        bigquery_dsn="bigquery://project/dataset",
        sqlite_file="unused.sqlite3",
        engine_options=options,
        refresh_hooks=False,
        schema_report=False,
    ) == "engine-sentinel"
    assert conf.models.databases == {"default": {
        "engine": "postgres",
        "postgres_dsn": "postgresql+pg8000://user:pw@10.0.0.1:5432/app",
        "bigquery_dsn": "bigquery://project/dataset",
        "sqlite_file": "unused.sqlite3",
        "engine_options": options,
    }}


def test_install_leaves_unset_arguments_alone(tmp_path):
    """A ``None`` argument must not clobber a value set elsewhere — that is
    what makes a partial second call safe."""
    first = str(tmp_path / "first.sqlite3")
    install(engine="sqlite", sqlite_file=first)
    install(refresh_hooks=False)  # no settings at all
    assert conf.models.databases["default"] == {"engine": "sqlite", "sqlite_file": first}


def test_install_wires_the_refresh_hooks():
    assert not getattr(Skeleton, "_viur_models_refresh_hooks", False)
    install(engine="memory")
    assert Skeleton._viur_models_refresh_hooks is True


def test_install_can_skip_the_refresh_hooks():
    install(engine="memory", refresh_hooks=False)
    assert not getattr(Skeleton, "_viur_models_refresh_hooks", False)


def test_install_forwards_refresh_hook_kwargs():
    captured = {}

    def fake_hooks(**kwargs):
        captured.update(kwargs)

    from viur.models import crossstore

    original = crossstore.install_refresh_hooks
    crossstore.install_refresh_hooks = fake_hooks
    try:
        install(engine="memory", missing_on_delete="keep", countdown=42)
    finally:
        crossstore.install_refresh_hooks = original
    assert captured == {"missing_on_delete": "keep", "countdown": 42}


def test_install_rejects_unknown_refresh_hook_kwargs():
    with pytest.raises(TypeError):
        install(engine="memory", nonsense=True)


def test_install_propagates_a_misconfigured_preset():
    with pytest.raises(RuntimeError, match="postgres_dsn"):
        install(engine="postgres")


# --------------------------------------------------------------------------- #
# setup()                                                                      #
# --------------------------------------------------------------------------- #

def test_setup_creates_the_schema_for_the_memory_preset():
    engine = install(engine="memory", refresh_hooks=False)
    assert setup() is None
    with db.get_session() as session:  # the table exists, so this works
        session.add(BootProbe(name="kept"))
    with db.get_session() as session:
        assert [row.name for row in session.exec(select(BootProbe)).all()] == ["kept"]
    assert engine.url.render_as_string() == "sqlite://"


def test_setup_warns_when_no_model_was_imported(monkeypatch, caplog):
    """The one way setup() can quietly do nothing — an empty metadata means
    create_all() creates nothing and the app serves an empty database."""
    install(engine="memory", refresh_hooks=False)
    monkeypatch.setattr(SQLModel, "metadata", MetaData())
    with caplog.at_level(logging.WARNING, logger=boot.logger.name):
        assert setup() is None
    assert "empty SQLModel.metadata" in caplog.text


def test_setup_reports_an_unmigrated_schema(tmp_path, caplog):
    install(engine="sqlite", sqlite_file=str(tmp_path / "fresh.sqlite3"),
            refresh_hooks=False)
    with caplog.at_level(logging.ERROR, logger=boot.logger.name):
        assert setup() is None
    assert "alembic upgrade head" in caplog.text


def test_setup_reports_the_stamped_revision(tmp_path, caplog):
    engine = install(engine="sqlite", sqlite_file=str(tmp_path / "stamped.sqlite3"),
                     refresh_hooks=False)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('abc123')"))
    with caplog.at_level(logging.INFO, logger=boot.logger.name):
        assert setup() == "abc123"
    assert "abc123" in caplog.text


def test_setup_accepts_an_explicit_engine(tmp_path):
    """A process may inspect a database other than the configured one."""
    install(engine="sqlite", sqlite_file=str(tmp_path / "configured.sqlite3"),
            refresh_hooks=False)
    from sqlalchemy import create_engine

    other = create_engine(f"sqlite:///{tmp_path / 'other.sqlite3'}")
    try:
        with other.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('other-rev')"))
        assert setup(other) == "other-rev"
    finally:
        # db.reset() only disposes the CONFIGURED engine; this one is local.
        # Left open, its SQLite connection is closed by the garbage collector
        # instead — a ResourceWarning, which this suite escalates to an error
        # against whichever test happens to be running at the time.
        other.dispose()


# --------------------------------------------------------------------------- #
# setup(migrations=…) — the generated scaffold                                 #
# --------------------------------------------------------------------------- #

@pytest.fixture
def dev_server(monkeypatch):
    """Pretend to be the local dev server (the only place that scaffolds)."""
    monkeypatch.setattr(boot, "is_dev_server", lambda: True)


@pytest.fixture
def no_bootstrap(monkeypatch):
    """Record the initial-revision call instead of running Alembic."""
    calls = []
    monkeypatch.setattr(boot, "_bootstrap_revision", lambda root: calls.append(root))
    return calls


def test_setup_generates_the_scaffold(tmp_path, dev_server, no_bootstrap):
    install(engine="memory", refresh_hooks=False)
    setup(migrations=tmp_path, app_dir=tmp_path / "deploy")
    assert scaffold.is_complete(tmp_path)
    assert no_bootstrap == [tmp_path]


def test_setup_does_not_scaffold_without_the_argument(tmp_path, dev_server, no_bootstrap):
    install(engine="memory", refresh_hooks=False)
    setup()
    assert not scaffold.is_complete(tmp_path)
    assert no_bootstrap == []


def test_setup_does_not_scaffold_in_production(tmp_path, monkeypatch, no_bootstrap):
    """A deployed instance has a read-only filesystem and no business owning
    migration sources."""
    monkeypatch.setattr(boot, "is_dev_server", lambda: False)
    install(engine="memory", refresh_hooks=False)
    setup(migrations=tmp_path)
    assert not (tmp_path / "alembic.ini").exists()
    assert no_bootstrap == []


def test_setup_scaffolds_only_once(tmp_path, dev_server, no_bootstrap):
    """The second boot must not regenerate — and must not re-run the initial
    revision, which would pile up empty revisions."""
    install(engine="memory", refresh_hooks=False)
    setup(migrations=tmp_path)
    setup(migrations=tmp_path)
    assert no_bootstrap == [tmp_path]  # exactly one bootstrap


def test_setup_can_skip_the_initial_revision(tmp_path, dev_server, no_bootstrap):
    install(engine="memory", refresh_hooks=False)
    setup(migrations=tmp_path, initial_revision=False)
    assert scaffold.is_complete(tmp_path)
    assert no_bootstrap == []


def test_setup_survives_an_unwritable_scaffold_target(tmp_path, dev_server, no_bootstrap, caplog):
    """A scaffold that cannot be written must not stop an app from serving."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    install(engine="memory", refresh_hooks=False)
    with caplog.at_level(logging.WARNING, logger=boot.logger.name):
        setup(migrations=blocked)  # must not raise
    assert "could not write the Alembic scaffold" in caplog.text
    assert no_bootstrap == []


def test_bootstrap_reports_a_missing_alembic(tmp_path, monkeypatch, caplog):
    """Alembic is an optional extra — the app keeps running, only unmigrated."""
    import builtins

    real_import = builtins.__import__

    def no_alembic(name, *args, **kwargs):
        if name.startswith("alembic"):
            raise ImportError("no alembic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_alembic)
    with caplog.at_level(logging.WARNING, logger=boot.logger.name):
        assert boot._bootstrap_revision(tmp_path) is None
    assert "alembic is not installed" in caplog.text


def test_bootstrap_reports_a_failure_instead_of_raising(tmp_path, caplog):
    """No alembic.ini at that path — the boot must survive it."""
    install(engine="memory", refresh_hooks=False)
    with caplog.at_level(logging.WARNING, logger=boot.logger.name):
        assert boot._bootstrap_revision(tmp_path) is None
    assert "could not apply the generated scaffold" in caplog.text


def test_has_model_tables_follows_the_database(tmp_path):
    install(engine="memory", refresh_hooks=False)
    assert boot._has_model_tables() is False
    setup()  # create_all for the memory preset
    assert boot._has_model_tables() is True


def test_is_dev_server_follows_core(monkeypatch):
    from viur.core import conf

    monkeypatch.setattr(conf.instance, "is_dev_server", True, raising=False)
    assert boot.is_dev_server() is True
    monkeypatch.setattr(conf.instance, "is_dev_server", False, raising=False)
    assert boot.is_dev_server() is False


# --------------------------------------------------------------------------- #
# _bootstrap_revision — a real Alembic run over a generated scaffold           #
# --------------------------------------------------------------------------- #

@pytest.fixture
def project(tmp_path):
    """A throwaway project on disk: a generated scaffold plus the two modules
    its ``env.py`` imports. ``prepend_sys_path`` in the generated ini puts the
    distribution folder on the path, so the imports resolve."""
    root = tmp_path / "project"
    app = root / "deploy"
    (app / "models").mkdir(parents=True)
    (app / "models" / "__init__.py").write_text("")
    # env.py imports it, but the URL actually used is the engine configured in
    # this process (resolve_url precedence) — this is only the fallback.
    (app / "models_db.py").write_text(
        f'def url():\n    return "sqlite:///{tmp_path / "fallback.sqlite3"}"\n'
    )
    scaffold.generate(root, app_dir=app)
    return root


def _revisions(root):
    return sorted((root / "migrations" / "versions").glob("*.py"))


def test_bootstrap_creates_and_applies_the_first_revision(project, tmp_path):
    """Empty database: the revision creates the tables and is applied."""
    install(engine="sqlite", sqlite_file=str(tmp_path / "fresh.sqlite3"),
            refresh_hooks=False)
    assert boot._bootstrap_revision(project) == "upgrade"

    revisions = _revisions(project)
    assert len(revisions) == 1
    assert "op.create_table" in revisions[0].read_text()
    assert setup() is not None  # the database is stamped now


def test_empty_db_probe_never_mutates_the_callers_config():
    """The probe URL goes onto a COPY of the config.

    Setting it on the caller's object and restoring it in ``finally`` is the
    race this avoids: a second caller entering while the override is in place
    would autogenerate against the throwaway database, and interleaved
    restores can leave the override behind permanently.
    """
    from alembic.config import Config

    config = Config("alembic.ini")
    config.cmd_opts = "untouched"

    with boot._autogenerate_against_empty_db(config) as scoped:
        assert scoped is not config
        assert config.cmd_opts == "untouched"          # during, not just after
        [override] = scoped.cmd_opts.x
        assert override.startswith("url=sqlite:///")   # the probe database
        assert override.endswith("empty.sqlite3")
        assert scoped.config_file_name == config.config_file_name  # same config

    assert config.cmd_opts == "untouched"              # and nothing to restore


def test_empty_db_probe_is_skipped_for_an_empty_database():
    """The other arm hands the config through unchanged."""
    from alembic.config import Config

    config = Config("alembic.ini")
    with boot._unchanged(config) as scoped:
        assert scoped is config


def test_bootstrap_adopts_a_populated_database_without_touching_it(project, tmp_path):
    """A database that already has the tables must be STAMPED, not upgraded —
    and the revision must still describe the full schema, or the history could
    never rebuild it."""
    engine = install(engine="sqlite", sqlite_file=str(tmp_path / "existing.sqlite3"),
                     refresh_hooks=False)
    SQLModel.metadata.create_all(engine)
    with db.get_session() as session:
        session.add(BootProbe(name="survives"))

    assert boot._bootstrap_revision(project) == "stamp"

    revisions = _revisions(project)
    assert len(revisions) == 1
    # Not an empty revision — this is the whole point of the empty-db probe.
    assert "op.create_table" in revisions[0].read_text()
    assert setup() is not None  # stamped
    with db.get_session() as session:
        assert [row.name for row in session.exec(select(BootProbe)).all()] == ["survives"]
