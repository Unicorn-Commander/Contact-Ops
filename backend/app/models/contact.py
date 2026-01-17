"""
Contact model for the catalogue_contacts table.
"""
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.email import Email
    from app.models.source import Source


class Contact(Base):
    """
    Represents a contact in the data catalogue.

    Stores individual contact information linked to companies,
    with enrichment data and source tracking.
    """

    __tablename__ = "catalogue_contacts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    company_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    full_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    first_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    last_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    primary_email: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=True,
    )
    phone: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    mobile: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    linkedin_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )
    enrichment_data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
    )
    source_apps: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(Text),
        nullable=True,
        default=list,
    )
    owner_user_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(3, 2),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    company: Mapped[Optional["Company"]] = relationship(
        "Company",
        back_populates="contacts",
        lazy="selectin",
    )
    emails: Mapped[list["Email"]] = relationship(
        "Email",
        back_populates="contact",
        lazy="selectin",
    )
    sources: Mapped[list["Source"]] = relationship(
        "Source",
        primaryjoin="and_(Contact.id == foreign(Source.entity_id), Source.entity_type == 'contact')",
        viewonly=True,
        lazy="selectin",
    )

    # Indexes
    __table_args__ = (
        Index("ix_catalogue_contacts_full_name", "full_name"),
        Index("ix_catalogue_contacts_last_name", "last_name"),
        Index("ix_catalogue_contacts_first_seen_at", "first_seen_at"),
    )

    def __repr__(self) -> str:
        return f"<Contact(id={self.id}, name='{self.full_name}', email='{self.primary_email}')>"
