"""
Job model for the verification_jobs table.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Job(Base):
    """
    Represents a verification or processing job.

    Tracks async job status for email verification, cataloguing,
    and CSV upload operations.
    """

    __tablename__ = "verification_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
        index=True,
    )
    job_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    total_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    processed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    error_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    input_data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )
    results: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_by_app: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )
    created_by_user: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )

    # Indexes
    __table_args__ = (
        Index("ix_verification_jobs_created_at", "created_at"),
        Index("ix_verification_jobs_status_job_type", "status", "job_type"),
    )

    def __repr__(self) -> str:
        return f"<Job(id={self.id}, type='{self.job_type}', status='{self.status}')>"

    @property
    def progress_percentage(self) -> float:
        """Calculate job progress as a percentage."""
        if self.total_count == 0:
            return 0.0
        return round((self.processed_count / self.total_count) * 100, 2)

    @property
    def is_complete(self) -> bool:
        """Check if the job has finished processing."""
        return self.status in ("completed", "failed")
