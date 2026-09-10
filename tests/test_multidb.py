"""Named engines — ``Model.viur_database`` decides where a model lives."""
import pytest
from sqlalchemy import JSON
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, select

from viur.models import CrossStoreIndex, Field, Model, RelationLink, UserRef, db, install, install_config, setup
from viur.models import crossstore, migrations
from viur.models.sqllist import SQLList

from tests.test_sqllist import RecordingRender


class Metric(Model, table=True):
    __tablename__ = "viur_models_test_metric"
    viur_database = "analytics"
    name: str = Field(descr="Name", max_length=50)
    author: UserRef() | None = Field(default=None, sa_type=JSON, descr="Autor")


class MetricModule(SQLList):
    model = Metric

    def __init__(self):
        super().__init__("metrics", "/metrics")
        self.render = RecordingRender()

    def can(self, instance):
        return True


class LocalNote(Model, table=True):
    __tablename__ = "viur_models_test_localnote"
    name: str = Field(descr="Name", max_length=50)


def _memory():
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


@pytest.fixture()
def two_databases():
    for name in ("default", "analytics"):
        engine = db.configure(_memory(), name=name)
        SQLModel.metadata.create_all(engine, tables=db.tables_for(name))
    yield
    db.reset()


@pytest.fixture(autouse=True)
def _patched_core(monkeypatch):
    monkeypatch.setattr(crossstore, "resolve_relskel", lambda marker: {"key": {"type": "key"}, "name": {"type": "str"}})
    monkeypatch.setattr(crossstore, "read_dest", lambda marker, key: KNOWN.get(key))


KNOWN = {"user/1": {"key": "user/1", "name": "alice"}}


# --- db ----------------------------------------------------------------------

def test_database_of():
    assert db.database_of(None) == "default"
    assert db.database_of("x") == "x"
    assert db.database_of(Metric) == "analytics"
    assert db.database_of(LocalNote) == "default"


def test_get_engine_names_the_missing_database():
    db.reset()
    with pytest.raises(RuntimeError, match="no engine 'analytics'.*name='analytics'"):
        db.get_engine(Metric)


def test_tables_for_splits_models_and_shares_internal_tables():
    default = {t.name for t in db.tables_for("default")}
    analytics = {t.name for t in db.tables_for(Metric)}
    assert "viur_models_test_metric" in analytics and "viur_models_test_metric" not in default
    assert "viur_models_test_localnote" in default and "viur_models_test_localnote" not in analytics
    assert CrossStoreIndex.__tablename__ in default & analytics


def test_sessions_follow_the_model(two_databases):
    assert set(db.engine_names()) == {"default", "analytics"}
    with db.get_session(Metric) as session:
        session.add(Metric(name="m"))
    with db.get_session() as session:
        session.add(LocalNote(name="n"))
    with db.get_session("analytics") as session:
        assert [m.name for m in session.exec(select(Metric)).all()] == ["m"]
    with db.get_session(LocalNote) as session:
        assert [n.name for n in session.exec(select(LocalNote)).all()] == ["n"]
    db.reset()
    assert db.engine_names() == ()


# --- conf / boot -------------------------------------------------------------

def test_install_builds_every_configured_database(tmp_path):
    install(
        engine="memory",
        databases={
            "analytics": {"engine": "memory"},
            "archive": {"url": f"sqlite:///{tmp_path / 'archive.sqlite3'}"},
        },
        refresh_hooks=False,
    )
    try:
        assert set(db.engine_names()) == {"default", "analytics", "archive"}
        assert db.preset_from_conf("analytics") == "memory" and db.preset_from_conf("archive") is None
        assert db.url_from_conf("archive").endswith("archive.sqlite3")
        assert setup() is None  # memory presets: create_all; archive: unmigrated
        with db.get_session(Metric) as session:
            session.add(Metric(name="m"))
        with db.get_session() as session:
            assert session.exec(select(LocalNote)).all() == []
    finally:
        db.reset()


def test_unknown_database_entry_fails_fast():
    install_config().databases.clear()
    with pytest.raises(RuntimeError, match="no entry 'nope'"):
        db.url_from_conf("nope")


def test_setup_treats_hand_configured_engines_as_migratable(two_databases, caplog):
    install_config().databases.clear()
    assert setup() is None
    assert "'analytics' is not migrated" in caplog.text


# --- relations across databases ---------------------------------------------

def test_relation_targets_must_share_the_database():
    from viur.models.base import _check_relation_databases

    _check_relation_databases(LocalNote, {"ok": {"target": LocalNote, "link": None}})
    with pytest.raises(TypeError, match="LocalNote.metric: Metric lives in database 'analytics'"):
        _check_relation_databases(LocalNote, {"metric": {"target": Metric}})
    assert RelationLink.viur_database == "default"


# --- SQLList + cross-store ---------------------------------------------------

def test_module_reads_and_writes_its_own_database(two_databases):
    module = MetricModule()
    verb, instance = module.add(name="cpu", author="user/1", skey="csrf")
    assert verb == "addSuccess"
    with db.get_session("analytics") as session:
        assert [m.name for m in session.exec(select(Metric)).all()] == ["cpu"]
        assert [r.model_table for r in session.exec(select(CrossStoreIndex)).all()] == ["viur_models_test_metric"]
    with db.get_session("default") as session:
        assert session.exec(select(CrossStoreIndex)).all() == []

    verb, rows = module.list(name="cpu")
    assert verb == "list" and [r.name for r in rows] == ["cpu"]
    verb, viewed = module.view(instance.viur_key)
    assert verb == "view" and viewed.name == "cpu"
    verb, edited = module.edit(instance.viur_key, name="mem", skey="csrf")
    assert verb == "editSuccess" and edited.name == "mem"

    KNOWN["user/1"] = {"key": "user/1", "name": "alice@renamed"}
    try:
        assert crossstore.refresh_for_target("user/1")["refreshed"] == 1
    finally:
        KNOWN["user/1"] = {"key": "user/1", "name": "alice"}
    with db.get_session(Metric) as session:
        assert session.exec(select(Metric)).one().author["name"] == "alice@renamed"

    verb, _ = module.delete(instance.viur_key, skey="csrf")
    assert verb == "deleteSuccess"


# --- migrations --------------------------------------------------------------

def test_resolve_url_picks_the_named_engine(two_databases):
    assert migrations.resolve_url(x_args={"db": "analytics"}) == "sqlite://"
    assert migrations.resolve_url(x_args={"url": "sqlite:///x.db", "db": "analytics"}) == "sqlite:///x.db"


def test_resolve_url_falls_back_to_the_conf_entry(monkeypatch):
    db.reset()
    install_config().databases["analytics"] = {"engine": "sqlite", "sqlite_file": "a.sqlite3"}
    monkeypatch.setenv(migrations.DSN_ENV_VAR, "sqlite:///env.db")
    try:
        assert migrations.resolve_url(x_args={"db": "analytics"}) == "sqlite:///a.sqlite3"
        assert migrations.resolve_url() == "sqlite:///env.db"
    finally:
        install_config().databases.clear()


def test_resolve_url_without_conf_names_the_database(monkeypatch):
    db.reset()
    monkeypatch.setattr(migrations, "_models_conf", lambda: None)
    with pytest.raises(RuntimeError, match="No engine 'analytics'"):
        migrations.resolve_url(x_args={"db": "analytics"})


def test_include_object_for_restricts_to_the_database():
    include = migrations.include_object_for("analytics")
    assert include(None, "viur_models_test_metric", "table", False, None) is True
    assert include(None, "viur_models_test_localnote", "table", False, None) is False
    assert include(None, "alembic_version", "table", False, None) is False
    assert include(None, "anything", "column", False, None) is True
