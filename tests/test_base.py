"""ViURModel — key encoding and kind derivation."""
from viur.models import ViURField, ViURModel


class Article(ViURModel, table=True):
    __tablename__ = "viur_models_test_article"
    name: str = ViURField(default="", required=False)


class Draft(ViURModel):  # no table — kind falls back to the class name
    name: str = ViURField(default="", required=False)


def test_key_roundtrip():
    article = Article(id=42)
    key = article.viur_key
    assert key and "=" not in key and "/" not in key.replace("_", "")
    assert Article.viur_parse_key(key) == 42


def test_key_is_none_without_id():
    assert Article().viur_key is None


def test_parse_key_rejects_foreign_and_malformed_keys():
    assert Article.viur_parse_key(Draft(id=1).viur_key) is None  # wrong kind
    assert Article.viur_parse_key("&&& not base64 &&&") is None
    assert Article.viur_parse_key("") is None
    assert Article.viur_parse_key("aGFsbG8") is None  # valid b64, no separator
    # separator present but empty primary key
    import base64
    empty = base64.urlsafe_b64encode(b"viur_models_test_article\x1f").decode().rstrip("=")
    assert Article.viur_parse_key(empty) is None


def test_non_numeric_primary_keys_pass_through_as_str():
    import base64
    raw = base64.urlsafe_b64encode(b"viur_models_test_article\x1fabc-uuid").decode().rstrip("=")
    assert Article.viur_parse_key(raw) == "abc-uuid"


def test_kind_fallback_without_table():
    assert Draft(id=7).viur_key is not None
    assert Draft.viur_parse_key(Draft(id=7).viur_key) == 7
