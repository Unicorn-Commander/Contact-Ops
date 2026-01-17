"""
Email model for the catalogue_emails table.
"""
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.source import Source


class Email(Base):
    """
    Represents an email address in the data catalogue.

    Tracks email verification status, validity, and bounce information
    for email deliverability management.
    """

    __tablename__ = "catalogue_emails"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    contact_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogue_contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    domain: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    is_valid: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
    )
    is_verified_smtp: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_catchall: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
    )
    is_disposable: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
    )
    is_role_based: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        nullable=True,
    )
    risk_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(3, 2),
        nullable=True,
    )
    bounce_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    last_bounce_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    verification_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    discovered_by_app: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    contact: Mapped[Optional["Contact"]] = relationship(
        "Contact",
        back_populates="emails",
        lazy="selectin",
    )
    sources: Mapped[list["Source"]] = relationship(
        "Source",
        primaryjoin="and_(Email.id == foreign(Source.entity_id), Source.entity_type == 'email')",
        viewonly=True,
        lazy="selectin",
    )

    # Indexes
    __table_args__ = (
        Index("ix_catalogue_emails_is_valid", "is_valid"),
        Index("ix_catalogue_emails_is_verified_smtp", "is_verified_smtp"),
        Index("ix_catalogue_emails_discovered_by_app", "discovered_by_app"),
        Index("ix_catalogue_emails_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Email(id={self.id}, email='{self.email}', valid={self.is_valid})>"
