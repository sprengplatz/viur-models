"""Base for association-object link tables (relations with a ``using`` payload)."""
import typing as t

from sqlmodel import SQLModel


class RelationLink(SQLModel):
    """Marker base for association-object link tables."""

    #: Same database as both related models.
    viur_database: t.ClassVar[str] = "default"
