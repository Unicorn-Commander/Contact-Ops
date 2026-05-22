"""
SQLAlchemy ORM models for Contact-Ops.

These mirror the Alembic migration DDL in alembic/versions/.
"""

import uuid as uuid_module
from datetime import datetime
from sqlalchemy import (
    Boolean, ForeignKey, Integer, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    parent_tenant_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    branding: Mapped[dict] = mapped_column(JSONB, default=dict)
    retention_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    hipaa_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    gdpr_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    data_residency: Mapped[str] = mapped_column(Text, default="us-east")
    data_intel_publish_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    carddav_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    graph_mode: Mapped[str] = mapped_column(Text, default="per_org_graph")
    graph_name: Mapped[str | None] = mapped_column(Text)
    qdrant_namespace: Mapped[str] = mapped_column(Text)
    garage_bucket_prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(server_default="now()")
    deprecated_at: Mapped[datetime | None] = mapped_column()
    etag: Mapped[str] = mapped_column(Text, default="")


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    legacy_contact_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID, unique=True)
    kind: Mapped[str] = mapped_column(Text, default="individual")
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    family_name: Mapped[str | None] = mapped_column(Text)
    given_name: Mapped[str | None] = mapped_column(Text)
    additional_names: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    honorific_prefix: Mapped[str | None] = mapped_column(Text)
    honorific_suffix: Mapped[str | None] = mapped_column(Text)
    phonetic_family_name: Mapped[str | None] = mapped_column(Text)
    phonetic_given_name: Mapped[str | None] = mapped_column(Text)
    nicknames: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    file_as: Mapped[str | None] = mapped_column(Text)
    pronouns: Mapped[str | None] = mapped_column(Text)
    gender_identity: Mapped[str | None] = mapped_column(Text)
    birthday: Mapped[dict | None] = mapped_column(JSONB)
    anniversary: Mapped[dict | None] = mapped_column(JSONB)
    death_date: Mapped[dict | None] = mapped_column(JSONB)
    is_deceased: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_languages: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    time_zone: Mapped[str | None] = mapped_column(Text)
    preferred_contact_channel: Mapped[str | None] = mapped_column(Text)
    preferred_contact_time_window: Mapped[dict | None] = mapped_column(JSONB)
    headline: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    occupation_title: Mapped[str | None] = mapped_column(Text)
    current_org_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    current_org_role_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    canonical_owner_tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"))
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False)
    vcard_uid: Mapped[str | None] = mapped_column(Text, unique=True)
    source_pid_map: Mapped[dict] = mapped_column(JSONB, default=dict)
    merge_status: Mapped[str] = mapped_column(Text, default="canonical")
    merged_into_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("persons.id"))
    name_embedding: Mapped[object | None] = mapped_column(Vector(384))
    bio_embedding: Mapped[object | None] = mapped_column(Vector(1024))
    voice_fingerprint_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    face_embedding: Mapped[object | None] = mapped_column(Vector(512))
    communication_strength_score: Mapped[float] = mapped_column(server_default="0")
    last_interaction_at: Mapped[datetime | None] = mapped_column()
    last_inbound_at: Mapped[datetime | None] = mapped_column()
    last_outbound_at: Mapped[datetime | None] = mapped_column()
    interaction_count_30d: Mapped[int] = mapped_column(server_default="0")
    interaction_count_90d: Mapped[int] = mapped_column(server_default="0")
    interaction_count_365d: Mapped[int] = mapped_column(server_default="0")
    preferred_channel_inferred: Mapped[str | None] = mapped_column(Text)
    response_latency_p50_hours: Mapped[float | None] = mapped_column()
    relationship_temperature: Mapped[str] = mapped_column(Text, default="cold")
    last_topic_clusters: Mapped[dict | None] = mapped_column(JSONB)
    inferred_attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    retention_class: Mapped[str] = mapped_column(Text, default="operational_2y")
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(server_default="now()")
    created_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    etag: Mapped[str] = mapped_column(Text, default="")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    legacy_company_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID, unique=True)
    kind: Mapped[str] = mapped_column(Text, default="company")
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    dba_names: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    description: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    naics_codes: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    sic_codes: Mapped[list] = mapped_column(ARRAY(Text), default=list)
    employee_count_estimate: Mapped[int | None] = mapped_column(Integer)
    employee_count_range: Mapped[str | None] = mapped_column(Text)
    annual_revenue_estimate: Mapped[float | None] = mapped_column(Numeric(18, 2))
    annual_revenue_range: Mapped[str | None] = mapped_column(Text)
    founded_year: Mapped[int | None] = mapped_column(Integer)
    dissolved_year: Mapped[int | None] = mapped_column(Integer)
    ticker_symbol: Mapped[str | None] = mapped_column(Text)
    cik: Mapped[str | None] = mapped_column(Text)
    duns_number: Mapped[str | None] = mapped_column(Text)
    sam_uei: Mapped[str | None] = mapped_column(Text)
    ein_enc: Mapped[bytes | None] = mapped_column(BYTEA)
    cage_code: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(Text)
    linkedin_company_id: Mapped[str | None] = mapped_column(Text)
    crunchbase_uuid: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    logo_asset_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    parent_org_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    headquarters_address_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    tax_status: Mapped[str | None] = mapped_column(Text)
    state_of_incorporation: Mapped[str | None] = mapped_column(Text)
    is_sdvosb: Mapped[bool] = mapped_column(Boolean, default=False)
    is_minority_owned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_woman_owned: Mapped[bool] = mapped_column(Boolean, default=False)
    description_embedding: Mapped[object | None] = mapped_column(Vector(1024))
    canonical_owner_tenant_id: Mapped[uuid_module.UUID] = mapped_column(ForeignKey("tenants.id"))
    merge_status: Mapped[str] = mapped_column(Text, default="canonical")
    merged_into_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    retention_class: Mapped[str] = mapped_column(Text, default="operational_2y")
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(server_default="now()")
    created_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    etag: Mapped[str] = mapped_column(Text, default="")


# Re-export Base for the app layer
__all__ = ["Base", "Tenant", "Person", "Organization"]
