"""Built-in bone types — every alias emits its bone's type string and the
pinned structure extras (verified against viur-core in the integration
suite; this pins them without the real core)."""
import pytest

from viur.models import (
    Code,
    Color,
    Credential,
    Json,
    Phone,
    Raw,
    SortIndex,
    Uid,
    Uri,
    ViURField,
    ViURModel,
)
from viur.models.types import PHONE_TEST_PATTERN


class TypesModel(ViURModel):
    raw: Raw = ViURField(default="", required=False)
    code: Code = ViURField(default="", required=False)
    color: Color = ViURField(default="", required=False)
    phone: Phone = ViURField(default="", required=False, max_length=15)
    uri: Uri = ViURField(default="", required=False)
    uid: Uid = ViURField(default="", required=False, readonly=True)
    sortindex: SortIndex = ViURField(default=0.0, required=False)
    data: Json = ViURField(default_factory=dict, required=False)
    secret: Credential = ViURField(default="", required=False, visible=False)


STRUCTURE = TypesModel.viur_structure()


@pytest.mark.parametrize("field,type_string", [
    ("raw", "raw"),
    ("code", "raw.code"),
    ("color", "color"),
    ("phone", "str.phone"),
    ("uri", "uri"),
    ("uid", "uid"),
    ("sortindex", "numeric.sortindex"),
    ("data", "raw.json"),
    ("secret", "str.credential"),
])
def test_type_strings(field, type_string):
    assert STRUCTURE[field]["type"] == type_string


def test_pinned_extras():
    assert STRUCTURE["phone"]["test"] == PHONE_TEST_PATTERN
    assert STRUCTURE["phone"]["maxlength"] == 15  # str extras stay (refining type)
    assert STRUCTURE["uri"]["local_path_allowed"] is False
    assert (STRUCTURE["uid"]["fillchar"], STRUCTURE["uid"]["length"]) == ("*", 13)
    assert (STRUCTURE["sortindex"]["precision"], STRUCTURE["sortindex"]["decimal"]) == (8, False)
    assert STRUCTURE["data"]["schema"] == {}
    assert STRUCTURE["secret"]["maxlength"] is None  # credential overrides str default
    assert "maxlength" not in STRUCTURE["raw"]  # replace=True drops str extras