"""RecordBone mapping — plain pydantic nesting + RecordJSON storage.

A nested (non-table) SQLModel is the ``using``-skel analogue: validation,
error paths and the dump shape (the plain values dict) come natively from
pydantic; the structure entry (``type: record`` + ``using``) and the JSON
column glue (``RecordJSON``) are the only viur-models additions.
"""
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine, select

from viur.models import RecordJSON, ViURField, ViURModel, ViURRecord, db
from viur.models.sqllist import SQLList

from tests.test_sqllist import RecordingRender


class Address(ViURRecord):  # the RelSkel analogue — no system fields
    street: str = ViURField(descr="Straße", max_length=100)
    zip_code: int | None = ViURField(default=None, descr="PLZ")


class Delivery(ViURModel, table=True):
    __tablename__ = "viur_models_test_delivery"
    name: str = ViURField(default="", required=False)
    address: Address | None = ViURField(
        default=None, sa_type=RecordJSON(Address), descr="Adresse", format="$(street)",
    )
    stops: list[Address] = ViURField(
        default_factory=list, sa_type=RecordJSON(Address), required=False, descr="Stationen",
    )


class DeliveryModule(SQLList):
    model = Delivery

    def __init__(self):
        super().__init__("deliveries", "/deliveries")
        self.render = RecordingRender()

    def can(self, instance):
        return True


@pytest.fixture()
def module():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db.configure(engine)
    yield DeliveryModule()
    db.reset()


# --------------------------------------------------------------------------- #
# structure                                                                    #
# --------------------------------------------------------------------------- #

def test_record_structure():
    structure = Delivery.viur_structure()
    address = structure["address"]
    assert (address["type"], address["multiple"], address["format"]) == (
        "record", False, "$(street)",
    )
    assert address["emptyvalue"] is None
    assert set(address["using"]) == {"street", "zip_code"}
    assert address["using"]["street"]["maxlength"] == 100

    stops = structure["stops"]
    assert (stops["multiple"], stops["defaultvalue"], stops["format"]) == (True, [], None)


def test_table_models_are_not_records():
    from tests.test_relations import Post  # a table model

    with pytest.raises(TypeError, match="Relationship"):
        class Broken(ViURModel):
            post: Post | None = ViURField(default=None)


# --------------------------------------------------------------------------- #
# dump — native nesting                                                        #
# --------------------------------------------------------------------------- #

def test_record_dump_is_the_plain_values_dict():
    delivery = Delivery(
        id=1, name="x",
        address=Address(street="Hauptweg 1", zip_code=12345),
        stops=[Address(street="A"), Address(street="B", zip_code=1)],
    )
    dump = delivery.viur_dump()
    assert dump["address"] == {"street": "Hauptweg 1", "zip_code": 12345}
    assert dump["stops"] == [
        {"street": "A", "zip_code": None},
        {"street": "B", "zip_code": 1},
    ]
    assert Delivery(id=2).viur_dump()["address"] is None


# --------------------------------------------------------------------------- #
# from_client — native validation                                              #
# --------------------------------------------------------------------------- #

def test_from_client_validates_nested_dicts():
    instance, errors = Delivery.viur_from_client(
        {"address": {"street": "Hauptweg 1", "zip_code": "12345"}},
    )
    assert errors == []
    assert instance.address.zip_code == 12345  # nested coercion, natively

    instance, errors = Delivery.viur_from_client(
        {"stops": [{"street": "A"}, {"street": "B"}]},
    )
    assert errors == [] and len(instance.stops) == 2


def test_empty_submissions_clear_records_and_multiples():
    # "" on record bones clears: multiple → [] (browsers post "" for an
    # emptied list), single → None
    instance, errors = Delivery.viur_from_client(
        {"name": "x", "stops": "", "address": ""},
    )
    assert errors == []
    assert instance.stops == [] and instance.address is None

    # empty values INSIDE a record clear like top-level bones; stray
    # sub-keys are ignored (pydantic drops them)
    instance, errors = Delivery.viur_from_client(
        {"name": "x", "address": {"street": "Gasse", "zip_code": "", "extra": ""}},
    )
    assert errors == []
    assert (instance.address.street, instance.address.zip_code) == ("Gasse", None)

    # … per item in multiple records too
    instance, errors = Delivery.viur_from_client(
        {"name": "x", "stops": [{"street": "A", "zip_code": ""}]},
    )
    assert errors == []
    assert instance.stops[0].zip_code is None


def test_from_client_accepts_indexed_dotted_multiple_records():
    # vi/admin4 posts multiple records as ``stops.<idx>.<field>=…``
    instance, errors = Delivery.viur_from_client({
        "stops.0.street": "A", "stops.0.zip_code": "1",
        "stops.1.street": "B", "stops.1.zip_code": "",  # empty → default
    })
    assert errors == []
    assert [(s.street, s.zip_code) for s in instance.stops] == [("A", 1), ("B", None)]


def test_from_client_nested_errors_carry_the_full_path():
    _, errors = Delivery.viur_from_client({"address": {"zip_code": "abc"}})
    paths = {tuple(error.fieldPath) for error in errors}
    assert ("address", "street") in paths      # missing required nested field
    assert ("address", "zip_code") in paths    # invalid nested value


def test_from_client_accepts_dotted_record_input():
    instance, errors = Delivery.viur_from_client(
        {"address.street": "Hauptweg 1", "address.zip_code": "12345"},
    )
    assert errors == []
    assert (instance.address.street, instance.address.zip_code) == ("Hauptweg 1", 12345)


def test_plain_sqlmodel_still_works_as_record():
    class Plain(SQLModel):
        note: str = ViURField(default="", required=False)

    class Wrapper(ViURModel):
        extra: Plain | None = ViURField(default=None)

    assert Wrapper.viur_structure()["extra"]["type"] == "record"


def test_viur_record_structure_is_cached_and_fails_fast():
    structure = Address.viur_structure()
    assert Address.viur_structure() is structure
    assert "key" not in structure  # no system fields on records

    import pytest as _pytest
    with _pytest.raises(TypeError, match="no bone mapping"):
        class BrokenRecord(ViURRecord):
            blob: dict = ViURField(default=None)


def test_recordjson_none_passthrough():
    # SQLAlchemy short-circuits NULLs in practice; the decorator stays
    # defensive for direct use.
    column = RecordJSON(Address)
    assert column.process_bind_param(None, None) is None
    assert column.process_result_value(None, None) is None


# --------------------------------------------------------------------------- #
# SQLList roundtrip (RecordJSON storage)                                       #
# --------------------------------------------------------------------------- #

def test_records_roundtrip_through_the_database(module):
    verb, created = module.add(
        name="Tour", skey="csrf",
        address={"street": "Hauptweg 1", "zip_code": "12345"},
        stops=[{"street": "A"}, {"street": "B"}],
    )
    assert verb == "addSuccess"

    with db.get_session() as session:
        stored = session.exec(select(Delivery)).one()
    assert isinstance(stored.address, Address)          # validated back on read
    assert stored.address.street == "Hauptweg 1"
    assert [stop.street for stop in stored.stops] == ["A", "B"]

    # merge keeps the record when unsubmitted; nested dump shape roundtrips
    verb, edited = module.edit(created.viur_key, name="Tour 2")
    assert verb == "editSuccess"
    assert edited.viur_dump()["address"]["street"] == "Hauptweg 1"