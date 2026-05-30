"""Python mirrors of PostgreSQL enum types created by Alembic."""

import enum


def enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class EntityKind(str, enum.Enum):
    person = "person"
    organization = "organization"
    edge = "edge"
    tag = "tag"
    voice_print = "voice_print"
    address = "address"
    email = "email"
    phone = "phone"
    identifier = "identifier"
    media_asset = "media_asset"
    tenant = "tenant"
    fact = "fact"
    interaction = "interaction"


class PersonKind(str, enum.Enum):
    individual = "individual"
    group = "group"
    organization_alias = "organization_alias"
    location_alias = "location_alias"


class OrgKind(str, enum.Enum):
    company = "company"
    nonprofit = "nonprofit"
    government = "government"
    household = "household"
    band = "band"
    project = "project"
    vendor = "vendor"
    customer = "customer"
    partner = "partner"
    holding_company = "holding_company"
    school = "school"
    religious = "religious"
    union = "union"
    other = "other"


class MergeStatus(str, enum.Enum):
    canonical = "canonical"
    merged_into = "merged_into"
    archived = "archived"
    do_not_link = "do_not_link"


class ContactChannel(str, enum.Enum):
    email = "email"
    sms = "sms"
    call = "call"
    meeting = "meeting"
    letter = "letter"
    im = "im"
    social_post = "social_post"
    app_push = "app_push"
    signal = "signal"
    whatsapp = "whatsapp"


class Direction(str, enum.Enum):
    inbound = "in"
    outbound = "out"
    observed = "observed"


class TenantKind(str, enum.Enum):
    personal = "personal"
    magic_unicorn_internal = "magic_unicorn_internal"
    brand = "brand"
    white_label_customer = "white_label_customer"


class ConsentBasis(str, enum.Enum):
    self_provided = "self_provided"
    business_card = "business_card"
    opted_in = "opted_in"
    public_record = "public_record"
    legitimate_interest = "legitimate_interest"
    vendor_provided = "vendor_provided"


class ActorType(str, enum.Enum):
    human = "human"
    agent = "agent"
    automation_rule = "automation_rule"
    external_system = "external_system"
    migration = "migration"


class EventStatus(str, enum.Enum):
    proposed = "proposed"
    approved = "approved"
    applied = "applied"
    rejected = "rejected"
    reverted = "reverted"
    superseded = "superseded"
    resolved = "resolved"


class SourceType(str, enum.Enum):
    self_reported = "self_reported"
    public_record = "public_record"
    linkedin = "linkedin"
    apollo = "apollo"
    zoominfo = "zoominfo"
    clearbit = "clearbit"
    peoplelabs = "peoplelabs"
    ios_contacts_export = "ios_contacts_export"
    google_contacts = "google_contacts"
    nextcloud = "nextcloud"
    icloud = "icloud"
    manual_entry = "manual_entry"
    business_card_scan = "business_card_scan"
    email_signature_parse = "email_signature_parse"
    meeting_transcript = "meeting_transcript"
    web_research = "web_research"
    agent_inference = "agent_inference"
    sam_gov = "sam_gov"
    sec_edgar = "sec_edgar"
    crisis_doc = "crisis_doc"
    data_intel = "data_intel"


class TemperatureState(str, enum.Enum):
    cold = "cold"
    warm = "warm"
    hot = "hot"
    dormant = "dormant"


class PreferredContact(str, enum.Enum):
    email = "email"
    sms = "sms"
    call = "call"
    signal = "signal"
    imessage = "imessage"
    dont_contact = "dont_contact"
    unknown = "unknown"


class SeniorityLevel(str, enum.Enum):
    c_level = "c_level"
    vp = "vp"
    director = "director"
    manager = "manager"
    ic = "ic"
    junior = "junior"
    intern = "intern"
    board = "board"
    external = "external"
    unknown = "unknown"


class EmploymentType(str, enum.Enum):
    ft = "ft"
    pt = "pt"
    contract = "contract"
    advisory = "advisory"
    board = "board"
    ownership = "ownership"
    intern = "intern"
    volunteer = "volunteer"


class RoleType(str, enum.Enum):
    employee = "employee"
    founder = "founder"
    co_founder = "co_founder"
    contractor = "contractor"
    advisor = "advisor"
    board_member = "board_member"
    board_chair = "board_chair"
    board_observer = "board_observer"
    investor = "investor"
    lead_investor = "lead_investor"
    angel = "angel"
    counsel = "counsel"
    accountant = "accountant"
    customer_contact = "customer_contact"
    vendor_contact = "vendor_contact"
    volunteer = "volunteer"
    intern = "intern"
    alumni = "alumni"
    member = "member"
    parent_company_exec = "parent_company_exec"
    executive = "executive"
    officer = "officer"
    owner = "owner"
    consultant = "consultant"
    representative = "representative"
    spokesperson = "spokesperson"
    beneficial_owner = "beneficial_owner"
    custodian = "custodian"
    trustee = "trustee"
    witness = "witness"


class RelationType(str, enum.Enum):
    parent_of = "parent_of"
    child_of = "child_of"
    spouse_of = "spouse_of"
    partner_of = "partner_of"
    sibling_of = "sibling_of"
    grandparent_of = "grandparent_of"
    grandchild_of = "grandchild_of"
    aunt_uncle_of = "aunt_uncle_of"
    niece_nephew_of = "niece_nephew_of"
    cousin_of = "cousin_of"
    step_parent_of = "step_parent_of"
    step_child_of = "step_child_of"
    step_sibling_of = "step_sibling_of"
    in_law_of = "in_law_of"
    ex_spouse_of = "ex_spouse_of"
    co_parent_of = "co_parent_of"
    guardian_of = "guardian_of"
    ward_of = "ward_of"
    adopted_parent_of = "adopted_parent_of"
    adopted_child_of = "adopted_child_of"
    dating = "dating"
    engaged_to = "engaged_to"
    household_member = "household_member"
    roommate_of = "roommate_of"
    friend_of = "friend_of"
    close_friend_of = "close_friend_of"
    acquaintance_of = "acquaintance_of"
    colleague_of = "colleague_of"
    manager_of = "manager_of"
    reports_to = "reports_to"
    mentor_of = "mentor_of"
    mentee_of = "mentee_of"
    collaborator_of = "collaborator_of"
    co_founder_of = "co_founder_of"
    co_investor_with = "co_investor_with"
    client_of = "client_of"
    vendor_of = "vendor_of"
    referrer_of = "referrer_of"
    referred_by = "referred_by"
    introduced = "introduced"
    introduced_by = "introduced_by"
    counsel_for = "counsel_for"
    client_of_counsel = "client_of_counsel"
    witness_for = "witness_for"
    witness_against = "witness_against"
    co_plaintiff_with = "co_plaintiff_with"
    co_defendant_with = "co_defendant_with"
    opposing_party_to = "opposing_party_to"
    named_in_with = "named_in_with"
    expert_for = "expert_for"
    expert_against = "expert_against"
    executor_for = "executor_for"
    beneficiary_of = "beneficiary_of"
    self = "self"
    emergency_contact_for = "emergency_contact_for"
    next_of_kin_for = "next_of_kin_for"
    power_of_attorney_for = "power_of_attorney_for"
    healthcare_proxy_for = "healthcare_proxy_for"
    knows = "knows"
    met_once = "met_once"
    same_alias_as = "same_alias_as"
    suspected_same_as = "suspected_same_as"


class OrgRelationType(str, enum.Enum):
    parent_of = "parent_of"
    subsidiary_of = "subsidiary_of"
    acquired = "acquired"
    acquired_by = "acquired_by"
    merged_with = "merged_with"
    spun_off_from = "spun_off_from"
    spun_off = "spun_off"
    partnership_with = "partnership_with"
    vendor_to = "vendor_to"
    customer_of = "customer_of"
    competitor_of = "competitor_of"
    franchise_of = "franchise_of"
    member_of_group = "member_of_group"
    jv_partner_of = "jv_partner_of"
    licensor_of = "licensor_of"
    licensee_of = "licensee_of"
    distributor_of = "distributor_of"
    reseller_of = "reseller_of"
    successor_of = "successor_of"
    predecessor_of = "predecessor_of"


class EmailType(str, enum.Enum):
    personal = "personal"
    work = "work"
    school = "school"
    other = "other"
    alias = "alias"


class EmailDeliverability(str, enum.Enum):
    unknown = "unknown"
    valid = "valid"
    invalid = "invalid"
    risky = "risky"
    accept_all = "accept_all"
    disposable = "disposable"


class PhoneType(str, enum.Enum):
    mobile = "mobile"
    home = "home"
    work = "work"
    fax = "fax"
    main = "main"
    other = "other"


class LineType(str, enum.Enum):
    mobile = "mobile"
    landline = "landline"
    voip = "voip"
    toll_free = "toll_free"
    satellite = "satellite"
    unknown = "unknown"


class AddressType(str, enum.Enum):
    home = "home"
    work = "work"
    mailing = "mailing"
    billing = "billing"
    shipping = "shipping"
    other = "other"


class AddressVerifiedVia(str, enum.Enum):
    unverified = "unverified"
    usps = "usps"
    smarty = "smarty"
    manual = "manual"
    self_provided = "self_provided"
    arrived_mail = "arrived_mail"


class GeoPrecision(str, enum.Enum):
    rooftop = "rooftop"
    range = "range"
    street = "street"
    locality = "locality"
    region = "region"
    country = "country"
    unknown = "unknown"


class TrustTier(str, enum.Enum):
    propose_only = "propose_only"
    auto_apply_low = "auto_apply_low"
    auto_apply_high = "auto_apply_high"
    authoritative = "authoritative"
    suspended = "suspended"


class RetentionClass(str, enum.Enum):
    ephemeral_30d = "ephemeral_30d"
    operational_2y = "operational_2y"
    indefinite = "indefinite"
    hipaa_6y = "hipaa_6y"
    legal_hold = "legal_hold"


class VisibilityScope(str, enum.Enum):
    private = "private"
    team = "team"
    org = "org"
    shared = "shared"
