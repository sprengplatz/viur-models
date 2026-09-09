"""Base for association-object link tables (relations with a ``using`` payload)."""
from sqlmodel import SQLModel


class RelationLink(SQLModel):
    """Marker base for association-object link tables."""
