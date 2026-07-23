"""``@computed_field`` — the ``compute`` (method ``Always``) analogue:
read-only bones derived from the instance at dump time, never stored."""
from pydantic import computed_field
from sqlmodel import Relationship

from viur.models import Password, Text, ViURField, ViURModel, ViURRecord


class Ticket(ViURModel):
    name: str = ViURField(default="", required=False, descr="Name")
    prio: int = ViURField(default=0, descr="Prio")

    @computed_field(json_schema_extra={"viur": {
        "descr": "Anzeige", "params": {"tooltip": "derived"},
    }})
    @property
    def display(self) -> str:
        return f"{self.name} ({self.prio})"

    @computed_field(title="Als Text")  # semantic return type drives the bone
    @property
    def body(self) -> Text:
        return self.name * 2

    @computed_field  # write-only return type keeps masking dumps
    @property
    def masked(self) -> Password:
        return "s3cret!"

    @computed_field  # int arithmetic — crashes over raw client strings
    @property
    def boosted(self) -> int:
        return self.prio + 1


STRUCTURE = Ticket.viur_structure()


def test_computed_bone_is_readonly_always_compute():
    bone = STRUCTURE["display"]
    assert bone["type"] == "str"
    assert (bone["readonly"], bone["required"], bone["indexed"]) == (True, False, False)
    assert bone["compute"] == {"method": "Always"}
    # bone parameters travel in json_schema_extra={"viur": {...}}
    assert bone["descr"] == "Anzeige"
    assert bone["params"] == {"tooltip": "derived"}


def test_computed_semantic_type_and_descr_fallbacks():
    assert STRUCTURE["body"]["type"] == "text"
    assert STRUCTURE["body"]["descr"] == "Als Text"  # decorator title
    assert STRUCTURE["masked"]["descr"] == "Masked"  # field-name fallback


def test_computed_bones_sort_after_stored_fields():
    assert list(STRUCTURE)[-4:] == ["display", "body", "masked", "boosted"]


def test_computed_values_dump_and_write_only_masks():
    dump = Ticket(name="A", prio=2).viur_dump()
    assert dump["display"] == "A (2)"
    assert dump["body"] == "AA"
    assert dump["masked"] == STRUCTURE["masked"]["emptyvalue"]  # never leaks
    assert dump["boosted"] == 3


def test_computed_input_is_dropped_like_readonly_bones():
    instance, errors = Ticket.viur_from_client(
        {"name": "B", "display": "HACK", "masked": "x"},
    )
    assert errors == []
    assert instance.display == "B (0)"


def test_computed_degrades_to_emptyvalue_on_rejected_forms():
    """Computed fields run user code over RAW values on a best-effort form
    (rejected re-render) — a crash degrades to the bone's emptyvalue, a
    working computed still dumps. Valid instances keep raising (bugs stay
    visible — see test_computed_values_dump_and_write_only_masks)."""
    form, errors = Ticket.viur_from_client({"name": "B", "prio": "oops"})
    assert errors
    dump = form.viur_dump()
    assert dump["display"] == "B (oops)"  # raw values that work still dump
    assert dump["boosted"] == 0           # "oops" + 1 crashes → emptyvalue


# --------------------------------------------------------------------------- #
# records                                                                      #
# --------------------------------------------------------------------------- #

class Score(ViURRecord):
    points: int = ViURField(default=0, descr="Punkte")

    @computed_field
    @property
    def label(self) -> str:
        return f"{self.points}p"


class Match(ViURModel):
    score: Score | None = ViURField(default=None, descr="Score")


def test_record_using_and_dump_include_computed():
    using = Match.viur_structure()["score"]["using"]
    assert using["label"]["readonly"] is True
    assert using["label"]["compute"] == {"method": "Always"}
    assert Match(score=Score(points=3)).viur_dump()["score"]["label"] == "3p"


# --------------------------------------------------------------------------- #
# relations — computed names work as viur_ref_keys                            #
# --------------------------------------------------------------------------- #

class ComputedAuthor(ViURModel, table=True):
    __tablename__ = "viur_models_test_computed_author"

    viur_ref_keys = ("name", "handle")

    name: str = ViURField(default="", required=False)

    @computed_field
    @property
    def handle(self) -> str:
        return f"@{self.name.lower()}"


class ComputedPost(ViURModel, table=True):
    __tablename__ = "viur_models_test_computed_post"

    title: str = ViURField(default="", required=False)
    author_id: int | None = ViURField(
        default=None, foreign_key="viur_models_test_computed_author.id",
    )
    author: ComputedAuthor | None = Relationship()


def test_relskel_and_dest_include_computed_ref_keys():
    relskel = ComputedPost.viur_structure()["author"]["relskel"]
    assert set(relskel) == {"key", "name", "handle", "shortkey"}

    post = ComputedPost(title="t", author_id=1)
    post.author = ComputedAuthor(id=1, name="Ada")
    dest = post.viur_dump()["author"]["dest"]
    assert (dest["name"], dest["handle"]) == ("Ada", "@ada")
