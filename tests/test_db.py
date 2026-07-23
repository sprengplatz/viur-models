"""Session lifecycle — configure guard, commit/rollback semantics."""
import pytest
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import SQLModel, create_engine, select

from viur.models import ViURField, ViURModel
from viur.models import db


class DBProbe(ViURModel, table=True):
    __tablename__ = "viur_models_test_dbprobe"
    name: str = ViURField(default="", required=False)


@pytest.fixture(autouse=True)
def _clean_engine():
    yield
    db.reset()


def _memory_engine():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_get_session_without_configure_fails_fast():
    db.reset()
    with pytest.raises(RuntimeError, match="configure"):
        with db.get_session():
            pass  # pragma: no cover


def test_configure_accepts_ready_made_engine():
    engine = _memory_engine()
    assert db.configure(engine) is engine
    assert db.get_engine() is engine


def test_configure_url_defaults_to_nullpool():
    engine = db.configure("sqlite://")
    assert isinstance(engine.pool, NullPool)


def test_session_commits_on_success():
    db.configure(_memory_engine())
    with db.get_session() as session:
        session.add(DBProbe(name="kept"))
    with db.get_session() as session:
        names = [row.name for row in session.exec(select(DBProbe)).all()]
    assert names == ["kept"]


def test_session_rolls_back_on_error():
    db.configure(_memory_engine())
    with pytest.raises(RuntimeError, match="boom"):
        with db.get_session() as session:
            session.add(DBProbe(name="dropped"))
            raise RuntimeError("boom")
    with db.get_session() as session:
        assert session.exec(select(DBProbe)).all() == []


def test_instances_stay_readable_after_session_close():
    db.configure(_memory_engine())
    with db.get_session() as session:
        probe = DBProbe(name="detached")
        session.add(probe)
    assert (probe.id, probe.name) == (1, "detached")  # expire_on_commit=False
