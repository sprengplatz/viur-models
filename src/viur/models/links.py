"""``RelationLink`` — association objects: relations WITH edge payload.

The ``RelationalBone(using=RelSkel)`` analogue: extra data living **on the
relation itself** (the ``rel`` part of ``{"dest": …, "rel": …}``). In SQL
that is the association-object pattern — the link table carries the payload
columns, and those columns ARE the using-skel::

    class EntryTagLink(RelationLink, table=True):
        __tablename__ = "example_entry_tag"
        entry_id: int | None = Field(default=None, foreign_key="example_entry.id", primary_key=True)
        tag_id: int | None = Field(default=None, foreign_key="example_tag.id", primary_key=True)
        tag: "ExampleTag" = Relationship()                       # the dest side
        weight: int = ViURField(default=0, descr="Gewichtung")   # using payload

    class ExampleEntry(ViURModel, table=True):
        tags: list[EntryTagLink] = Relationship(
            sa_relationship_kwargs={"cascade": "all, delete-orphan"},  # required
        )

The link model must declare exactly one to-one ``Relationship`` to the
target (the ``dest`` side); every scalar field that is not one of the two
FK columns becomes part of the ``using`` structure. Client input is the
bone's wire shape: ``[{"dest": {"key": …}, "rel": {…}}, …]`` (plain keys
work too — the payload then takes its defaults).
"""
from sqlmodel import SQLModel


class RelationLink(SQLModel):
    """Marker base for association-object link tables (see module docs)."""
