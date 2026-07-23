"""ViURField — metadata transport and schema_extra whitelist."""
import pytest
from sqlmodel import SQLModel

from viur.models import VIUR_META_KEY, ViURField


class FieldsModel(SQLModel):
    name: str = ViURField(descr="Name", params={"tooltip": "hint"})
    code: str = ViURField(
        default="",
        required=False,
        schema_extra={"pattern": "^[a-z]+$", "json_schema_extra": {"custom": 1}},
    )


def test_viur_meta_lands_in_json_schema_extra():
    extra = FieldsModel.model_fields["name"].json_schema_extra
    assert extra[VIUR_META_KEY] == {
        "descr": "Name",
        "visible": True,
        "readonly": False,
        "params": {"tooltip": "hint"},
    }


def test_schema_extra_passthrough_merges_with_user_json_schema_extra():
    field_info = FieldsModel.model_fields["code"]
    assert field_info.json_schema_extra["custom"] == 1
    assert field_info.json_schema_extra[VIUR_META_KEY]["required"] is False
    # pattern reached pydantic's validation metadata
    assert any(getattr(m, "pattern", None) == "^[a-z]+$" for m in field_info.metadata)


def test_unknown_schema_extra_key_raises():
    with pytest.raises(TypeError, match="json_shema_extra|Unknown schema_extra"):
        ViURField(schema_extra={"json_shema_extra": {}})  # typo must not vanish
