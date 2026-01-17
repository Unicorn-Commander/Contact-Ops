"""
Company model for the catalogue_companies table.
"""
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from sqlalchemy import ARRAY, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.source import Source


class Company(Base):
    """
    Represents a company in the data catalogue.

    Stores company information with flexible enrichment data storage
    and tracking of which source applications contributed data.
    """

    __tablename__ = "catalogue_companies"

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    primary_domain: Mapped[Optional[str]] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=True,
    )
    industry: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    sub_industry: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    employee_count: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    headquarters_city: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    headquarters_state: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    headquarters_country: Mapped[Optional[str]] = mapped_column(
        String(255),
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
        UUID(as_uuid=True),
        nullable=True,
        index=True,
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
    contacts: Mapped[list["Contact"]] = relationship(
        "Contact",
        back_populates="company",
        lazy="selectin",
    )
    sources: Mapped[list["Source"]] = relationship(
        "Source",
        primaryjoin="and_(Company.id == foreign(Source.entity_id), Source.entity_type == 'company')",
        viewonly=True,
        lazy="selectin",
    )

    # Indexes
    __table_args__ = (
        Index("ix_catalogue_companies_industry", "industry"),
        Index("ix_catalogue_companies_headquarters_country", "headquarters_country"),
        Index("ix_catalogue_companies_first_seen_at", "first_seen_at"),
    )

    def __repr__(self) -> str:
        return f"<Company(id={self.id}, name='{self.name}', domain='{self.primary_domain}')>"
