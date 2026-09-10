"""conf.models presets — install_config + configure_from_conf."""
import pytest
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import SQLModel, select

from viur.core import conf
from viur.models import ModelsConfig, Field, Model, install_config
from viur.models import db


class ConfProbe(Model, table=True):
    __tablename__ = "viur_models_test_confprobe"
    name: str = Field(default="", required=False)


@pytest.fixture(autouse=True)
def _clean_conf_and_engine():
    if hasattr(conf, "models"):
        delattr(conf, "models")
    yield
    if hasattr(conf, "models"):
        delattr(conf, "models")
    db.reset()


def test_install_config_attaches_namespace():
    cfg = install_config()
    assert conf.models is cfg
    assert isinstance(cfg, ModelsConfig)
    assert cfg.databases == {}


def test_install_config_is_idempotent():
    cfg = install_config()
    cfg.databases["default"] = {"engine": "memory"}
    assert install_config() is cfg
    assert conf.models.databases == {"default": {"engine": "memory"}}


def test_install_config_replaces_foreign_attribute():
    conf.models = object()  # something else squatting on the name
    cfg = install_config()
    assert isinstance(cfg, ModelsConfig)
    assert conf.models is cfg


def test_databases_not_shared_between_instances():
    first = ModelsConfig()
    first.databases["default"] = {"engine": "memory"}
    assert ModelsConfig().databases == {}


def test_unconfigured_default_fails_fast():
    install_config()
    with pytest.raises(RuntimeError, match="no entry 'default'"):
        db.configure_from_conf()
    install_config().databases["default"] = {}
    with pytest.raises(RuntimeError, match="conf.models.databases"):
        db.configure_from_conf()


def test_unknown_preset_fails_fast():
    install_config().databases["default"] = {"engine": "oracle"}
    with pytest.raises(RuntimeError, match="'oracle'"):
        db.configure_from_conf()


def test_memory_preset_shares_one_database_across_sessions():
    install_config().databases["default"] = {"engine": "memory"}
    engine = db.configure_from_conf()
    assert engine.url.render_as_string() == "sqlite://"
    assert isinstance(engine.pool, StaticPool)
    SQLModel.metadata.create_all(engine)
    with db.get_session() as session:
        session.add(ConfProbe(name="kept"))
    with db.get_session() as session:
        names = [row.name for row in session.exec(select(ConfProbe)).all()]
    assert names == ["kept"]


def test_memory_preset_respects_engine_option_overrides():
    install_config().databases["default"] = {"engine": "memory", "engine_options": {"poolclass": NullPool}}
    engine = db.configure_from_conf()
    assert isinstance(engine.pool, NullPool)


def test_sqlite_preset_uses_configured_file(tmp_path):
    install_config().databases["default"] = {"engine": "sqlite", "sqlite_file": str(tmp_path / "probe.sqlite3")}
    engine = db.configure_from_conf()
    assert engine.url.database == str(tmp_path / "probe.sqlite3")
    assert engine.url.drivername == "sqlite"
    assert isinstance(engine.pool, NullPool)  # configure()'s serverless default


def test_postgres_preset_requires_dsn():
    install_config().databases["default"] = {"engine": "postgres"}
    with pytest.raises(RuntimeError, match="postgres_dsn"):
        db.configure_from_conf()


def test_postgres_preset_passes_dsn_and_options_through(monkeypatch):
    # No postgres driver in the unit-test env — capture the configure()
    # call instead of building a real engine.
    calls = {}

    def fake_configure(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return "engine-sentinel"

    monkeypatch.setattr(db, "configure", fake_configure)
    install_config().databases["default"] = {
        "engine": "postgres", "postgres_dsn": "postgresql+pg8000://user:pw@10.0.0.1:5432/app",
        "engine_options": {"creator": _fake_creator, "echo": True},
    }
    assert db.configure_from_conf() == "engine-sentinel"
    assert calls["url"] == "postgresql+pg8000://user:pw@10.0.0.1:5432/app"
    assert calls["kwargs"] == {"creator": _fake_creator, "echo": True, "name": "default"}


def _fake_creator():  # pragma: no cover - never called, only passed through
    raise AssertionError("must not connect")
