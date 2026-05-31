"""CardDAV FastAPI router (RFC 6352).

Mounts at ``/carddav`` and implements the HTTP verb set CardDAV
clients (iOS Contacts.app, macOS Contacts.app, Thunderbird CardBook)
expect:

  * ``OPTIONS``                                  — DAV class advertisement
  * ``PROPFIND``                                 — principal / home-set
                                                   / addressbook / resource
  * ``GET``                                      — vCard body
  * ``PUT``                                      — create / update vCard
  * ``DELETE``                                   — soft-delete vCard
  * ``REPORT``                                   — addressbook-query +
                                                   addressbook-multiget +
                                                   sync-collection (stub)

``/.well-known/carddav`` redirects to ``/carddav/`` so an iPhone can
resolve a server simply by entering the hostname.

Auth is HTTP Basic over per-device app passwords (see
:mod:`contact_ops.carddav.auth`). The router runs that check inline
because PROPFIND/REPORT aren't standard FastAPI verbs and would
bypass any dependency-based auth.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from lxml import etree
from sqlalchemy import select

from contact_ops.carddav import addressbook as ab
from contact_ops.carddav import reports
from contact_ops.carddav.auth import (
    CarddavPrincipal,
    auth_session,
    bind_tenant,
    resolve_principal,
)
from contact_ops.carddav.etag import (
    collection_etag,
    enforce_preconditions,
    quote_etag,
)
from contact_ops.carddav.vcard_serialize import serialize_canonical_to_vcard
from contact_ops.models import Person


logger = structlog.get_logger(__name__)


carddav_router = APIRouter()


# ---------- .well-known shortcut ----------


@carddav_router.get("/.well-known/carddav", include_in_schema=False)
async def well_known_carddav() -> RedirectResponse:
    """Redirect ``/.well-known/carddav`` to the real mount per RFC 6764."""

    return RedirectResponse(url="/carddav/", status_code=status.HTTP_301_MOVED_PERMANENTLY)


# ---------- OPTIONS ----------


_DAV_CLASSES = "1, 2, 3, addressbook, extended-mkcol"
_ALLOWED_METHODS = "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, REPORT, COPY, MOVE"


def _options_response() -> Response:
    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "DAV": _DAV_CLASSES,
            "Allow": _ALLOWED_METHODS,
            "MS-Author-Via": "DAV",
            "Accept-Ranges": "none",
        },
    )


@carddav_router.options("/carddav", include_in_schema=False)
@carddav_router.options("/carddav/", include_in_schema=False)
async def options_root() -> Response:
    return _options_response()


@carddav_router.options("/carddav/{tail:path}", include_in_schema=False)
async def options_any(tail: str) -> Response:
    return _options_response()


# ---------- PROPFIND ----------


@carddav_router.api_route("/carddav", methods=["PROPFIND"], include_in_schema=False)
@carddav_router.api_route("/carddav/", methods=["PROPFIND"], include_in_schema=False)
async def propfind_root(request: Request) -> Response:
    principal = await resolve_principal(request)
    body = await request.body()
    requested = reports.parse_propfind_body(body)

    multistatus = reports.new_multistatus()
    resp = reports.add_response(multistatus, href="/carddav/")
    prop = reports.add_propstat(resp, status_code=200, status_phrase="OK")
    info = reports.CollectionInfo(
        href="/carddav/",
        display_name="Contact-Ops CardDAV",
        description="Root principal collection",
        sync_token="",
        etag="",
        is_addressbook=False,
        principal_href=reports.principal_href(base="/carddav", user_id=principal.user_id),
    )
    reports.populate_collection_props(prop, info=info, requested=requested)

    user_resp = reports.add_response(
        multistatus,
        href=reports.principal_href(base="/carddav", user_id=principal.user_id),
    )
    user_prop = reports.add_propstat(user_resp, status_code=200, status_phrase="OK")
    user_info = reports.CollectionInfo(
        href=reports.principal_href(base="/carddav", user_id=principal.user_id),
        display_name=f"Principal for {principal.user_id}",
        description="User principal home",
        sync_token="",
        etag="",
        is_addressbook=False,
        principal_href=reports.principal_href(base="/carddav", user_id=principal.user_id),
    )
    reports.populate_collection_props(user_prop, info=user_info, requested=requested)

    return _multistatus_response(multistatus)


@carddav_router.api_route(
    "/carddav/{user_id}", methods=["PROPFIND"], include_in_schema=False
)
@carddav_router.api_route(
    "/carddav/{user_id}/", methods=["PROPFIND"], include_in_schema=False
)
async def propfind_user(user_id: str, request: Request) -> Response:
    principal = await resolve_principal(request)
    _assert_principal_owns(principal, user_id=user_id)
    body = await request.body()
    requested = reports.parse_propfind_body(body)

    multistatus = reports.new_multistatus()
    user_href = reports.principal_href(base="/carddav", user_id=principal.user_id)
    resp = reports.add_response(multistatus, href=user_href)
    prop = reports.add_propstat(resp, status_code=200, status_phrase="OK")
    info = reports.CollectionInfo(
        href=user_href,
        display_name=f"Principal for {principal.user_id}",
        description="User principal home",
        sync_token="",
        etag="",
        is_addressbook=False,
        principal_href=user_href,
    )
    reports.populate_collection_props(prop, info=info, requested=requested)

    depth = (request.headers.get("depth") or "0").lower()
    if depth in ("1", "infinity"):
        ab_href = reports.addressbook_href(
            base="/carddav",
            user_id=principal.user_id,
            tenant_slug=principal.tenant_slug,
        )
        ab_resp = reports.add_response(multistatus, href=ab_href)
        ab_prop = reports.add_propstat(ab_resp, status_code=200, status_phrase="OK")
        ab_info = reports.CollectionInfo(
            href=ab_href,
            display_name=f"{principal.tenant_slug} addressbook",
            description=f"Contact-Ops addressbook for {principal.tenant_slug}",
            sync_token="",
            etag="",
            is_addressbook=True,
            principal_href=user_href,
        )
        reports.populate_collection_props(ab_prop, info=ab_info, requested=requested)

    return _multistatus_response(multistatus)


@carddav_router.api_route(
    "/carddav/{user_id}/{tenant_slug}",
    methods=["PROPFIND"],
    include_in_schema=False,
)
@carddav_router.api_route(
    "/carddav/{user_id}/{tenant_slug}/",
    methods=["PROPFIND"],
    include_in_schema=False,
)
async def propfind_addressbook(
    user_id: str,
    tenant_slug: str,
    request: Request,
) -> Response:
    principal = await resolve_principal(request)
    _assert_principal_owns(principal, user_id=user_id, tenant_slug=tenant_slug)
    body = await request.body()
    requested = reports.parse_propfind_body(body)

    multistatus = reports.new_multistatus()
    ab_href = reports.addressbook_href(
        base="/carddav",
        user_id=principal.user_id,
        tenant_slug=principal.tenant_slug,
    )
    user_href = reports.principal_href(base="/carddav", user_id=principal.user_id)

    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        members = await ab.list_addressbook_members(session, tenant_id=principal.tenant_id)

    coll_etag = collection_etag(m.etag for m in members)
    resp = reports.add_response(multistatus, href=ab_href)
    prop = reports.add_propstat(resp, status_code=200, status_phrase="OK")
    info = reports.CollectionInfo(
        href=ab_href,
        display_name=f"{principal.tenant_slug} addressbook",
        description=f"Contact-Ops addressbook for {principal.tenant_slug}",
        sync_token=f"data:,sync-{coll_etag}",
        etag=coll_etag,
        is_addressbook=True,
        principal_href=user_href,
    )
    reports.populate_collection_props(prop, info=info, requested=requested)

    depth = (request.headers.get("depth") or "0").lower()
    if depth in ("1", "infinity"):
        for member in members:
            vc_href = reports.vcard_href(
                base="/carddav",
                user_id=principal.user_id,
                tenant_slug=principal.tenant_slug,
                vcard_uid=member.vcard_uid,
            )
            m_resp = reports.add_response(multistatus, href=vc_href)
            m_prop = reports.add_propstat(m_resp, status_code=200, status_phrase="OK")
            m_info = reports.ResourceInfo(
                href=vc_href,
                etag=member.etag,
                last_modified=_ensure_utc(member.last_modified),
                content_length=0,
            )
            reports.populate_resource_props(m_prop, info=m_info, requested=requested)

    return _multistatus_response(multistatus)


@carddav_router.api_route(
    "/carddav/{user_id}/{tenant_slug}/{vcard_uid_with_ext:path}",
    methods=["PROPFIND"],
    include_in_schema=False,
)
async def propfind_vcard(
    user_id: str,
    tenant_slug: str,
    vcard_uid_with_ext: str,
    request: Request,
) -> Response:
    principal = await resolve_principal(request)
    _assert_principal_owns(principal, user_id=user_id, tenant_slug=tenant_slug)
    vcard_uid = _strip_vcf(vcard_uid_with_ext)
    body = await request.body()
    requested = reports.parse_propfind_body(body)

    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        person = await _find_person_by_uid(session, vcard_uid=vcard_uid)
        if person is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="vcard not found")
        canonical = await ab.load_canonical_from_person(
            session, person_id=person.id, tenant_id=principal.tenant_id
        )

    body_bytes = b"" if canonical is None else serialize_canonical_to_vcard(canonical).encode("utf-8")
    multistatus = reports.new_multistatus()
    href = reports.vcard_href(
        base="/carddav",
        user_id=principal.user_id,
        tenant_slug=principal.tenant_slug,
        vcard_uid=vcard_uid,
    )
    resp = reports.add_response(multistatus, href=href)
    prop = reports.add_propstat(resp, status_code=200, status_phrase="OK")
    info = reports.ResourceInfo(
        href=href,
        etag=person.etag or str(person.id),
        last_modified=_ensure_utc(person.updated_at or person.created_at),
        content_length=len(body_bytes),
        body=body_bytes if (requested and reports.CR("address-data") in requested) else None,
    )
    reports.populate_resource_props(prop, info=info, requested=requested)
    return _multistatus_response(multistatus)


# ---------- GET ----------


@carddav_router.get(
    "/carddav/{user_id}/{tenant_slug}/{vcard_uid_with_ext:path}",
    include_in_schema=False,
)
async def get_vcard(
    user_id: str,
    tenant_slug: str,
    vcard_uid_with_ext: str,
    request: Request,
) -> Response:
    principal = await resolve_principal(request)
    _assert_principal_owns(principal, user_id=user_id, tenant_slug=tenant_slug)
    vcard_uid = _strip_vcf(vcard_uid_with_ext)

    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        person = await _find_person_by_uid(session, vcard_uid=vcard_uid)
        if person is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="vcard not found")
        canonical = await ab.load_canonical_from_person(
            session, person_id=person.id, tenant_id=principal.tenant_id
        )

    if canonical is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="vcard not found")
    body = serialize_canonical_to_vcard(canonical)
    return Response(
        content=body,
        media_type="text/vcard; charset=utf-8",
        headers={
            "ETag": quote_etag(person.etag or str(person.id)),
            "Last-Modified": _ensure_utc(person.updated_at or person.created_at).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            ),
        },
    )


# ---------- PUT ----------


_MAX_VCARD_BYTES = 256 * 1024


@carddav_router.put(
    "/carddav/{user_id}/{tenant_slug}/{vcard_uid_with_ext:path}",
    include_in_schema=False,
)
async def put_vcard(
    user_id: str,
    tenant_slug: str,
    vcard_uid_with_ext: str,
    request: Request,
) -> Response:
    principal = await resolve_principal(request)
    _assert_principal_owns(principal, user_id=user_id, tenant_slug=tenant_slug)
    vcard_uid = _strip_vcf(vcard_uid_with_ext)

    body = await request.body()
    if len(body) > _MAX_VCARD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"vcard exceeds {_MAX_VCARD_BYTES} bytes",
        )

    if_match = request.headers.get("if-match")
    if_none_match = request.headers.get("if-none-match")

    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        existing = await _find_person_by_uid(session, vcard_uid=vcard_uid)
        current_etag = existing.etag if existing else None
        enforce_preconditions(
            if_match=if_match, if_none_match=if_none_match, current=current_etag
        )

        try:
            text_payload = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="vcard body must be UTF-8",
            ) from exc

        try:
            person, _canonical = await ab.apply_vcard_text_to_db(
                session,
                vcard_text=text_payload,
                principal_tenant_id=principal.tenant_id,
                principal_user_id=principal.user_id,
                target_vcard_uid=vcard_uid,
                existing_person_id=existing.id if existing else None,
            )
        except ab.CrossTenantWriteRefused as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        await session.flush()
        new_etag = person.etag or str(person.id)
        created = existing is None

    return Response(
        status_code=(
            status.HTTP_201_CREATED if created else status.HTTP_204_NO_CONTENT
        ),
        headers={
            "ETag": quote_etag(new_etag),
            "Location": reports.vcard_href(
                base="/carddav",
                user_id=principal.user_id,
                tenant_slug=principal.tenant_slug,
                vcard_uid=vcard_uid,
            ),
        },
    )


# ---------- DELETE ----------


@carddav_router.delete(
    "/carddav/{user_id}/{tenant_slug}/{vcard_uid_with_ext:path}",
    include_in_schema=False,
)
async def delete_vcard(
    user_id: str,
    tenant_slug: str,
    vcard_uid_with_ext: str,
    request: Request,
) -> Response:
    principal = await resolve_principal(request)
    _assert_principal_owns(principal, user_id=user_id, tenant_slug=tenant_slug)
    vcard_uid = _strip_vcf(vcard_uid_with_ext)

    if_match = request.headers.get("if-match")

    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        person = await _find_person_by_uid(session, vcard_uid=vcard_uid)
        current_etag = person.etag if person else None
        enforce_preconditions(if_match=if_match, if_none_match=None, current=current_etag)
        if person is None:
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        await ab.soft_delete_person_for_tenant(
            session, person_id=person.id, tenant_id=principal.tenant_id
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- REPORT ----------


@carddav_router.api_route(
    "/carddav/{user_id}/{tenant_slug}",
    methods=["REPORT"],
    include_in_schema=False,
)
@carddav_router.api_route(
    "/carddav/{user_id}/{tenant_slug}/",
    methods=["REPORT"],
    include_in_schema=False,
)
async def report_addressbook(
    user_id: str,
    tenant_slug: str,
    request: Request,
) -> Response:
    principal = await resolve_principal(request)
    _assert_principal_owns(principal, user_id=user_id, tenant_slug=tenant_slug)

    body = await request.body()
    try:
        root = etree.fromstring(body)
    except etree.XMLSyntaxError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="REPORT body must be valid XML",
        ) from exc

    if root.tag == reports.CR("addressbook-multiget"):
        return await _report_multiget(principal, body)
    if root.tag == reports.CR("addressbook-query"):
        return await _report_query(principal, body)
    if root.tag == reports.D("sync-collection"):
        return await _report_sync_collection(principal, body)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"unsupported REPORT type: {root.tag}",
    )


async def _report_multiget(principal: CarddavPrincipal, body: bytes) -> Response:
    requested, hrefs = reports.parse_multiget_body(body)
    multistatus = reports.new_multistatus()
    if not hrefs:
        return _multistatus_response(multistatus)

    uids = [_uid_from_href(h) for h in hrefs]
    uids = [u for u in uids if u]

    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        for uid in uids:
            person = await _find_person_by_uid(session, vcard_uid=uid)
            href = reports.vcard_href(
                base="/carddav",
                user_id=principal.user_id,
                tenant_slug=principal.tenant_slug,
                vcard_uid=uid,
            )
            if person is None:
                resp = reports.add_response(multistatus, href=href)
                etree.SubElement(resp, reports.D("status")).text = "HTTP/1.1 404 Not Found"
                continue
            canonical = await ab.load_canonical_from_person(
                session, person_id=person.id, tenant_id=principal.tenant_id
            )
            if canonical is None:
                resp = reports.add_response(multistatus, href=href)
                etree.SubElement(resp, reports.D("status")).text = "HTTP/1.1 404 Not Found"
                continue
            vcard_body = serialize_canonical_to_vcard(canonical).encode("utf-8")
            resp = reports.add_response(multistatus, href=href)
            prop = reports.add_propstat(resp, status_code=200, status_phrase="OK")
            info = reports.ResourceInfo(
                href=href,
                etag=person.etag or str(person.id),
                last_modified=_ensure_utc(person.updated_at or person.created_at),
                content_length=len(vcard_body),
                body=vcard_body,
            )
            reports.populate_resource_props(
                prop,
                info=info,
                requested=requested,
                include_address_data=True,
            )

    return _multistatus_response(multistatus)


async def _report_query(principal: CarddavPrincipal, body: bytes) -> Response:
    requested, query = reports.parse_query_body(body)
    multistatus = reports.new_multistatus()

    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        members = await ab.list_addressbook_members(session, tenant_id=principal.tenant_id)

        if query.text_match:
            needle = query.text_match.lower()
            members = [m for m in members if needle in m.display_name.lower()]

        for member in members:
            href = reports.vcard_href(
                base="/carddav",
                user_id=principal.user_id,
                tenant_slug=principal.tenant_slug,
                vcard_uid=member.vcard_uid,
            )
            resp = reports.add_response(multistatus, href=href)
            prop = reports.add_propstat(resp, status_code=200, status_phrase="OK")
            body_bytes: bytes | None = None
            if requested is None or reports.CR("address-data") in requested:
                canonical = await ab.load_canonical_from_person(
                    session, person_id=member.person_id, tenant_id=principal.tenant_id
                )
                if canonical is not None:
                    body_bytes = serialize_canonical_to_vcard(canonical).encode("utf-8")
            info = reports.ResourceInfo(
                href=href,
                etag=member.etag,
                last_modified=_ensure_utc(member.last_modified),
                content_length=len(body_bytes or b""),
                body=body_bytes,
            )
            reports.populate_resource_props(
                prop,
                info=info,
                requested=requested,
                include_address_data=body_bytes is not None,
            )

    return _multistatus_response(multistatus)


async def _report_sync_collection(
    principal: CarddavPrincipal, body: bytes
) -> Response:
    """Phase 2 stub for ``sync-collection`` (RFC 6578).

    Returns the full member set with a fresh sync-token. Phase 3 lands
    real token-based incremental sync.
    """

    multistatus = reports.new_multistatus()
    async with auth_session() as session:
        await bind_tenant(session, principal.tenant_id, uc_uid=principal.user_id)
        members = await ab.list_addressbook_members(session, tenant_id=principal.tenant_id)

    coll_etag = collection_etag(m.etag for m in members)
    for member in members:
        href = reports.vcard_href(
            base="/carddav",
            user_id=principal.user_id,
            tenant_slug=principal.tenant_slug,
            vcard_uid=member.vcard_uid,
        )
        resp = reports.add_response(multistatus, href=href)
        prop = reports.add_propstat(resp, status_code=200, status_phrase="OK")
        info = reports.ResourceInfo(
            href=href,
            etag=member.etag,
            last_modified=_ensure_utc(member.last_modified),
            content_length=0,
        )
        reports.populate_resource_props(prop, info=info)
    etree.SubElement(multistatus, reports.D("sync-token")).text = (
        f"data:,sync-{coll_etag}"
    )
    return _multistatus_response(multistatus)


# ---------- helpers ----------


def _multistatus_response(root: etree._Element) -> Response:
    return Response(
        content=reports.render_multistatus(root),
        media_type='application/xml; charset="utf-8"',
        status_code=status.HTTP_207_MULTI_STATUS,
    )


def _strip_vcf(name: str) -> str:
    decoded = name
    if decoded.lower().endswith(".vcf"):
        decoded = decoded[:-4]
    # CardDAV clients sometimes URL-encode the ``urn:uuid:`` colon.
    return decoded


def _uid_from_href(href: str) -> str | None:
    """Extract the vCard UID from a CardDAV resource href."""

    cleaned = href.rstrip("/")
    if not cleaned:
        return None
    tail = cleaned.rsplit("/", 1)[-1]
    return _strip_vcf(tail) or None


async def _find_person_by_uid(
    session: Any,
    *,
    vcard_uid: str,
) -> Person | None:
    """Resolve a Person row by ``vcard_uid``. Tries the UUID-form first."""

    if not vcard_uid:
        return None
    # Direct vcard_uid match (this is the production happy path).
    result = await session.scalar(
        select(Person).where(Person.vcard_uid == vcard_uid)
    )
    if result is not None:
        return result
    # Legacy migration fallback — accept a bare UUID (matches Person.id).
    try:
        candidate = uuid.UUID(vcard_uid.replace("urn:uuid:", "", 1))
    except ValueError:
        return None
    return await session.get(Person, candidate)


def _assert_principal_owns(
    principal: CarddavPrincipal,
    *,
    user_id: str | None = None,
    tenant_slug: str | None = None,
) -> None:
    """Raise 403 when the URL path doesn't belong to the authenticated user.

    The URL ``/carddav/<user_id>/<tenant_slug>/...`` is informational; we
    do not let it override the principal's tenant/user binding (otherwise
    a user with an app password for tenant A could browse tenant B by
    guessing the path).
    """

    if user_id is not None and user_id != principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="path user does not match authenticated principal",
        )
    if tenant_slug is not None and tenant_slug != principal.tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="path tenant does not match authenticated principal",
        )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
