"""BigQuery backend — id generation, model base, engine workarounds, LIKE.

The full CRUD path (SQLList over the BigQuery dialect) was verified
end-to-end against the BigQuery emulator; here the backend-specific pieces
are pinned without needing the emulator or the ``sqlalchemy-bigquery``
dialect: everything below runs on fakes and SQLite.
"""
import logging

import pytest
import sqlalchemy as sa

from viur.models import Field, db, install_config
from viur.models import bigquery
from viur.models.bigquery import BigQueryModel, apply_engine_workarounds, new_id


# --------------------------------------------------------------------------- #
# id generation                                                               #
# --------------------------------------------------------------------------- #

def test_new_id_shape():
    generated = new_id()
    assert len(generated) == 26
    assert set(generated) <= set(bigquery.CROCKFORD)


def test_new_id_is_time_ordered():
    """The whole point of the ULID layout: ORDER BY id ≈ insertion order,
    so SQLList's default ordering and the keyset tiebreaker stay
    meaningful without an autoincrement column."""
    early = new_id(_now_ms=lambda: 1_000_000)
    late = new_id(_now_ms=lambda: 2_000_000)
    assert early < late


def test_new_id_is_unique_within_one_millisecond():
    fixed = lambda: 1_234_567  # noqa: E731
    assert len({new_id(_now_ms=fixed) for _ in range(200)}) == 200


def test_new_id_never_returns_an_all_digit_key(monkeypatch):
    """``viur_parse_key`` turns an all-digit key into an ``int`` primary
    key, which would then miss the STRING column — the generator
    regenerates instead. Forced here by stubbing the randomness."""
    padded = [bytes(10), b"\xff" * 10]  # 1st candidate: digits only ("000…")

    def fake_urandom(n):
        return padded.pop(0)

    monkeypatch.setattr(bigquery.os, "urandom", fake_urandom)
    generated = new_id(_now_ms=lambda: 0)  # time part "0000000000"
    assert not generated.isdigit()
    assert padded == []  # both candidates consumed → it did regenerate


def test_encode_base32_is_lexically_sortable():
    values = [0, 1, 31, 32, 1_000_000, 2**48 - 1]
    encoded = [bigquery._encode_base32(value, 10) for value in values]
    assert encoded == sorted(encoded)
    assert all(len(item) == 10 for item in encoded)


# --------------------------------------------------------------------------- #
# the model base                                                              #
# --------------------------------------------------------------------------- #

class BQThing(BigQueryModel, table=True):
    __tablename__ = "bq_unit_thing"
    name: str = Field(descr="Name", max_length=40)


def test_model_generates_string_ids():
    a, b = BQThing(name="x"), BQThing(name="y")
    assert isinstance(a.id, str) and len(a.id) == 26
    assert a.id != b.id


def test_model_key_roundtrip():
    thing = BQThing(name="x")
    assert BQThing.viur_parse_key(thing.viur_key) == thing.id


def test_model_system_datetimes_are_timestamps():
    """BigQuery's DATETIME is naive; the aware system values need TIMESTAMP —
    inserting them into DATETIME fails on the real backend."""
    for column in ("creationdate", "changedate"):
        col_type = BQThing.__table__.c[column].type
        assert isinstance(col_type, sa.TIMESTAMP)
        assert col_type.timezone is True


def test_model_structure_stays_skeleton_compatible():
    structure = BQThing.viur_structure()
    assert structure["key"]["type"] == "key"
    assert structure["name"]["type"] == "str"
    assert structure["creationdate"]["readonly"] is True


def test_model_disables_delete_rowcount_confirmation():
    """DML jobs do not report matched rows — without this every working
    DELETE logs a spurious 0-rows SAWarning."""
    assert BQThing.__mapper__.confirm_deleted_rows is False


# --------------------------------------------------------------------------- #
# engine workarounds                                                          #
# --------------------------------------------------------------------------- #

class _FakeDialect:
    name = "bigquery"
    supports_sane_rowcount = True
    supports_sane_multi_rowcount = True


class _FakeEngine:
    def __init__(self):
        self.dialect = _FakeDialect()

    def dispose(self):
        pass


def test_apply_engine_workarounds_flips_the_rowcount_flags():
    engine = _FakeEngine()
    notes = apply_engine_workarounds(engine)

    assert engine.dialect.supports_sane_rowcount is False
    assert engine.dialect.supports_sane_multi_rowcount is False
    assert any("rowcount" in note for note in notes)
    assert any("NO-OPS" in note for note in notes)  # the honesty note, always


def test_apply_engine_workarounds_is_idempotent():
    engine = _FakeEngine()
    apply_engine_workarounds(engine)
    notes = apply_engine_workarounds(engine)
    assert not any("rowcount" in note for note in notes)  # nothing left to flip
    assert any("NO-OPS" in note for note in notes)


def test_db_configure_applies_the_workarounds_and_warns(caplog):
    engine = _FakeEngine()
    with caplog.at_level(logging.WARNING, logger="viur.models.db"):
        db.configure(engine)
    db.reset()

    assert engine.dialect.supports_sane_rowcount is False
    assert any("NO-OPS" in record.message for record in caplog.records)


def test_db_configure_leaves_other_dialects_alone():
    engine = db.configure("sqlite://")
    assert engine.dialect.supports_sane_rowcount is True
    db.reset()


# --------------------------------------------------------------------------- #
# preset                                                                      #
# --------------------------------------------------------------------------- #

def test_url_from_preset_bigquery():
    assert db.url_from_preset("bigquery", bigquery_dsn="bigquery://p/d") \
        == "bigquery://p/d"


def test_url_from_preset_bigquery_requires_a_dsn():
    with pytest.raises(RuntimeError, match="bigquery_dsn"):
        db.url_from_preset("bigquery")


def test_url_from_conf_passes_the_bigquery_dsn():
    cfg = install_config()
    cfg.databases["default"] = {"engine": "bigquery", "bigquery_dsn": "bigquery://proj/ds"}
    try:
        assert db.url_from_conf() == "bigquery://proj/ds"
    finally:
        cfg.databases.pop("default")


# --------------------------------------------------------------------------- #
# dialect-aware LIKE                                                          #
# --------------------------------------------------------------------------- #

def test_ilike_uses_an_escape_clause_on_ordinary_backends():
    from viur.models.sqllist import _ilike

    db.configure("sqlite://")
    try:
        expression = _ilike(BQThing.__table__.c.name, r"al\%pha%", BQThing)
        assert expression.modifiers.get("escape") == "\\"
    finally:
        db.reset()


def test_ilike_omits_the_escape_clause_on_bigquery():
    """BigQuery's LIKE has no ESCAPE clause — backslash is its implicit
    escape, and passing ``escape=`` is a syntax error there."""
    from viur.models.sqllist import _ilike

    db.configure(_FakeEngine())
    try:
        expression = _ilike(BQThing.__table__.c.name, r"al\%pha%", BQThing)
        assert expression.modifiers.get("escape") is None
    finally:
        db.reset()
