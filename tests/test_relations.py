"""Relational mapping (analysis/01 §5.4) — structure, dump, input, SQLList.

To-one relations: the FK field + ``Relationship()`` emit ONE
``relational.<kind>`` bone under the relationship's name; bone parameters
come from the FK field's ViURField metadata; ``dest`` payloads carry
``key`` + the target's ``viur_ref_keys``.
"""
import typing as t

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Relationship, SQLModel, create_engine

from viur.models import ViURField, ViURModel, db
from viur.models.sqllist import SQLList

from tests.test_sqllist import RecordingRender


class Author(ViURModel, table=True):
    __tablename__ = "viur_models_test_author"
    name: str = ViURField(default="", required=False)
    posts: list["Post"] = Relationship(back_populates="author")  # to-many: no bone


class Fan(ViURModel, table=True):  # m2m target WITH ref keys (name)
    __tablename__ = "viur_models_test_fan"
    name: str = ViURField(default="", required=False)


class PostFanLink(SQLModel, table=True):  # plain link table, no ViURModel
    __tablename__ = "viur_models_test_postfan"
    post_id: int | None = Field(
        default=None, foreign_key="viur_models_test_post.id", primary_key=True,
    )
    fan_id: int | None = Field(
        default=None, foreign_key="viur_models_test_fan.id", primary_key=True,
    )


class PostTagLink(SQLModel, table=True):
    __tablename__ = "viur_models_test_posttag"
    post_id: int | None = Field(
        default=None, foreign_key="viur_models_test_post.id", primary_key=True,
    )
    tag_id: int | None = Field(
        default=None, foreign_key="viur_models_test_tag.id", primary_key=True,
    )


class Post(ViURModel, table=True):
    __tablename__ = "viur_models_test_post"

    viur_relation_meta = {"tags": {"descr": "Schlagworte"}}

    title: str = ViURField(descr="Titel", max_length=100)
    author_id: int | None = ViURField(
        default=None, foreign_key="viur_models_test_author.id", descr="Autor",
    )
    author: Author | None = Relationship(back_populates="posts")
    fans: list[Fan] = Relationship(link_model=PostFanLink)          # multiple
    tags: list["Tag"] = Relationship(link_model=PostTagLink)        # multiple, key-only target


class StrictPost(ViURModel, table=True):
    __tablename__ = "viur_models_test_strictpost"
    author_id: int = ViURField(foreign_key="viur_models_test_author.id")
    author: Author = Relationship()


class Tag(ViURModel, table=True):  # no "name" field — ref keys reduce to "key"
    __tablename__ = "viur_models_test_tag"
    label: str = ViURField(default="", required=False)


class Tagged(ViURModel, table=True):
    __tablename__ = "viur_models_test_tagged"
    tag_id: int | None = ViURField(default=None, foreign_key="viur_models_test_tag.id")
    tag: Tag | None = Relationship()


class PostModule(SQLList):
    model = Post

    def __init__(self):
        super().__init__("posts", "/posts")
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
    yield PostModule()
    db.reset()


def _author(name="Alice"):
    with db.get_session() as session:
        author = Author(name=name)
        session.add(author)
    return author


# --------------------------------------------------------------------------- #
# structure                                                                    #
# --------------------------------------------------------------------------- #

def test_relation_emits_one_relational_bone():
    structure = Post.viur_structure()
    assert "author_id" not in structure  # consumed by the relationship
    bone = structure["author"]
    assert bone["type"] == "relational.viur_models_test_author"
    assert bone["descr"] == "Autor"  # from the FK field's ViURField
    assert (bone["module"], bone["format"], bone["using"]) == (
        "viur_models_test_author", "$(dest.name)", None,
    )
    assert (bone["required"], bone["multiple"], bone["emptyvalue"]) == (False, False, None)
    assert set(bone["relskel"]) == {"key", "shortkey", "name"}  # ref keys + system bones
    assert bone["sortindex"] == structure["title"]["sortindex"] + 1  # FK position


def test_non_nullable_fk_is_required():
    assert StrictPost.viur_structure()["author"]["required"] is True


def test_to_many_side_has_no_bone():
    assert "posts" not in Author.viur_structure()


class OneOwner(ViURModel, table=True):
    __tablename__ = "viur_models_test_oneowner"
    profile: t.Optional["Profile"] = Relationship(
        back_populates="owner", sa_relationship_kwargs={"uselist": False},
    )


class Profile(ViURModel, table=True):
    __tablename__ = "viur_models_test_profile"
    owner_id: int | None = ViURField(
        default=None, foreign_key="viur_models_test_oneowner.id",
    )
    owner: OneOwner | None = Relationship(back_populates="profile")


def test_fk_less_one_to_one_side_has_no_bone():
    assert "owner" in Profile.viur_structure()      # FK side maps
    assert "profile" not in OneOwner.viur_structure()  # FK-less side skipped


def test_relations_map_is_cached_per_class():
    relations = Post.viur_relations()
    assert Post.viur_relations() is relations
    assert relations["author"]["fk"] == "author_id"
    assert relations["author"]["target"] is Author
    assert Author.viur_relations() == {}


# --------------------------------------------------------------------------- #
# dump                                                                         #
# --------------------------------------------------------------------------- #

def test_dump_loaded_relation_carries_ref_keys(module):
    author = _author("Alice")
    post = Post(id=1, title="Hello", author_id=author.id, author=author)
    dumped = post.viur_dump()["author"]
    assert dumped == {"dest": {"key": author.viur_key, "name": "Alice"}, "rel": None}


def test_dump_unloaded_relation_falls_back_to_key_only():
    post = Post(id=1, title="Hello", author_id=42)
    dumped = post.viur_dump()["author"]
    assert dumped == {"dest": {"key": Author.viur_encode_key(42)}, "rel": None}


def test_dump_empty_relation_is_none():
    assert Post(id=1, title="Hello").viur_dump()["author"] is None


def test_ref_keys_missing_on_target_reduce_to_key():
    assert set(Tagged.viur_structure()["tag"]["relskel"]) == {"key", "shortkey"}
    tag = Tag(id=7, label="x")
    dumped = Tagged(id=1, tag_id=7, tag=tag).viur_dump()["tag"]
    assert dumped == {"dest": {"key": tag.viur_key}, "rel": None}  # no "name" on Tag


# --------------------------------------------------------------------------- #
# from_client                                                                  #
# --------------------------------------------------------------------------- #

def test_from_client_accepts_key_string_and_dump_shape(module):
    author = _author()
    instance, errors = Post.viur_from_client({"title": "x", "author": author.viur_key})
    assert errors == [] and instance.author_id == author.id

    instance, errors = Post.viur_from_client(
        {"title": "x", "author": {"dest": {"key": author.viur_key}}},
    )
    assert errors == [] and instance.author_id == author.id

    # flat dict shape without "dest" wrapper is accepted too
    instance, errors = Post.viur_from_client(
        {"title": "x", "author": {"key": author.viur_key}},
    )
    assert errors == [] and instance.author_id == author.id


def test_from_client_empty_clears_the_relation():
    instance, errors = Post.viur_from_client({"title": "x", "author": ""})
    assert errors == [] and instance.author_id is None


def test_relational_bone_module_resolves_from_registered_sqllist(module):
    class Genre(ViURModel, table=True):
        __tablename__ = "viur_models_test_genre"
        name: str = ViURField(default="", required=False)

    class Song(ViURModel, table=True):
        __tablename__ = "viur_models_test_song"
        genre_id: int | None = ViURField(
            default=None, foreign_key="viur_models_test_genre.id",
        )
        genre: Genre | None = Relationship()

    # no module serves Genre yet — the kind is the fallback
    assert Song.viur_structure()["genre"]["module"] == "viur_models_test_genre"

    class GenreModule(SQLList):
        model = Genre

        def can(self, instance):
            return True

    # registration drops cached structures; bones rebuild with the module
    GenreModule("genres", "/genres")
    bone = Song.viur_structure()["genre"]
    assert bone["module"] == "genres"
    assert bone["type"] == "relational.viur_models_test_genre"  # kind stays

    # first module wins — a second one serving the same model changes nothing
    GenreModule("genres_again", "/genres2")
    assert Song.viur_structure()["genre"]["module"] == "genres"

    # explicit viur_relation_meta override beats the registry
    class Song2(ViURModel, table=True):
        __tablename__ = "viur_models_test_song2"
        viur_relation_meta = {"genre": {"module": "custom_genres"}}
        genre_id: int | None = ViURField(
            default=None, foreign_key="viur_models_test_genre.id",
        )
        genre: Genre | None = Relationship()

    assert Song2.viur_structure()["genre"]["module"] == "custom_genres"


def test_from_client_without_relation_key_leaves_fk_untouched():
    instance, errors = Post.viur_from_client({"title": "x"})
    assert errors == [] and instance.author_id is None


def test_from_client_rejects_malformed_and_foreign_keys(module):
    for bad in ("garbage", Post.viur_encode_key(1)):  # wrong kind = foreign
        instance, errors = Post.viur_from_client({"title": "x", "author": bad})
        assert instance.title == "x"  # best-effort form keeps the input
        assert [tuple(e.fieldPath) for e in errors] == [("author",)]
        assert errors[0].severity.name == "Invalid"


# --------------------------------------------------------------------------- #
# multiple (many-to-many via link table)                                       #
# --------------------------------------------------------------------------- #

def _fan(name):
    with db.get_session() as session:
        fan = Fan(name=name)
        session.add(fan)
    return fan


def test_multiple_relation_structure():
    structure = Post.viur_structure()
    fans = structure["fans"]
    assert (fans["type"], fans["multiple"], fans["required"]) == (
        "relational.viur_models_test_fan", True, False,
    )
    assert fans["descr"] == "Fans"  # default: title-cased relation name
    assert structure["tags"]["descr"] == "Schlagworte"  # viur_relation_meta
    assert set(structure["tags"]["relskel"]) == {"key", "shortkey"}  # key-only target
    # m2m bones are appended after the regular fields
    assert fans["sortindex"] >= len(Post.model_fields)
    assert structure["tags"]["sortindex"] == fans["sortindex"] + 1


def test_multiple_dump_shapes(module):
    fan_a, fan_b = _fan("A"), _fan("B")
    post = Post(id=1, title="x", fans=[fan_a, fan_b])
    dumped = post.viur_dump()["fans"]
    assert dumped == [
        {"dest": {"key": fan_a.viur_key, "name": "A"}, "rel": None},
        {"dest": {"key": fan_b.viur_key, "name": "B"}, "rel": None},
    ]
    assert Post(id=2, title="y").viur_dump()["fans"] == []  # unloaded/empty


def test_multiple_from_client_accepts_lists_and_single_values(module):
    fan = _fan("A")
    instance, errors = Post.viur_from_client({"title": "x", "fans": [fan.viur_key]})
    assert errors == []
    assert instance.__dict__["_viur_pending_relations"]["fans"] == [fan.id]

    instance, _ = Post.viur_from_client({"title": "x", "fans": fan.viur_key})
    assert instance.__dict__["_viur_pending_relations"]["fans"] == [fan.id]

    instance, _ = Post.viur_from_client(
        {"title": "x", "fans": [{"dest": {"key": fan.viur_key}}]},
    )
    assert instance.__dict__["_viur_pending_relations"]["fans"] == [fan.id]

    instance, _ = Post.viur_from_client({"title": "x", "fans": ""})  # clear
    assert instance.__dict__["_viur_pending_relations"]["fans"] == []

    # empty items inside a list are skipped, not errors
    instance, errors = Post.viur_from_client({"title": "x", "fans": ["", fan.viur_key]})
    assert errors == []
    assert instance.__dict__["_viur_pending_relations"]["fans"] == [fan.id]


def test_duplicate_multiple_keys_are_rejected_by_default(module):
    # like core (MultipleConstraints.duplicates defaults False) — and the
    # SQL link table could not even store the duplicate: its composite
    # primary key would collapse it silently
    fan = _fan("A")
    instance, errors = Post.viur_from_client(
        {"title": "x", "fans": [fan.viur_key, fan.viur_key]},
    )
    assert [e.errorMessage for e in errors] == ["Duplicate entries are not allowed"]


def test_multiple_from_client_rejects_malformed_keys(module):
    instance, errors = Post.viur_from_client({"title": "x", "fans": ["garbage"]})
    assert instance.title == "x"  # best-effort form keeps the input
    assert [tuple(e.fieldPath) for e in errors] == [("fans",)]


def test_multiple_selection_roundtrips_on_rejected_form(module):
    # a validation error elsewhere must not lose the m2m selection: the
    # parked keys dump as key-only dests on the best-effort form
    fan = _fan("A")
    form, errors = Post.viur_from_client(
        {"title": "x" * 101, "fans": [fan.viur_key]},  # title too long
    )
    assert errors
    assert form.viur_dump()["fans"] == [
        {"dest": {"key": fan.viur_key}, "rel": None},
    ]


def test_multiple_add_edit_roundtrip(module):
    fan_a, fan_b = _fan("A"), _fan("B")
    verb, created = module.add(title="x", fans=[fan_a.viur_key, fan_b.viur_key], skey="c")
    assert verb == "addSuccess"

    _, viewed = module.view(created.viur_key)
    assert [d["dest"]["name"] for d in viewed.viur_dump()["fans"]] == ["A", "B"]

    # explicit list replaces; empty string clears
    _, edited = module.edit(created.viur_key, fans=fan_b.viur_key, title="x")
    _, viewed = module.view(created.viur_key)
    assert [d["dest"]["name"] for d in viewed.viur_dump()["fans"]] == ["B"]

    _, edited = module.edit(created.viur_key, fans="", title="x")
    _, viewed = module.view(created.viur_key)
    assert viewed.viur_dump()["fans"] == []

    # UNSUBMITTED multiple clears too: browsers send nothing for an empty
    # multi-selection — absent means empty, not "keep stored"
    module.edit(created.viur_key, fans=fan_a.viur_key, title="x")
    _, edited = module.edit(created.viur_key, title="y")
    _, viewed = module.view(created.viur_key)
    assert viewed.viur_dump()["fans"] == []


class ConstrainedFanLink(SQLModel, table=True):
    __tablename__ = "viur_models_test_constrainedfan"
    post_id: int | None = Field(
        default=None, foreign_key="viur_models_test_constrainedpost.id", primary_key=True,
    )
    fan_id: int | None = Field(
        default=None, foreign_key="viur_models_test_fan.id", primary_key=True,
    )


class ConstrainedPost(ViURModel, table=True):
    __tablename__ = "viur_models_test_constrainedpost"

    viur_relation_meta = {
        "fans": {"multiple": {"min": 1, "max": 2, "duplicates": False}},
    }

    title: str = ViURField(default="", required=False)
    fans: list[Fan] = Relationship(link_model=ConstrainedFanLink)


def test_multiple_constraints_in_structure_and_enforcement(module):
    bone = ConstrainedPost.viur_structure()["fans"]
    assert bone["multiple"] == {"duplicates": False, "max": 2, "min": 1}

    fan_a, fan_b, fan_c = _fan("A"), _fan("B"), _fan("C")
    keys = [fan_a.viur_key, fan_b.viur_key, fan_c.viur_key]

    _, errors = ConstrainedPost.viur_from_client({"fans": []})
    assert "min 1" in errors[0].errorMessage
    _, errors = ConstrainedPost.viur_from_client({"fans": keys})
    assert "max 2" in errors[0].errorMessage
    _, errors = ConstrainedPost.viur_from_client({"fans": [keys[0], keys[0]]})
    assert "Duplicate" in errors[0].errorMessage

    instance, errors = ConstrainedPost.viur_from_client({"fans": keys[:2]})
    assert errors == []
    assert instance.__dict__["_viur_pending_relations"]["fans"] == [fan_a.id, fan_b.id]


def test_multiple_add_with_unknown_target_is_rejected(module):
    verb, form = module.add(title="x", fans=[Fan.viur_encode_key(999)], skey="c")
    assert verb == "add"
    assert [tuple(e.fieldPath) for e in form.errors] == [("fans",)]
    with db.get_session() as session:
        from sqlmodel import select
        assert session.exec(select(Post)).all() == []


# --------------------------------------------------------------------------- #
# SQLList                                                                      #
# --------------------------------------------------------------------------- #

def test_add_and_view_roundtrip_with_relation(module):
    author = _author("Alice")
    verb, created = module.add(title="Hello", author=author.viur_key, skey="csrf")
    assert verb == "addSuccess"
    assert created.author_id == author.id

    verb, viewed = module.view(created.viur_key)
    # eager-loaded relation survives the closed session
    assert viewed.viur_dump()["author"]["dest"]["name"] == "Alice"


def test_add_with_unknown_relation_target_is_rejected(module):
    verb, form = module.add(title="x", author=Author.viur_encode_key(999), skey="csrf")
    assert verb == "add"
    assert [tuple(e.fieldPath) for e in form.errors] == [("author",)]
    assert form.errors[0].errorMessage == "Unknown key"


def test_edit_keeps_relation_when_unsubmitted_and_can_change_it(module):
    alice, bob = _author("Alice"), _author("Bob")
    _, created = module.add(title="Hello", author=alice.viur_key, skey="csrf")

    # unsubmitted relation survives the merge (dump shape roundtrips)
    _, edited = module.edit(created.viur_key, title="Hi")
    assert edited.author_id == alice.id

    # explicit change moves the FK; the stale loaded relation is expired
    _, edited = module.edit(created.viur_key, author=bob.viur_key)
    assert edited.author_id == bob.id
    assert edited.viur_dump()["author"]["dest"]["key"] == bob.viur_key

    # rejected relation edit keeps stored value
    _, form = module.edit(created.viur_key, author=Author.viur_encode_key(999))
    assert [tuple(e.fieldPath) for e in form.errors] == [("author",)]
    _, viewed = module.view(created.viur_key)
    assert viewed.author_id == bob.id