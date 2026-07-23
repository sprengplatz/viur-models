"""viur_dump / viur_from_client — value shapes and error mapping.

Runs against viur-light-mock's ``viur.core`` stand-ins (pytest plugin);
the integration suite repeats the comparisons against the real core.
"""
import datetime
import decimal
import enum

from viur.core.bones.base import ReadFromClientErrorSeverity

from viur.models import ViURField, ViURModel


class Mood(enum.Enum):
    GOOD = "good"
    BAD = "bad"


class Entry(ViURModel):
    name: str = ViURField(descr="Name", max_length=20)
    rating: int | None = ViURField(default=None, ge=1, le=5)
    price: decimal.Decimal | None = ViURField(default=None)
    mood: Mood | None = ViURField(default=None)
    due: datetime.datetime | None = ViURField(default=None)
    day: datetime.date | None = ViURField(default=None)


def test_dump_shapes():
    entry = Entry(
        id=1, name="x", rating=3, price=decimal.Decimal("9.5"), mood=Mood.GOOD,
        due=datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.timezone.utc),
        day=datetime.date(2026, 7, 15),
    )
    dump = entry.viur_dump()
    assert dump["key"] == entry.viur_key
    assert "id" not in dump  # raw primary key never leaves the model
    assert (dump["name"], dump["rating"]) == ("x", 3)
    assert dump["price"] == 9.5  # Decimal -> float
    assert dump["mood"] == "good"  # enum -> value
    assert dump["due"] == "2026-07-15T12:00:00+00:00"  # datetime -> isoformat
    assert dump["day"] == "2026-07-15"  # plain dates have no tz to attach
    assert isinstance(dump["creationdate"], str)
    # naive datetimes (tz-less backends like SQLite) dump as UTC
    naive = Entry(name="x", due=datetime.datetime(2026, 7, 15, 12, 0))
    assert naive.viur_dump()["due"] == "2026-07-15T12:00:00+00:00"


def test_dump_bones_filter():
    dump = Entry(name="x").viur_dump(bones=("name", "rating"))
    assert set(dump) == {"name", "rating"}
    assert dump["rating"] is None


def test_renderable_protocol_aliases():
    entry = Entry(name="x")
    assert entry.dump(bones=("name",)) == {"name": "x"}
    assert entry.structure() is Entry.viur_structure()


def test_from_client_accepts_and_filters():
    instance, errors = Entry.viur_from_client({
        "name": "ok",
        "rating": "4",             # lax coercion, like bones parse form strings
        "key": "forged",           # readonly -> dropped
        "creationdate": "nope",    # readonly -> dropped
        "unknown": 1,              # not in structure -> dropped
    })
    assert errors == []
    assert (instance.name, instance.rating) == ("ok", 4)
    assert instance.creationdate.tzinfo is not None  # default_factory, not client value


def test_empty_submission_clears_non_string_bones():
    # HTML forms send cleared inputs as "" — non-string bones treat that as
    # an empty submission and clear to None; string bones keep "" (it IS
    # their emptyvalue)
    instance, errors = Entry.viur_from_client({
        "name": "", "rating": "", "price": "", "mood": "", "due": "", "day": "",
    })
    assert errors == []
    assert instance.name == ""
    assert (instance.rating, instance.price, instance.mood) == (None, None, None)
    assert (instance.due, instance.day) == (None, None)


def test_from_client_error_mapping():
    instance, errors = Entry.viur_from_client({"rating": 99})
    # best-effort form: carries the submitted value for the re-render
    assert instance is not None and instance.rating == 99
    by_path = {tuple(error.fieldPath): error for error in errors}
    # not submitted -> NotSet ("Field not submitted"), like the real bones
    assert by_path[("name",)].severity is ReadFromClientErrorSeverity.NotSet
    assert by_path[("rating",)].severity is ReadFromClientErrorSeverity.Invalid
    assert by_path[("rating",)].errorMessage
