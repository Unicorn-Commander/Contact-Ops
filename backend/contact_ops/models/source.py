import uuid as uuid_module
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from contact_ops.models import Base
from contact_ops.models.enums import SourceType, enum_values


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    legacy_source_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", create_type=False, values_callable=enum_values),
        nullable=False,
    )
    source_uri: Mapped[str | None] = mapped_column(Text)
    source_record_id: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieval_method: Mapped[str | None] = mapped_column(Text)
    raw_payload_asset_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("media_assets.id"))
    source_reliability_base: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
