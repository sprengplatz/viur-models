"""BigQuery backend: string ids, ``TIMESTAMP`` system fields, dialect workarounds.
Extra ``spltz-viur-models[bigquery]``; constraints in ``docs/bigquery.md``."""
import os
import time
import typing as t
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlmodel import Field as SQLModelField

from .base import Model
from .fields import Field

#: Crockford base32 (no I, L, O, U); lexical order == numeric order.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_id(*, _now_ms: t.Callable[[], int] | None = None) -> str:
    """ULID-style id: 48-bit ms timestamp + 80 random bits, 26 chars Crockford base32.
    Never all digits — ``viur_parse_key`` would read those as an int key."""
    now_ms = _now_ms or (lambda: time.time_ns() // 1_000_000)
    while True:
        candidate = (
            _encode_base32(now_ms() & (2**48 - 1), 10)
            + _encode_base32(int.from_bytes(os.urandom(10)), 16)
        )
        if not candidate.isdigit():
            return candidate


class BigQueryModel(Model):
    """``Model`` with a client-generated string ``id`` and ``TIMESTAMP`` system datetimes."""

    __mapper_args__ = {"confirm_deleted_rows": False}

    id: str | None = SQLModelField(default_factory=new_id, primary_key=True)
    creationdate: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=sa.TIMESTAMP(timezone=True),
        readonly=True, visible=False,
        descr="created at", compute={"method": "Once"},
    )
    changedate: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=sa.TIMESTAMP(timezone=True),
        readonly=True, visible=False,
        descr="updated at", compute={"method": "OnWrite"},
    )


def apply_engine_workarounds(engine: t.Any) -> list[str]:
    """Clear the rowcount capabilities BigQuery DML cannot satisfy; returns log notes."""
    notes = []
    if engine.dialect.supports_sane_rowcount or engine.dialect.supports_sane_multi_rowcount:
        engine.dialect.supports_sane_rowcount = False
        engine.dialect.supports_sane_multi_rowcount = False
        notes.append("rowcount checks disabled (DML jobs do not report matched rows)")
    notes.append(
        "transactions are NO-OPS on BigQuery — session.rollback() cannot undo "
        "anything; a failed action may leave earlier statements applied"
    )
    return notes
