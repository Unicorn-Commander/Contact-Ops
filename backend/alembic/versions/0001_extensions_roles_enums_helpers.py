"""extensions, roles, enums, helpers

Revision ID: 0001
Revises:
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    has_pg_uuidv7 = op.get_bind().execute(sa.text("""
        SELECT EXISTS (
            SELECT 1
            FROM pg_available_extensions
            WHERE name = 'pg_uuidv7'
        )
    """)).scalar()
    if has_pg_uuidv7:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_uuidv7")
        op.execute("""
            CREATE OR REPLACE FUNCTION uuidv7_generate() RETURNS uuid
            LANGUAGE sql VOLATILE PARALLEL SAFE
            AS $$ SELECT uuid_generate_v7() $$
        """)
    else:
        op.execute("""
            CREATE OR REPLACE FUNCTION uuidv7_generate() RETURNS uuid
            LANGUAGE plpgsql VOLATILE PARALLEL SAFE
            AS $$
            DECLARE
                unix_ts_ms bigint;
                random_bytes bytea;
                rand_a integer;
                uuid_bytes bytea;
            BEGIN
                unix_ts_ms := floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint;
                random_bytes := gen_random_bytes(10);
                rand_a := ((get_byte(random_bytes, 0) << 8) | get_byte(random_bytes, 1)) & 4095;

                uuid_bytes :=
                    decode(lpad(to_hex(unix_ts_ms), 12, '0'), 'hex') ||
                    decode(lpad(to_hex((7 << 12) | rand_a), 4, '0'), 'hex') ||
                    substring(random_bytes FROM 3 FOR 8);
                uuid_bytes := set_byte(uuid_bytes, 8, (get_byte(uuid_bytes, 8) & 63) | 128);

                RETURN encode(uuid_bytes, 'hex')::uuid;
            END
            $$
        """)

    # App roles
    op.execute("DO $$ BEGIN CREATE ROLE contact_ops_app NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE ROLE contact_ops_audit NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE ROLE contact_ops_ro NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    # Helper functions
    op.execute("""
        CREATE OR REPLACE FUNCTION sha256_bytes(payload jsonb) RETURNS bytea
        LANGUAGE sql IMMUTABLE
        AS $$ SELECT digest(payload::text, 'sha256') $$
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION norm_text(t text) RETURNS text
        LANGUAGE sql IMMUTABLE
        AS $$ SELECT lower(unaccent(coalesce(t,''))) $$
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION valid_window_ok(v_from timestamptz, v_to timestamptz) RETURNS boolean
        LANGUAGE sql IMMUTABLE
        AS $$ SELECT v_to IS NULL OR v_from IS NULL OR v_to > v_from $$
    """)

    # ENUMs
    op.execute("""
        CREATE TYPE entity_kind AS ENUM (
            'person','organization','edge','tag','voice_print','address',
            'email','phone','identifier','media_asset','tenant','fact','interaction'
        )
    """)
    op.execute("""
        CREATE TYPE person_kind AS ENUM ('individual','group','organization_alias','location_alias')
    """)
    op.execute("""
        CREATE TYPE org_kind AS ENUM (
            'company','nonprofit','government','household','band','project',
            'vendor','customer','partner','holding_company','school','religious','union','other'
        )
    """)
    op.execute("""
        CREATE TYPE merge_status AS ENUM ('canonical','merged_into','archived','do_not_link')
    """)
    op.execute("""
        CREATE TYPE contact_channel AS ENUM (
            'email','sms','call','meeting','letter','im','social_post','app_push','signal','whatsapp'
        )
    """)
    op.execute("CREATE TYPE direction AS ENUM ('in','out','observed')")
    op.execute("""
        CREATE TYPE tenant_kind AS ENUM ('personal','magic_unicorn_internal','brand','white_label_customer')
    """)
    op.execute("""
        CREATE TYPE consent_basis AS ENUM (
            'self_provided','business_card','opted_in','public_record',
            'legitimate_interest','vendor_provided'
        )
    """)
    op.execute("CREATE TYPE actor_type AS ENUM ('human','agent','automation_rule','external_system','migration')")
    op.execute("""
        CREATE TYPE event_status AS ENUM ('proposed','approved','applied','rejected','reverted','superseded')
    """)
    op.execute("""
        CREATE TYPE source_type AS ENUM (
            'self_reported','public_record','linkedin','apollo','zoominfo','clearbit','peoplelabs',
            'google_contacts','icloud','manual_entry','business_card_scan','email_signature_parse',
            'meeting_transcript','web_research','agent_inference','sam_gov','sec_edgar','crisis_doc','data_intel'
        )
    """)
    op.execute("CREATE TYPE temperature_state AS ENUM ('cold','warm','hot','dormant')")
    op.execute("""
        CREATE TYPE preferred_contact AS ENUM ('email','sms','call','signal','imessage','dont_contact','unknown')
    """)
    op.execute("""
        CREATE TYPE seniority_level AS ENUM ('c_level','vp','director','manager','ic','junior','intern','board','external','unknown')
    """)
    op.execute("""
        CREATE TYPE employment_type AS ENUM ('ft','pt','contract','advisory','board','ownership','intern','volunteer')
    """)
    op.execute("""
        CREATE TYPE role_type AS ENUM (
            'employee','founder','co_founder','contractor','advisor','board_member','board_chair','board_observer',
            'investor','lead_investor','angel','counsel','accountant','customer_contact','vendor_contact',
            'volunteer','intern','alumni','member','parent_company_exec','executive','officer','owner',
            'consultant','representative','spokesperson','beneficial_owner','custodian','trustee','witness'
        )
    """)
    op.execute("""
        CREATE TYPE relation_type AS ENUM (
            'parent_of','child_of','spouse_of','partner_of','sibling_of','grandparent_of','grandchild_of',
            'aunt_uncle_of','niece_nephew_of','cousin_of','step_parent_of','step_child_of','step_sibling_of',
            'in_law_of','ex_spouse_of','co_parent_of','guardian_of','ward_of','adopted_parent_of','adopted_child_of',
            'dating','engaged_to','household_member','roommate_of','friend_of','close_friend_of','acquaintance_of',
            'colleague_of','manager_of','reports_to','mentor_of','mentee_of','collaborator_of','co_founder_of',
            'co_investor_with','client_of','vendor_of','referrer_of','referred_by','introduced','introduced_by',
            'counsel_for','client_of_counsel','witness_for','witness_against','co_plaintiff_with','co_defendant_with',
            'opposing_party_to','named_in_with','expert_for','expert_against','executor_for','beneficiary_of',
            'self','emergency_contact_for','next_of_kin_for','power_of_attorney_for','healthcare_proxy_for',
            'knows','met_once','same_alias_as','suspected_same_as'
        )
    """)
    op.execute("""
        CREATE TYPE org_relation_type AS ENUM (
            'parent_of','subsidiary_of','acquired','acquired_by','merged_with','spun_off_from','spun_off',
            'partnership_with','vendor_to','customer_of','competitor_of','franchise_of','member_of_group',
            'jv_partner_of','licensor_of','licensee_of','distributor_of','reseller_of','successor_of','predecessor_of'
        )
    """)
    op.execute("CREATE TYPE email_type AS ENUM ('personal','work','school','other','alias')")
    op.execute("CREATE TYPE email_deliverability AS ENUM ('unknown','valid','invalid','risky','accept_all','disposable')")
    op.execute("CREATE TYPE phone_type AS ENUM ('mobile','home','work','fax','main','other')")
    op.execute("CREATE TYPE line_type AS ENUM ('mobile','landline','voip','toll_free','satellite','unknown')")
    op.execute("CREATE TYPE address_type AS ENUM ('home','work','mailing','billing','shipping','other')")
    op.execute("CREATE TYPE address_verified_via AS ENUM ('unverified','usps','smarty','manual','self_provided','arrived_mail')")
    op.execute("CREATE TYPE geo_precision AS ENUM ('rooftop','range','street','locality','region','country','unknown')")
    op.execute("CREATE TYPE trust_tier AS ENUM ('propose_only','auto_apply_low','auto_apply_high','authoritative','suspended')")
    op.execute("CREATE TYPE retention_class AS ENUM ('ephemeral_30d','operational_2y','indefinite','hipaa_6y','legal_hold')")
    op.execute("CREATE TYPE visibility_scope AS ENUM ('private','team','org','shared')")


def downgrade() -> None:
    op.execute("DROP TYPE visibility_scope")
    op.execute("DROP TYPE retention_class")
    op.execute("DROP TYPE trust_tier")
    op.execute("DROP TYPE geo_precision")
    op.execute("DROP TYPE address_verified_via")
    op.execute("DROP TYPE address_type")
    op.execute("DROP TYPE line_type")
    op.execute("DROP TYPE phone_type")
    op.execute("DROP TYPE email_deliverability")
    op.execute("DROP TYPE email_type")
    op.execute("DROP TYPE org_relation_type")
    op.execute("DROP TYPE relation_type")
    op.execute("DROP TYPE role_type")
    op.execute("DROP TYPE employment_type")
    op.execute("DROP TYPE seniority_level")
    op.execute("DROP TYPE preferred_contact")
    op.execute("DROP TYPE temperature_state")
    op.execute("DROP TYPE source_type")
    op.execute("DROP TYPE event_status")
    op.execute("DROP TYPE actor_type")
    op.execute("DROP TYPE consent_basis")
    op.execute("DROP TYPE tenant_kind")
    op.execute("DROP TYPE direction")
    op.execute("DROP TYPE contact_channel")
    op.execute("DROP TYPE merge_status")
    op.execute("DROP TYPE org_kind")
    op.execute("DROP TYPE person_kind")
    op.execute("DROP TYPE entity_kind")
    op.execute("DROP FUNCTION sha256_bytes(jsonb)")
    op.execute("DROP FUNCTION norm_text(text)")
    op.execute("DROP FUNCTION valid_window_ok(timestamptz, timestamptz)")
    op.execute("DROP FUNCTION uuidv7_generate()")
    op.execute("DROP EXTENSION IF EXISTS pg_uuidv7")
    op.execute("DROP ROLE IF EXISTS contact_ops_app")
    op.execute("DROP ROLE IF EXISTS contact_ops_audit")
    op.execute("DROP ROLE IF EXISTS contact_ops_ro")
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS citext")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
    op.execute("DROP EXTENSION IF EXISTS btree_gin")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
