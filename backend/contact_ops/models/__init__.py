"""
SQLAlchemy ORM models for Contact-Ops.

These mirror the Alembic migration DDL in alembic/versions/.
"""

import uuid as uuid_module
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy import (
    Boolean, Enum, ForeignKey, Integer, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from contact_ops.models.enums import (
    ContactChannel,
    MergeStatus,
    OrgKind,
    PersonKind,
    PreferredContact,
    RetentionClass,
    TemperatureState,
    TenantKind,
    enum_values,
)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[TenantKind] = mapped_column(
        Enum(TenantKind, name="tenant_kind", create_type=False, values_callable=enum_values),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, nullable=False)
    # Subscription plan tier (P-00075 §4 entitlement). free < pro < enterprise;
    # the policy + ladder live in contact_ops.mcp.entitlement. Existing tenants
    # were grandfathered to 'enterprise' in migration 0041; new self-served
    # tenants default to 'free'.
    plan_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'free'"))
    parent_tenant_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    branding: Mapped[dict] = mapped_column(JSONB, default=dict)
    retention_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    hipaa_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    gdpr_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    data_residency: Mapped[str] = mapped_column(Text, default="us-east")
    data_intel_publish_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_approve_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Workspace-wide agent master switch (the Agent Command Center kill-switch).
    # True => BaseAgent.execute refuses to run ANY agent for this tenant, beside
    # the per-agent / fleet circuit breaker. Distinct from auto_approve_disabled
    # (which only stops auto-APPLY; agents still run and propose). reason/at/by
    # are audit detail the console surfaces; who/when/why also hits prov_activities.
    agents_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.text("false")
    )
    agents_paused_reason: Mapped[str | None] = mapped_column(Text)
    agents_paused_at: Mapped[datetime | None] = mapped_column()
    agents_paused_by: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    carddav_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    graph_mode: Mapped[str] = mapped_column(Text, default="per_org_graph")
    graph_name: Mapped[str | None] = mapped_column(Text)
    qdrant_namespace: Mapped[str] = mapped_column(Text)
    garage_bucket_prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    deprecated_at: Mapped[datetime | None] = mapped_column()
    etag: Mapped[str] = mapped_column(Text, default="")


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    legacy_contact_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID, unique=True)
    kind: Mapped[PersonKind] = mapped_column(
        Enum(PersonKind, name="person_kind", create_type=False, values_callable=enum_values),
        server_default=sa.text("'individual'"),
    )
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
    preferred_contact_channel: Mapped[PreferredContact] = mapped_column(
        Enum(PreferredContact, name="preferred_contact", create_type=False, values_callable=enum_values),
        server_default=sa.text("'unknown'"),
    )
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
    merge_status: Mapped[MergeStatus] = mapped_column(
        Enum(MergeStatus, name="merge_status", create_type=False, values_callable=enum_values),
        server_default=sa.text("'canonical'"),
    )
    merged_into_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("persons.id"))
    name_embedding: Mapped[object | None] = mapped_column(Vector(384))
    bio_embedding: Mapped[object | None] = mapped_column(Vector(1024))
    voice_fingerprint_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    face_embedding: Mapped[object | None] = mapped_column(Vector(512))
    communication_strength_score: Mapped[float] = mapped_column(server_default=sa.text("0"))
    last_interaction_at: Mapped[datetime | None] = mapped_column()
    last_inbound_at: Mapped[datetime | None] = mapped_column()
    last_outbound_at: Mapped[datetime | None] = mapped_column()
    interaction_count_30d: Mapped[int] = mapped_column(server_default=sa.text("0"))
    interaction_count_90d: Mapped[int] = mapped_column(server_default=sa.text("0"))
    interaction_count_365d: Mapped[int] = mapped_column(server_default=sa.text("0"))
    preferred_channel_inferred: Mapped[ContactChannel | None] = mapped_column(
        Enum(ContactChannel, name="contact_channel", create_type=False, values_callable=enum_values)
    )
    response_latency_p50_hours: Mapped[float | None] = mapped_column()
    relationship_temperature: Mapped[TemperatureState] = mapped_column(
        Enum(TemperatureState, name="temperature_state", create_type=False, values_callable=enum_values),
        server_default=sa.text("'cold'"),
    )
    last_topic_clusters: Mapped[dict | None] = mapped_column(JSONB)
    inferred_attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    retention_class: Mapped[RetentionClass] = mapped_column(
        Enum(RetentionClass, name="retention_class", create_type=False, values_callable=enum_values),
        server_default=sa.text("'operational_2y'"),
    )
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    # Two-phase erasure tombstone (P-00075 compliance, migration 0042). An Art.17
    # erasure request sets these (+ redacts PII); the retention sweep hard-purges
    # once purge_after passes. Both null on a normal live person.
    tombstoned_at: Mapped[datetime | None] = mapped_column()
    purge_after: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    created_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    etag: Mapped[str] = mapped_column(Text, default="")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid_module.UUID] = mapped_column(PGUUID, primary_key=True)
    legacy_company_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID, unique=True)
    kind: Mapped[OrgKind] = mapped_column(
        Enum(OrgKind, name="org_kind", create_type=False, values_callable=enum_values),
        server_default=sa.text("'company'"),
    )
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
    merge_status: Mapped[MergeStatus] = mapped_column(
        Enum(MergeStatus, name="merge_status", create_type=False, values_callable=enum_values),
        server_default=sa.text("'canonical'"),
    )
    merged_into_id: Mapped[uuid_module.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    retention_class: Mapped[RetentionClass] = mapped_column(
        Enum(RetentionClass, name="retention_class", create_type=False, values_callable=enum_values),
        server_default=sa.text("'operational_2y'"),
    )
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    created_by_actor_id: Mapped[uuid_module.UUID | None] = mapped_column(PGUUID)
    etag: Mapped[str] = mapped_column(Text, default="")


from contact_ops.models.action_event import ActionEvent
from contact_ops.models.acl_grant import AclGrant
from contact_ops.models.agent_registry import AgentRegistry
from contact_ops.models.address import PostalAddress
from contact_ops.models.email import Email
from contact_ops.models.fact import Fact
from contact_ops.models.field_provenance import FieldProvenance
from contact_ops.models.identifier import Identifier
from contact_ops.models.im_handle import IMHandle
from contact_ops.models.media_asset import MediaAsset
from contact_ops.models.merge_candidate import MergeCandidate
from contact_ops.models.merge_history import MergeHistory
from contact_ops.models.organization_tenant_membership import OrganizationTenantMembership
from contact_ops.models.person_alias import PersonAlias
from contact_ops.models.person_org_role import PersonOrgRole
from contact_ops.models.person_person_relation import PersonPersonRelation
from contact_ops.models.person_tenant_membership import PersonTenantMembership
from contact_ops.models.personal_access_token import PersonalAccessToken
from contact_ops.models.phone import Phone
from contact_ops.models.photo import Photo
from contact_ops.models.prov_activity import ProvActivity
from contact_ops.models.source import Source
from contact_ops.models.tag import Tag
from contact_ops.models.url import Url
from contact_ops.models.voice_consent import VoiceConsent
from contact_ops.models.voice_fingerprint import VoiceFingerprint
from contact_ops.models.voice_sample import VoiceSample


__all__ = [
    "Base",
    "Tenant",
    "Person",
    "Organization",
    "Email",
    "Phone",
    "ActionEvent",
    "AclGrant",
    "PersonAlias",
    "PersonPersonRelation",
    "AgentRegistry",
    "Fact",
    "FieldProvenance",
    "MediaAsset",
    "MergeCandidate",
    "MergeHistory",
    "OrganizationTenantMembership",
    "PersonTenantMembership",
    "PersonalAccessToken",
    "Photo",
    "PostalAddress",
    "Identifier",
    "IMHandle",
    "PersonOrgRole",
    "ProvActivity",
    "Source",
    "Tag",
    "Url",
    "VoiceConsent",
    "VoiceFingerprint",
    "VoiceSample",
]
