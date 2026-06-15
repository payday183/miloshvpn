from dataclasses import dataclass, replace
from datetime import timedelta
from html import escape
import base64
import json
import logging
import secrets
from typing import Any, Literal
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import DirectInboundSlot, DirectNode, DirectSubscription, DirectUserProfile, Subscription, User, VpnKey
from app.services.x3ui import DEFAULT_REALITY_FLOW, X3UIClient, X3UIError
from app.timeutils import utcnow

DirectStatus = Literal["ok", "warn", "error"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectProtocolTemplate:
    template_code: str
    protocol: str
    x3ui_protocol: str
    transport: str
    security: str
    reality_public_key: str = ""
    reality_private_key: str = ""
    reality_dest: str = ""
    reality_utls: str = ""
    reality_spider_x: str = "/"
    reality_short_id: str = ""
    xhttp_path: str = "/"
    xhttp_mode: str = "auto"
    xhttp_padding_bytes: str = "100-1000"


@dataclass(frozen=True)
class DirectNodeCheck:
    status: DirectStatus
    title: str
    detail: str


@dataclass(frozen=True)
class DirectNodePlannedSlot:
    slot_number: int
    template_code: str
    protocol: str
    port: int
    name: str


@dataclass(frozen=True)
class DirectNodeAuditResult:
    enabled_for_create: bool
    dry_run: bool
    node_id: str
    checks: list[DirectNodeCheck]
    planned_slots: list[DirectNodePlannedSlot]


@dataclass(frozen=True)
class DirectNodeProvisionResult:
    created: bool
    reused: bool
    subscription_url: str
    profiles_count: int
    checks: list[DirectNodeCheck]


@dataclass(frozen=True)
class ProvisionedDirectProfile:
    slot: DirectInboundSlot
    template: DirectProtocolTemplate
    inbound_id: int
    client_id: str
    uuid_or_password: str
    public_link: str


@dataclass(frozen=True)
class DirectNodeConfig:
    source: str
    node_id: str
    name: str
    country_code: str
    country_flag: str
    public_host: str
    public_ip: str
    api_base_url: str
    api_username: str
    api_password: str
    api_verify_tls: bool
    api_timeout_seconds: int
    agent_url: str
    agent_token: str
    agent_verify_tls: bool
    agent_timeout_seconds: int
    vpn_port_min: int
    vpn_port_max: int
    reserved_ports: str
    status: str = "unknown"


async def run_admin_direct_node_audit(session: AsyncSession, admin_user: User) -> DirectNodeAuditResult:
    settings = get_settings()
    checks: list[DirectNodeCheck] = []

    def add_check(status: DirectStatus, title: str, detail: str) -> None:
        checks.append(DirectNodeCheck(status=status, title=title, detail=detail))

    add_static_checks(settings, admin_user, add_check)
    node_config = await admin_test_node_config(session, settings)
    if node_config is None:
        add_check("error", "direct node", f"узел {settings.admin_test_node_id} не найден в env или БД")

    templates = direct_admin_templates(settings)
    validate_templates(settings, node_config, templates, add_check)
    planned_slots = []
    if node_config is not None:
        planned_slots = await plan_direct_node_slots(session, settings, node_config, templates, add_check)
        await audit_node_3xui(node_config, add_check)
        await audit_node_agent(settings, node_config, add_check)

    return DirectNodeAuditResult(
        enabled_for_create=direct_create_flags_ok(settings),
        dry_run=settings.dry_run_first,
        node_id=settings.admin_test_node_id,
        checks=checks,
        planned_slots=planned_slots,
    )


async def run_admin_direct_node_create(session: AsyncSession, admin_user: User) -> DirectNodeProvisionResult:
    settings = get_settings()
    audit = await run_admin_direct_node_audit(session, admin_user)
    checks = list(audit.checks)

    if audit.dry_run:
        raise RuntimeError("DRY_RUN_FIRST=true: сначала смотрим audit, создание заблокировано")
    if not direct_create_flags_ok(settings):
        raise RuntimeError("Direct-node create закрыт флагами ADMIN_TEST/ENABLE_DIRECT_NODE/ADMIN_TEST_CREATE_SUB")
    if any(check.status == "error" for check in audit.checks):
        raise RuntimeError("Direct-node audit содержит ошибки, создание остановлено")

    node_config = await admin_test_node_config(session, settings)
    if node_config is None:
        raise RuntimeError(f"Direct-node узел {settings.admin_test_node_id} не найден")

    existing_subscription = await active_direct_subscription(session, admin_user.id, node_config.node_id)
    existing_profiles = await active_direct_profiles(session, admin_user.id, node_config.node_id)
    if existing_subscription is not None and len(existing_profiles) >= settings.admin_test_profiles_count:
        return DirectNodeProvisionResult(
            created=False,
            reused=True,
            subscription_url=existing_subscription.subscription_url,
            profiles_count=len(existing_profiles),
            checks=checks,
        )

    templates = direct_admin_templates(settings)
    await upsert_direct_node(session, node_config, status="provisioning")
    await session.flush()

    x3ui = x3ui_client_for_config(node_config, settings)
    agent = DirectNodeAgent(config=node_config) if settings.enable_node_agent and node_config.agent_url.strip() else None
    if agent is None:
        checks.append(
            DirectNodeCheck(
                status="warn",
                title="node-agent",
                detail="пропускаю port check/firewall/tc, создаю только inbound-ы в 3x-ui",
            )
        )
    created_profiles: list[DirectUserProfile] = []
    created_slots: list[tuple[DirectInboundSlot, DirectProtocolTemplate]] = []
    created_remote_inbound_ids: list[int] = []

    try:
        for planned_slot in audit.planned_slots:
            template = prepare_direct_template(next(item for item in templates if item.template_code == planned_slot.template_code))
            existing_profile = next(
                (profile for profile in existing_profiles if profile.template_code == template.template_code),
                None,
            )
            if existing_profile is not None:
                continue

            if agent is not None:
                await agent.ensure_port_free(planned_slot.port)
            slot = await reserve_direct_slot(session, settings, node_config, admin_user, planned_slot)
            if slot.inbound_id is None:
                inbound_id = await create_remote_inbound_for_slot(x3ui, settings, node_config, slot, template)
                created_remote_inbound_ids.append(inbound_id)
                slot.inbound_id = inbound_id
            slot.updated_at = utcnow()
            if agent is not None:
                await agent.open_port(planned_slot.port)
                await agent.apply_tc_limit(planned_slot.port, settings.profile_speed_limit_mbit)
            created_slots.append((slot, template))

        if created_slots:
            direct_email = f"direct_admin_{admin_user.id}_{node_config.node_id}"
            provisioned_client = await x3ui.create_subscription_client(
                email=direct_email,
                telegram_id=admin_user.telegram_id,
                expires_at=utcnow() + timedelta(days=settings.free_trial_days),
                traffic_gb=None,
                inbound_ids=[slot.inbound_id for slot, _ in created_slots if slot.inbound_id],
                limit_ip=settings.x3ui_admin_limit_ip,
            )
            for slot, template in created_slots:
                public_link = direct_link_for_slot(slot, provisioned_client.links, node_config)
                if public_link is None:
                    if template.x3ui_protocol != "vless":
                        raise X3UIError(f"3x-ui did not return a subscription link for inbound {slot.inbound_id}")
                    public_link = build_public_link(
                        settings,
                        node_config,
                        slot,
                        template,
                        provisioned_client.client_uuid,
                        provisioned_client.client_uuid,
                    )
                profile = await save_direct_profile(
                    session,
                    admin_user,
                    slot=slot,
                    template=template,
                    client_id=provisioned_client.client_uuid,
                    secret_value=provisioned_client.client_uuid,
                    public_link=public_link,
                )
                created_profiles.append(profile)

        subscription = await create_or_get_direct_subscription(session, settings, node_config, admin_user)
        await upsert_direct_node(session, node_config, status="online")
        await session.commit()
    except Exception:
        for inbound_id in reversed(created_remote_inbound_ids):
            try:
                await delete_x3ui_inbound(x3ui, inbound_id)
            except Exception:
                logger.exception("Failed to cleanup direct-node inbound %s after provisioning error", inbound_id)
        await session.rollback()
        raise

    checks.append(
        DirectNodeCheck(
            status="ok",
            title="created profiles",
            detail=f"{len(created_profiles)} новых, всего sub будет отдавать 5 профилей",
        )
    )
    return DirectNodeProvisionResult(
        created=bool(created_profiles),
        reused=False,
        subscription_url=subscription.subscription_url,
        profiles_count=len(await active_direct_profiles(session, admin_user.id, node_config.node_id)),
        checks=checks,
    )


def direct_node_users_enabled(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return direct_node_user_provisioning_requested(settings) and bool(settings.subscription_base_url.strip())


def direct_node_user_provisioning_requested(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return (
        settings.enable_direct_node_provisioning
        and settings.node_provisioning_mode == "direct_node"
        and not settings.dry_run_first
    )


async def create_or_replace_user_direct_key(
    session: AsyncSession,
    user: User,
    subscription: Subscription,
) -> VpnKey:
    settings = get_settings()
    if not direct_node_user_provisioning_requested(settings):
        raise RuntimeError("Direct-node user provisioning is disabled by env flags")
    direct_subscription_url(settings, "self-check", public_only=True)

    node_config = await default_user_direct_node_config(session, settings)
    if node_config is None:
        raise RuntimeError("Direct-node user node is not configured")

    templates = direct_admin_templates(settings)
    if settings.admin_test_profiles_count != len(templates):
        raise RuntimeError("Direct-node profiles count does not match configured templates")

    await upsert_direct_node(session, node_config, status="provisioning")
    await session.flush()

    x3ui = x3ui_client_for_config(node_config, settings)
    created_remote_inbound_ids: list[int] = []
    try:
        slots_with_templates = await assigned_slots_for_user(session, user.id, node_config.node_id, templates)
        if len(slots_with_templates) < len(templates):
            planned_slots = await plan_direct_node_slots(
                session,
                settings,
                node_config,
                templates,
                lambda *_: None,
            )
            if len(planned_slots) < len(templates):
                raise RuntimeError("Direct-node port allocator did not return enough slots")

            slots_with_templates = []
            for planned_slot in planned_slots:
                template = prepare_direct_template(
                    next(item for item in templates if item.template_code == planned_slot.template_code)
                )
                slot = await reserve_direct_slot(session, settings, node_config, user, planned_slot)
                if slot.inbound_id is None:
                    inbound_id = await create_remote_inbound_for_slot(x3ui, settings, node_config, slot, template)
                    created_remote_inbound_ids.append(inbound_id)
                    slot.inbound_id = inbound_id
                slot.updated_at = utcnow()
                slots_with_templates.append((slot, template))

        direct_email = f"direct_user_{user.id}_{node_config.node_id}"
        for stale_email in (direct_email, f"direct_admin_{user.id}_{node_config.node_id}"):
            try:
                await x3ui.revoke_client(client_uuid="", email=stale_email)
            except Exception:
                pass

        provisioned_client = await x3ui.create_subscription_client(
            email=direct_email,
            telegram_id=user.telegram_id,
            expires_at=subscription.expires_at,
            traffic_gb=subscription.traffic_limit_gb,
            inbound_ids=[slot.inbound_id for slot, _ in slots_with_templates if slot.inbound_id],
            limit_ip=settings.direct_node_user_limit_ip,
        )
        await revoke_user_direct_or_system_keys(session, user.id, node_config)
        direct_subscription = await create_or_get_direct_subscription(
            session,
            settings,
            node_config,
            user,
            public_only=True,
        )

        await upsert_direct_profiles_for_client(
            session,
            user,
            node_config=node_config,
            slots_with_templates=slots_with_templates,
            provisioned_client=provisioned_client,
        )

        key = VpnKey(
            node_id=None,
            user_id=user.id,
            subscription_id=subscription.id,
            key_type="private",
            x3ui_client_uuid=provisioned_client.client_uuid,
            x3ui_sub_id=provisioned_client.sub_id,
            x3ui_inbound_ids=list(provisioned_client.inbound_ids),
            server_label=direct_server_label(node_config, len(slots_with_templates)),
            limit_ip=settings.direct_node_user_limit_ip,
            email=provisioned_client.email,
            vless_uri=direct_subscription.subscription_url,
            active=True,
            created_at=utcnow(),
            expires_at=subscription.expires_at,
        )
        session.add(key)
        await upsert_direct_node(session, node_config, status="online")
        await session.flush()
        return key
    except Exception:
        for inbound_id in reversed(created_remote_inbound_ids):
            try:
                await delete_x3ui_inbound(x3ui, inbound_id)
            except Exception:
                logger.exception("Failed to cleanup user direct-node inbound %s after provisioning error", inbound_id)
        raise


async def default_user_direct_node_config(session: AsyncSession, settings: Settings) -> DirectNodeConfig | None:
    return await admin_test_node_config(session, settings)


async def assigned_slots_for_user(
    session: AsyncSession,
    user_id: int,
    node_id: str,
    templates: list[DirectProtocolTemplate],
) -> list[tuple[DirectInboundSlot, DirectProtocolTemplate]]:
    template_by_code = {template.template_code: template for template in templates}
    slots = (
        await session.scalars(
            select(DirectInboundSlot)
            .where(
                DirectInboundSlot.node_id == node_id,
                DirectInboundSlot.assigned_user_id == user_id,
                DirectInboundSlot.status == "assigned",
                DirectInboundSlot.inbound_id.is_not(None),
            )
            .order_by(DirectInboundSlot.slot_number)
            .with_for_update()
        )
    ).all()
    result: list[tuple[DirectInboundSlot, DirectProtocolTemplate]] = []
    for slot in slots:
        template = template_by_code.get(slot.template_code)
        if template is not None:
            result.append((slot, template))
    return result


async def revoke_user_direct_or_system_keys(
    session: AsyncSession,
    user_id: int,
    node_config: DirectNodeConfig,
) -> None:
    keys = (
        await session.scalars(
            select(VpnKey)
            .where(VpnKey.user_id == user_id, VpnKey.key_type == "private", VpnKey.active.is_(True))
            .with_for_update()
        )
    ).all()
    now = utcnow()
    for key in keys:
        try:
            await revoke_key_remote(session, key, node_config=node_config)
        finally:
            key.active = False
            key.revoked_at = now


async def revoke_key_remote(
    session: AsyncSession,
    key: VpnKey,
    *,
    node_config: DirectNodeConfig | None = None,
) -> None:
    if is_direct_vpn_key(key):
        config = node_config or await direct_node_config_for_key(session, key)
        if config is None:
            raise RuntimeError("Direct-node config for key was not found")
        await x3ui_client_for_config(config).revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
        return
    await X3UIClient().revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)


def is_direct_vpn_key(key: VpnKey) -> bool:
    return key.email.startswith(("direct_user_", "direct_admin_")) or "/sub/direct/" in (key.vless_uri or "")


async def direct_node_config_for_key(session: AsyncSession, key: VpnKey) -> DirectNodeConfig | None:
    subscription = await session.scalar(
        select(DirectSubscription)
        .where(DirectSubscription.user_id == key.user_id, DirectSubscription.status == "active")
        .order_by(DirectSubscription.updated_at.desc(), DirectSubscription.id.desc())
    )
    if subscription is not None:
        return await direct_node_config_by_id(session, subscription.node_id)
    if "_" in key.email:
        return await direct_node_config_by_id(session, key.email.rsplit("_", 1)[-1])
    return None


async def direct_node_id_for_key(session: AsyncSession, key: VpnKey) -> str | None:
    subscription = await session.scalar(
        select(DirectSubscription)
        .where(DirectSubscription.user_id == key.user_id)
        .order_by(DirectSubscription.updated_at.desc(), DirectSubscription.id.desc())
    )
    if subscription is not None:
        return subscription.node_id
    if "_" in key.email:
        return key.email.rsplit("_", 1)[-1]
    return None


async def release_direct_key_slots(
    session: AsyncSession,
    key: VpnKey,
    *,
    status: str = "expired",
    release_slots: bool = True,
    delete_remote_inbounds: bool = False,
) -> None:
    if not is_direct_vpn_key(key):
        return
    node_id = await direct_node_id_for_key(session, key)
    if node_id is None:
        return

    now = utcnow()
    subscriptions = (
        await session.scalars(
            select(DirectSubscription).where(
                DirectSubscription.user_id == key.user_id,
                DirectSubscription.node_id == node_id,
                DirectSubscription.status == "active",
            )
        )
    ).all()
    for subscription in subscriptions:
        subscription.status = status
        subscription.updated_at = now

    profiles = (
        await session.scalars(
            select(DirectUserProfile)
            .where(
                DirectUserProfile.user_id == key.user_id,
                DirectUserProfile.node_id == node_id,
                DirectUserProfile.status == "active",
            )
            .with_for_update()
        )
    ).all()
    slot_ids = [profile.inbound_slot_id for profile in profiles]
    for profile in profiles:
        profile.status = status
        profile.updated_at = now

    if slot_ids and (release_slots or delete_remote_inbounds):
        slots = (
            await session.scalars(
                select(DirectInboundSlot)
                .where(
                    DirectInboundSlot.id.in_(slot_ids),
                    DirectInboundSlot.assigned_user_id == key.user_id,
                    DirectInboundSlot.status == "assigned",
                )
                .with_for_update()
            )
        ).all()
        x3ui = None
        if delete_remote_inbounds:
            config = await direct_node_config_by_id(session, node_id)
            if config is None:
                raise RuntimeError("Direct-node config for key slots was not found")
            x3ui = x3ui_client_for_config(config)
        for slot in slots:
            if delete_remote_inbounds and slot.inbound_id is not None and x3ui is not None:
                await delete_x3ui_inbound(x3ui, slot.inbound_id)
                slot.inbound_id = None
            if release_slots:
                slot.status = "free"
                slot.assigned_user_id = None
            slot.updated_at = now

    await session.flush()


async def direct_node_config_by_id(session: AsyncSession, node_id: str) -> DirectNodeConfig | None:
    settings = get_settings()
    env_config = env_direct_node_config(settings)
    if node_id == env_config.node_id:
        return env_config
    node = await session.get(DirectNode, node_id)
    return db_direct_node_config(node) if node is not None else None


async def upsert_direct_profiles_for_client(
    session: AsyncSession,
    user: User,
    *,
    node_config: DirectNodeConfig,
    slots_with_templates: list[tuple[DirectInboundSlot, DirectProtocolTemplate]],
    provisioned_client,
) -> None:
    existing_profiles = {
        profile.inbound_slot_id: profile
        for profile in await active_direct_profiles(session, user.id, node_config.node_id)
    }
    for slot, template in slots_with_templates:
        public_link = direct_link_for_slot(slot, provisioned_client.links, node_config)
        if public_link is None:
            if template.x3ui_protocol != "vless":
                raise X3UIError(f"3x-ui did not return a subscription link for inbound {slot.inbound_id}")
            public_link = build_public_link(
                get_settings(),
                node_config,
                slot,
                template,
                provisioned_client.client_uuid,
                provisioned_client.client_uuid,
            )

        profile = existing_profiles.get(slot.id)
        if profile is None:
            await save_direct_profile(
                session,
                user,
                slot=slot,
                template=template,
                client_id=provisioned_client.client_uuid,
                secret_value=provisioned_client.client_uuid,
                public_link=public_link,
            )
            continue
        profile.template_code = template.template_code
        profile.protocol = template.protocol
        profile.inbound_id = slot.inbound_id
        profile.port = slot.port
        profile.client_id = provisioned_client.client_uuid
        profile.uuid_or_password = provisioned_client.client_uuid
        profile.public_link = public_link
        profile.status = "active"
        profile.updated_at = utcnow()
    await session.flush()


def direct_server_label(node_config: DirectNodeConfig, profiles_count: int) -> str:
    return "MiloshVPN"


def env_direct_node_config(settings: Settings | None = None) -> DirectNodeConfig:
    settings = settings or get_settings()
    return DirectNodeConfig(
        source="env",
        node_id=settings.node_de_1_id,
        name=settings.node_de_1_name,
        country_code=settings.node_de_1_country_code,
        country_flag=settings.node_de_1_country_flag,
        public_host=settings.node_de_1_public_host,
        public_ip=settings.node_de_1_public_ip,
        api_base_url=settings.node_de_1_3xui_base_url,
        api_username=settings.node_de_1_3xui_username,
        api_password=settings.node_de_1_3xui_password,
        api_verify_tls=settings.node_de_1_3xui_verify_tls,
        api_timeout_seconds=settings.node_de_1_3xui_timeout_seconds,
        agent_url=settings.node_de_1_agent_url,
        agent_token=settings.node_de_1_agent_token,
        agent_verify_tls=settings.node_de_1_agent_verify_tls,
        agent_timeout_seconds=settings.node_de_1_agent_timeout_seconds,
        vpn_port_min=settings.node_de_1_vpn_port_min,
        vpn_port_max=settings.node_de_1_vpn_port_max,
        reserved_ports=settings.node_de_1_reserved_ports,
    )


def db_direct_node_config(node: DirectNode) -> DirectNodeConfig:
    return DirectNodeConfig(
        source="db",
        node_id=node.id,
        name=node.name,
        country_code=node.country_code,
        country_flag=node.country_flag,
        public_host=node.public_host,
        public_ip=node.public_ip,
        api_base_url=node.api_base_url,
        api_username=node.api_username,
        api_password=node.api_password,
        api_verify_tls=node.api_verify_tls,
        api_timeout_seconds=node.api_timeout_seconds,
        agent_url=node.agent_url,
        agent_token=node.agent_token,
        agent_verify_tls=node.agent_verify_tls,
        agent_timeout_seconds=node.agent_timeout_seconds,
        vpn_port_min=node.vpn_port_min,
        vpn_port_max=node.vpn_port_max,
        reserved_ports=node.reserved_ports,
        status=node.status,
    )


async def list_direct_node_configs(session: AsyncSession) -> list[DirectNodeConfig]:
    configs = [env_direct_node_config()]
    nodes = (await session.scalars(select(DirectNode).order_by(DirectNode.id))).all()
    configs.extend(db_direct_node_config(node) for node in nodes)
    return configs


async def admin_test_node_config(session: AsyncSession, settings: Settings) -> DirectNodeConfig | None:
    env_config = env_direct_node_config(settings)
    if settings.admin_test_node_id == env_config.node_id:
        return env_config

    node = await session.get(DirectNode, settings.admin_test_node_id)
    return db_direct_node_config(node) if node is not None else None


async def check_direct_node_connection(config: DirectNodeConfig) -> list[DirectNodeCheck]:
    checks: list[DirectNodeCheck] = []

    def add(status: DirectStatus, title: str, detail: str) -> None:
        checks.append(DirectNodeCheck(status, title, detail))

    if not config.api_base_url.strip():
        add("error", "3x-ui URL", "не задан")
    if not config.api_username.strip() or not config.api_password.strip():
        add("error", "3x-ui auth", "username/password не заданы")
    if config.vpn_port_min > config.vpn_port_max:
        add("error", "port range", "min больше max")
    else:
        add("ok", "port range", f"{config.vpn_port_min}-{config.vpn_port_max}")

    if any(check.status == "error" for check in checks):
        return checks

    try:
        inbounds = await x3ui_client_for_config(config).list_inbounds()
        add("ok", "3x-ui API", f"reachable, inbound count={len(inbounds)}")
    except Exception as exc:
        add("error", "3x-ui API", str(exc)[:180])

    if config.agent_url.strip():
        try:
            await agent_health_for_config(config)
            add("ok", "node-agent", "reachable")
        except Exception as exc:
            add("error", "node-agent", str(exc)[:180])
    else:
        add("warn", "node-agent", "не задан, создание inbound-ов будет заблокировано")
    return checks


async def save_direct_node_config(session: AsyncSession, config: DirectNodeConfig, *, status: str = "configured") -> DirectNode:
    now = utcnow()
    node = await session.get(DirectNode, config.node_id)
    if node is None:
        node = DirectNode(
            id=config.node_id,
            name=config.name,
            country_code=config.country_code,
            country_flag=config.country_flag,
            public_host=config.public_host,
            public_ip=config.public_ip,
            api_base_url=config.api_base_url,
            api_username=config.api_username,
            api_password=config.api_password,
            api_verify_tls=config.api_verify_tls,
            api_timeout_seconds=config.api_timeout_seconds,
            agent_url=config.agent_url,
            agent_token=config.agent_token,
            agent_verify_tls=config.agent_verify_tls,
            agent_timeout_seconds=config.agent_timeout_seconds,
            vpn_port_min=config.vpn_port_min,
            vpn_port_max=config.vpn_port_max,
            reserved_ports=config.reserved_ports,
            status=status,
            last_health_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(node)
    else:
        node.name = config.name
        node.country_code = config.country_code
        node.country_flag = config.country_flag
        node.public_host = config.public_host
        node.public_ip = config.public_ip
        node.api_base_url = config.api_base_url
        node.api_username = config.api_username
        node.api_password = config.api_password
        node.api_verify_tls = config.api_verify_tls
        node.api_timeout_seconds = config.api_timeout_seconds
        node.agent_url = config.agent_url
        node.agent_token = config.agent_token
        node.agent_verify_tls = config.agent_verify_tls
        node.agent_timeout_seconds = config.agent_timeout_seconds
        node.vpn_port_min = config.vpn_port_min
        node.vpn_port_max = config.vpn_port_max
        node.reserved_ports = config.reserved_ports
        node.status = status
        node.last_health_at = now
        node.updated_at = now
    await session.flush()
    return node


async def active_direct_subscription(
    session: AsyncSession,
    user_id: int,
    node_id: str,
) -> DirectSubscription | None:
    return await session.scalar(
        select(DirectSubscription).where(
            DirectSubscription.user_id == user_id,
            DirectSubscription.node_id == node_id,
            DirectSubscription.status == "active",
        )
    )


async def active_direct_profiles(session: AsyncSession, user_id: int, node_id: str) -> list[DirectUserProfile]:
    return list(
        (
            await session.scalars(
                select(DirectUserProfile)
                .where(
                    DirectUserProfile.user_id == user_id,
                    DirectUserProfile.node_id == node_id,
                    DirectUserProfile.status == "active",
                )
                .order_by(DirectUserProfile.id)
            )
        ).all()
    )


def direct_create_flags_ok(settings: Settings) -> bool:
    return (
        settings.admin_test_enabled
        and settings.enable_direct_node_provisioning
        and settings.node_provisioning_mode == "direct_node"
        and settings.admin_test_create_sub
    )


def add_static_checks(settings: Settings, admin_user: User, add_check) -> None:
    add_check(
        "ok" if settings.admin_test_enabled else "warn",
        "ADMIN_TEST_ENABLED",
        "enabled" if settings.admin_test_enabled else "disabled: создание админского direct-node теста закрыто",
    )
    add_check(
        "ok" if settings.enable_direct_node_provisioning else "warn",
        "ENABLE_DIRECT_NODE_PROVISIONING",
        "enabled" if settings.enable_direct_node_provisioning else "disabled: обычная система не затрагивается",
    )
    add_check(
        "ok" if settings.node_provisioning_mode == "direct_node" else "warn",
        "NODE_PROVISIONING_MODE",
        settings.node_provisioning_mode,
    )
    add_check("ok" if settings.dry_run_first else "warn", "DRY_RUN_FIRST", str(settings.dry_run_first).lower())

    if settings.admin_test_telegram_id and settings.admin_test_telegram_id != admin_user.telegram_id:
        add_check("error", "ADMIN_TEST_TELEGRAM_ID", "текущий админ не совпадает с разрешённым test Telegram ID")
    else:
        add_check("ok", "admin scope", "команда вызвана администратором")

    if settings.admin_test_user_id and settings.admin_test_user_id != admin_user.id:
        add_check("error", "ADMIN_TEST_USER_ID", "текущий internal user_id не совпадает с разрешённым test user_id")
    elif settings.admin_test_user_id:
        add_check("ok", "ADMIN_TEST_USER_ID", "совпадает")

    add_check(
        "ok" if settings.port_allocator_scope == "per_node" else "error",
        "PORT_ALLOCATOR_SCOPE",
        settings.port_allocator_scope,
    )
    add_check(
        "ok" if settings.profile_speed_limit_mbit == 40 else "warn",
        "PROFILE_SPEED_LIMIT_MBIT",
        f"{settings.profile_speed_limit_mbit} mbit",
    )
    add_check("ok" if settings.tc_limit_enabled else "warn", "TC_LIMIT_ENABLED", str(settings.tc_limit_enabled).lower())
    add_check(
        "ok" if settings.firewall_auto_open_ports else "warn",
        "FIREWALL_AUTO_OPEN_PORTS",
        str(settings.firewall_auto_open_ports).lower(),
    )


def direct_admin_templates(settings: Settings) -> list[DirectProtocolTemplate]:
    return [
        DirectProtocolTemplate(
            template_code="R1-AMD-FIREFOX-REALITY",
            protocol="vless-reality",
            x3ui_protocol="vless",
            transport="tcp",
            security="reality",
            reality_public_key=settings.r1_reality_public_key,
            reality_private_key=settings.r1_reality_private_key,
            reality_dest=clean_dest(settings.r1_reality_dest),
            reality_utls=settings.r1_reality_utls,
            reality_spider_x=settings.r1_reality_spider_x,
        ),
        DirectProtocolTemplate(
            template_code="R2-SONY-EDGE-XHTTP",
            protocol="vless-xhttp-reality",
            x3ui_protocol="vless",
            transport=(settings.r2_transport or "xhttp").lower(),
            security="reality",
            reality_public_key=settings.r2_reality_public_key,
            reality_private_key=settings.r2_reality_private_key,
            reality_dest=clean_dest(settings.r2_reality_dest),
            reality_utls=settings.r2_reality_utls,
            reality_spider_x=settings.r2_reality_spider_x,
            xhttp_path=settings.r2_xhttp_path,
            xhttp_mode=settings.r2_xhttp_mode,
            xhttp_padding_bytes=settings.r2_xhttp_padding_bytes,
        ),
        DirectProtocolTemplate(
            template_code="H3-HYSTERIA-SAFARI",
            protocol="hysteria2",
            x3ui_protocol=settings.h3_protocol or "hysteria",
            transport="hysteria",
            security="tls" if settings.h3_tls_enabled else "none",
        ),
        DirectProtocolTemplate(
            template_code="R4-INTEL-EDGE-REALITY",
            protocol="vless-reality",
            x3ui_protocol="vless",
            transport="tcp",
            security="reality",
            reality_public_key=settings.r4_reality_public_key,
            reality_private_key=settings.r4_reality_private_key,
            reality_dest=clean_dest(settings.r4_reality_dest),
            reality_utls=settings.r4_reality_utls,
            reality_spider_x=settings.r4_reality_spider_x,
        ),
        DirectProtocolTemplate(
            template_code="R5-AMD-RANDOM-REALITY",
            protocol="vless-reality",
            x3ui_protocol="vless",
            transport="tcp",
            security="reality",
            reality_public_key=settings.r5_reality_public_key,
            reality_private_key=settings.r5_reality_private_key,
            reality_dest=clean_dest(settings.r5_reality_dest),
            reality_utls=settings.r5_reality_utls,
            reality_spider_x=settings.r5_reality_spider_x,
        ),
    ]


def validate_templates(
    settings: Settings,
    node_config: DirectNodeConfig | None,
    templates: list[DirectProtocolTemplate],
    add_check,
) -> None:
    if settings.admin_test_profiles_count != len(templates):
        add_check(
            "error",
            "ADMIN_TEST_PROFILES_COUNT",
            f"ожидалось {len(templates)}, сейчас {settings.admin_test_profiles_count}",
        )
    else:
        add_check("ok", "profiles", "5 template_code: " + ", ".join(item.template_code for item in templates))

    for template in templates:
        if template.security == "reality":
            missing = []
            if not template.reality_public_key:
                missing.append("publicKey")
            if not template.reality_private_key:
                missing.append("privateKey")
            if not template.reality_dest:
                missing.append("dest")
            if missing and set(missing) <= {"publicKey", "privateKey"}:
                add_check("warn", template.template_code, "Reality keypair будет сгенерирован при создании inbound-а")
                continue
            add_check(
                "error" if missing else "ok",
                template.template_code,
                "missing " + ", ".join(missing) if missing else f"{template.transport}/{template.security}",
            )
            continue
        add_check("ok", template.template_code, f"{template.transport}/{template.security}")


async def plan_direct_node_slots(
    session: AsyncSession,
    settings: Settings,
    node_config: DirectNodeConfig,
    templates: list[DirectProtocolTemplate],
    add_check,
) -> list[DirectNodePlannedSlot]:
    if node_config.vpn_port_min > node_config.vpn_port_max:
        add_check("error", "port range", f"{node_config.node_id}: min port больше max port")
        return []

    reserved_ports = parse_reserved_ports(node_config.reserved_ports)
    existing_slots = (
        await session.scalars(
            select(DirectInboundSlot).where(DirectInboundSlot.node_id == node_config.node_id)
        )
    ).all()
    used_ports = {slot.port for slot in existing_slots}
    used_slot_numbers = {slot.slot_number for slot in existing_slots}

    planned: list[DirectNodePlannedSlot] = []
    next_port = node_config.vpn_port_min
    next_slot_number = 1
    for template in templates:
        existing_free_slot = next(
            (
                slot
                for slot in existing_slots
                if slot.template_code == template.template_code and slot.status == "free" and slot.assigned_user_id is None
            ),
            None,
        )
        if existing_free_slot is not None:
            planned.append(
                DirectNodePlannedSlot(
                    slot_number=existing_free_slot.slot_number,
                    template_code=template.template_code,
                    protocol=template.protocol,
                    port=existing_free_slot.port,
                    name=existing_free_slot.name,
                )
            )
            continue

        while next_slot_number in used_slot_numbers:
            next_slot_number += 1
        while next_port in reserved_ports or next_port in used_ports:
            next_port += 1
        if next_port > node_config.vpn_port_max:
            add_check("error", "port allocator", "не хватает свободных портов для 5 direct-node профилей")
            return planned

        planned.append(
            DirectNodePlannedSlot(
                slot_number=next_slot_number,
                template_code=template.template_code,
                protocol=template.protocol,
                port=next_port,
                name=render_inbound_name(settings, node_config, next_slot_number, template.template_code),
            )
        )
        used_ports.add(next_port)
        used_slot_numbers.add(next_slot_number)
        next_port += 1
        next_slot_number += 1

    add_check(
        "ok",
        "port plan",
        f"{len(planned)} портов в диапазоне {node_config.vpn_port_min}-{node_config.vpn_port_max}",
    )
    return planned


async def audit_node_3xui(node_config: DirectNodeConfig, add_check) -> None:
    if not node_config.api_base_url.strip():
        add_check("error", "Germany 3x-ui", f"{node_config.node_id}: 3x-ui URL не задан")
        return
    if not node_config.api_username.strip() or not node_config.api_password.strip():
        add_check("error", "Germany 3x-ui auth", f"{node_config.node_id}: username/password не заданы")
        return

    try:
        inbounds = await x3ui_client_for_config(node_config).list_inbounds()
    except Exception as exc:
        add_check("error", "Germany 3x-ui API", str(exc)[:180])
        return

    add_check("ok", "Germany 3x-ui API", f"reachable, inbound count={len(inbounds)}")


async def audit_node_agent(settings: Settings, node_config: DirectNodeConfig, add_check) -> None:
    if not settings.enable_node_agent:
        add_check("warn", "node-agent", "ENABLE_NODE_AGENT=false")
        return
    if not node_config.agent_url.strip():
        add_check("error", "node-agent", f"{node_config.node_id}: agent URL не задан")
        return

    try:
        await DirectNodeAgent(config=node_config).health()
    except Exception as exc:
        add_check("error", "node-agent", str(exc)[:180])
        return

    add_check("ok", "node-agent", "reachable")


async def agent_health_for_config(config: DirectNodeConfig) -> None:
    headers = {}
    if config.agent_token.strip():
        headers["Authorization"] = f"Bearer {config.agent_token.strip()}"
    async with httpx.AsyncClient(
        base_url=config.agent_url.strip(),
        timeout=max(1, config.agent_timeout_seconds),
        verify=config.agent_verify_tls,
        headers=headers,
    ) as client:
        response = await client.get("/health")
        if response.status_code == 404:
            response = await client.get("/status")
        response.raise_for_status()


async def upsert_direct_node(session: AsyncSession, config: DirectNodeConfig, *, status: str) -> DirectNode:
    now = utcnow()
    node = await session.get(DirectNode, config.node_id)
    if node is None:
        node = DirectNode(
            id=config.node_id,
            name=config.name,
            country_code=config.country_code,
            country_flag=config.country_flag,
            public_host=config.public_host,
            public_ip=config.public_ip,
            api_base_url=config.api_base_url,
            api_username=config.api_username,
            api_password=config.api_password,
            api_verify_tls=config.api_verify_tls,
            api_timeout_seconds=config.api_timeout_seconds,
            agent_url=config.agent_url,
            agent_token=config.agent_token,
            agent_verify_tls=config.agent_verify_tls,
            agent_timeout_seconds=config.agent_timeout_seconds,
            vpn_port_min=config.vpn_port_min,
            vpn_port_max=config.vpn_port_max,
            reserved_ports=config.reserved_ports,
            status=status,
            last_health_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(node)
    else:
        node.name = config.name
        node.country_code = config.country_code
        node.country_flag = config.country_flag
        node.public_host = config.public_host
        node.public_ip = config.public_ip
        node.api_base_url = config.api_base_url
        node.api_username = config.api_username
        node.api_password = config.api_password
        node.api_verify_tls = config.api_verify_tls
        node.api_timeout_seconds = config.api_timeout_seconds
        node.agent_url = config.agent_url
        node.agent_token = config.agent_token
        node.agent_verify_tls = config.agent_verify_tls
        node.agent_timeout_seconds = config.agent_timeout_seconds
        node.vpn_port_min = config.vpn_port_min
        node.vpn_port_max = config.vpn_port_max
        node.reserved_ports = config.reserved_ports
        node.status = status
        node.last_health_at = now
        node.updated_at = now
    await session.flush()
    return node


async def reserve_direct_slot(
    session: AsyncSession,
    settings: Settings,
    node_config: DirectNodeConfig,
    user: User,
    planned_slot: DirectNodePlannedSlot,
) -> DirectInboundSlot:
    now = utcnow()
    slot = await session.scalar(
            select(DirectInboundSlot)
            .where(
                DirectInboundSlot.node_id == node_config.node_id,
                DirectInboundSlot.template_code == planned_slot.template_code,
                DirectInboundSlot.status == "free",
                DirectInboundSlot.assigned_user_id.is_(None),
        )
        .order_by(DirectInboundSlot.slot_number)
        .limit(1)
        .with_for_update()
    )
    if slot is None:
        slot = DirectInboundSlot(
            node_id=node_config.node_id,
            slot_number=planned_slot.slot_number,
            template_code=planned_slot.template_code,
            protocol=planned_slot.protocol,
            inbound_id=None,
            port=planned_slot.port,
            name=planned_slot.name,
            speed_limit_mbit=settings.profile_speed_limit_mbit,
            status="assigned",
            assigned_user_id=user.id,
            last_assigned_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(slot)
    else:
        slot.status = "assigned"
        slot.assigned_user_id = user.id
        slot.last_assigned_at = now
        slot.updated_at = now
    await session.flush()
    return slot


async def create_remote_inbound_for_slot(
    x3ui: X3UIClient,
    settings: Settings,
    node_config: DirectNodeConfig,
    slot: DirectInboundSlot,
    template: DirectProtocolTemplate,
) -> int:
    payload = build_inbound_payload(settings, node_config, slot, template)
    return await create_x3ui_inbound(x3ui, payload, slot.name, slot.port)


def direct_client_identity(template: DirectProtocolTemplate) -> tuple[str, str]:
    if template.x3ui_protocol == "vless":
        client_uuid = str(uuid4())
        return client_uuid, client_uuid
    password = secrets.token_urlsafe(18)
    return password, password


def generate_reality_keypair() -> tuple[str, str]:
    private_key = X3UIClient._encode_xray_key(secrets.token_bytes(32))  # noqa: SLF001 - project-local xray key codec.
    public_key = X3UIClient._x25519_public_key_from_private(private_key)  # noqa: SLF001
    if not public_key:
        raise X3UIError("Failed to generate Reality publicKey")
    return private_key, public_key


def prepare_direct_template(template: DirectProtocolTemplate) -> DirectProtocolTemplate:
    if template.security != "reality":
        return template
    private_key = template.reality_private_key
    public_key = template.reality_public_key
    if not private_key or not public_key:
        private_key, public_key = generate_reality_keypair()
    short_id = template.reality_short_id or secrets.token_hex(4)
    return replace(
        template,
        reality_private_key=private_key,
        reality_public_key=public_key,
        reality_short_id=short_id,
    )


def build_inbound_payload(
    settings: Settings,
    node_config: DirectNodeConfig,
    slot: DirectInboundSlot,
    template: DirectProtocolTemplate,
) -> dict[str, Any]:
    if template.x3ui_protocol == "vless":
        inbound_settings = {"clients": [], "decryption": "none", "fallbacks": []}
    elif template.x3ui_protocol == "hysteria":
        inbound_settings = {"clients": [], "version": 2}
    else:
        inbound_settings = {"clients": []}

    return {
        "up": 0,
        "down": 0,
        "total": 0,
        "remark": slot.name,
        "enable": True,
        "expiryTime": 0,
        "listen": "",
        "port": slot.port,
        "protocol": template.x3ui_protocol,
        "settings": json.dumps(inbound_settings, ensure_ascii=False),
        "streamSettings": json.dumps(build_stream_settings(settings, node_config, template), ensure_ascii=False),
        "sniffing": json.dumps({"enabled": False, "destOverride": [], "metadataOnly": False}, ensure_ascii=False),
    }


def build_stream_settings(
    settings: Settings,
    node_config: DirectNodeConfig,
    template: DirectProtocolTemplate,
) -> dict[str, Any]:
    if template.security == "reality":
        server_name = reality_sni(template.reality_dest)
        stream: dict[str, Any] = {
            "network": template.transport,
            "security": "reality",
            "realitySettings": {
                "show": False,
                "xver": 0,
                "target": template.reality_dest,
                "serverNames": [server_name] if server_name else [],
                "privateKey": template.reality_private_key,
                "shortIds": [template.reality_short_id or secrets.token_hex(4)],
                "minClientVer": "",
                "maxClientVer": "",
                "maxTimediff": 0,
                "mldsa65Seed": "",
                "settings": {
                    "publicKey": template.reality_public_key,
                    "fingerprint": template.reality_utls or "chrome",
                    "serverName": server_name,
                    "spiderX": template.reality_spider_x or "/",
                    "mldsa65Verify": "",
                },
            },
        }
        if template.transport == "xhttp":
            stream["xhttpSettings"] = {
                "path": template.xhttp_path or "/",
                "host": "",
                "mode": template.xhttp_mode or "auto",
                "extra": {"paddingBytes": template.xhttp_padding_bytes or "100-1000"},
            }
        return stream

    if template.x3ui_protocol == "hysteria":
        stream = {
            "network": "hysteria",
            "security": "tls" if settings.h3_tls_enabled else "none",
            "hysteriaSettings": {"version": 2, "udpIdleTimeout": 60},
            "finalmask": {
                "udp": [
                    {
                        "type": "salamander",
                        "settings": {"password": secrets.token_urlsafe(12).lower()},
                    }
                ]
            },
        }
        if settings.h3_tls_enabled:
            stream["tlsSettings"] = {
                "serverName": settings.h3_tls_sni or node_config.public_host or node_config.public_ip,
                "minVersion": settings.h3_tls_min_version,
                "maxVersion": settings.h3_tls_max_version,
                "cipherSuites": "",
                "alpn": ["h3", "h2", "http/1.1"],
                "certificates": [
                    {
                        "certificateFile": settings.h3_tls_cert_file,
                        "keyFile": settings.h3_tls_key_file,
                        "ocspStapling": settings.h3_ocsp_stapling_seconds,
                        "usage": "encipherment",
                        "buildChain": False,
                        "oneTimeLoading": False,
                    }
                ],
                "settings": {
                    "fingerprint": settings.h3_utls,
                    "pinnedPeerCertSha256": [],
                    "echConfigList": "",
                },
                "disableSystemRoot": False,
                "echServerKeys": "",
                "enableSessionResumption": False,
                "rejectUnknownSni": False,
            }
        return stream

    return {
        "network": "tcp",
        "security": "tls" if settings.h3_tls_enabled else "none",
        "tlsSettings": {
            "serverName": settings.h3_tls_sni or node_config.public_host,
            "minVersion": settings.h3_tls_min_version,
            "maxVersion": settings.h3_tls_max_version,
            "certificates": [
                {
                    "certificateFile": settings.h3_tls_cert_file,
                    "keyFile": settings.h3_tls_key_file,
                    "ocspStapling": settings.h3_ocsp_stapling_seconds,
                }
            ],
            "settings": {
                "fingerprint": settings.h3_utls,
                "decrypt": settings.h3_decrypt,
                "encrypt": settings.h3_encrypt,
                "auth": settings.h3_auth,
                "seed": settings.h3_vision_seed,
            },
        },
    }


async def create_x3ui_inbound(x3ui: X3UIClient, payload: dict[str, Any], remark: str, port: int) -> int:
    async with x3ui._client() as client:  # noqa: SLF001 - internal wrapper for project-local API adapter.
        await x3ui._login(client)  # noqa: SLF001
        response = await client.post(x3ui._panel_path("/panel/api/inbounds/add"), json=payload)  # noqa: SLF001
        x3ui._raise_for_x3ui(response)  # noqa: SLF001
        inbound_id = inbound_id_from_payload(x3ui._payload_data(response))  # noqa: SLF001
        if inbound_id:
            return inbound_id

        response = await client.get(x3ui._panel_path("/panel/api/inbounds/list"))  # noqa: SLF001
        x3ui._raise_for_x3ui(response)  # noqa: SLF001
        for inbound in x3ui._inbound_list(x3ui._payload_data(response)):  # noqa: SLF001
            if str(inbound.get("remark") or "") == remark and int(inbound.get("port") or 0) == port:
                return int(inbound.get("id") or 0)
    raise X3UIError("3x-ui inbound was created but id was not found")


async def delete_x3ui_inbound(x3ui: X3UIClient, inbound_id: int) -> None:
    async with x3ui._client() as client:  # noqa: SLF001 - project-local API adapter.
        await x3ui._login(client)  # noqa: SLF001
        response = await client.post(x3ui._panel_path(f"/panel/api/inbounds/del/{inbound_id}"))  # noqa: SLF001
        if response.status_code == 404:
            response = await client.get(x3ui._panel_path(f"/panel/api/inbounds/del/{inbound_id}"))  # noqa: SLF001
        x3ui._raise_for_x3ui(response)  # noqa: SLF001


def inbound_id_from_payload(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("id", "inboundId"):
            try:
                value = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        for key in ("inbound", "obj"):
            value = inbound_id_from_payload(payload.get(key))
            if value:
                return value
    return None


async def save_direct_profile(
    session: AsyncSession,
    user: User,
    *,
    slot: DirectInboundSlot,
    template: DirectProtocolTemplate,
    client_id: str,
    secret_value: str,
    public_link: str,
) -> DirectUserProfile:
    now = utcnow()
    profile = DirectUserProfile(
        user_id=user.id,
        node_id=slot.node_id,
        inbound_slot_id=slot.id,
        template_code=template.template_code,
        protocol=template.protocol,
        inbound_id=slot.inbound_id,
        port=slot.port,
        client_id=client_id,
        uuid_or_password=secret_value,
        public_link=public_link,
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    await session.flush()
    return profile


async def create_or_get_direct_subscription(
    session: AsyncSession,
    settings: Settings,
    node_config: DirectNodeConfig,
    user: User,
    *,
    public_only: bool = False,
) -> DirectSubscription:
    existing = await active_direct_subscription(session, user.id, node_config.node_id)
    if existing is not None:
        existing.status = "active"
        existing.subscription_url = direct_subscription_url(
            settings,
            existing.subscription_token,
            public_only=public_only,
        )
        existing.updated_at = utcnow()
        await session.flush()
        return existing

    now = utcnow()
    token = secrets.token_urlsafe(32)
    subscription = DirectSubscription(
        user_id=user.id,
        node_id=node_config.node_id,
        subscription_token=token,
        subscription_url=direct_subscription_url(settings, token, public_only=public_only),
        status="active",
        created_at=now,
        updated_at=now,
    )
    session.add(subscription)
    await session.flush()
    return subscription


def direct_subscription_url(settings: Settings, token: str, *, public_only: bool = False) -> str:
    base = settings.subscription_base_url.strip()
    if not base and not public_only:
        base = settings.admin_panel_url or f"http://localhost:{settings.app_port}/admin"
    if not base:
        raise RuntimeError("SUBSCRIPTION_BASE_URL is required for user direct-node subscriptions")
    parsed = urlsplit(base)
    if not parsed.scheme or not parsed.netloc:
        if public_only:
            raise RuntimeError("SUBSCRIPTION_BASE_URL must be an absolute public URL")
        base = f"http://localhost:{settings.app_port}"
        parsed = urlsplit(base)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return urljoin(origin.rstrip("/") + "/", f"sub/direct/{quote(token, safe='')}")


async def direct_subscription_payload(session: AsyncSession, token: str) -> str | None:
    subscription = await session.scalar(
        select(DirectSubscription).where(
            DirectSubscription.subscription_token == token,
            DirectSubscription.status == "active",
        )
    )
    if subscription is None:
        return None
    profiles = (
        await session.scalars(
            select(DirectUserProfile)
            .where(
                DirectUserProfile.user_id == subscription.user_id,
                DirectUserProfile.node_id == subscription.node_id,
                DirectUserProfile.status == "active",
            )
            .order_by(DirectUserProfile.id)
        )
    ).all()
    body = "\n".join(profile.public_link for profile in profiles if profile.public_link)
    return base64.b64encode(body.encode("utf-8")).decode("ascii")


class DirectNodeAgent:
    def __init__(self, settings: Settings | None = None, config: DirectNodeConfig | None = None) -> None:
        self.settings = settings or get_settings()
        if config is None:
            config = env_direct_node_config(self.settings)
        self.agent_url = config.agent_url.strip()
        self.agent_token = config.agent_token.strip()
        self.agent_verify_tls = config.agent_verify_tls
        self.agent_timeout_seconds = config.agent_timeout_seconds

    async def health(self) -> None:
        async with self._client() as client:
            response = await client.get("/health")
            if response.status_code == 404:
                response = await client.get("/status")
            response.raise_for_status()

    async def ensure_port_free(self, port: int) -> None:
        async with self._client() as client:
            response = await client.post("/ports/check", json={"port": port})
            response.raise_for_status()
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            if isinstance(payload, dict) and payload.get("free") is False:
                raise RuntimeError(f"node-agent reports port {port} is busy")

    async def open_port(self, port: int) -> None:
        async with self._client() as client:
            response = await client.post("/firewall/open", json={"port": port, "provider": self.settings.firewall_provider})
            response.raise_for_status()

    async def apply_tc_limit(self, port: int, mbit: int) -> None:
        async with self._client() as client:
            response = await client.post("/tc/limit", json={"port": port, "rate": f"{mbit}mbit"})
            response.raise_for_status()

    def _client(self) -> httpx.AsyncClient:
        headers = {}
        if self.agent_token:
            headers["Authorization"] = f"Bearer {self.agent_token}"
        return httpx.AsyncClient(
            base_url=self.agent_url,
            timeout=max(1, self.agent_timeout_seconds),
            verify=self.agent_verify_tls,
            headers=headers,
        )


def direct_node_x3ui_client(settings: Settings) -> X3UIClient:
    base_url, web_base_path = split_panel_base_url(settings.node_de_1_3xui_base_url)
    node_settings = settings.model_copy(
        update={
            "x3ui_mode": "live",
            "x3ui_base_url": base_url,
            "x3ui_web_base_path": web_base_path,
            "x3ui_username": settings.node_de_1_3xui_username,
            "x3ui_password": settings.node_de_1_3xui_password,
            "my_3x_ui_login": "",
            "my_3x_ui_password": "",
            "x3ui_tls_verify": settings.node_de_1_3xui_verify_tls,
            "x3ui_timeout_seconds": settings.node_de_1_3xui_timeout_seconds,
            "vless_public_host": settings.node_de_1_public_host or settings.node_de_1_public_ip,
            "vless_public_port": settings.node_de_1_vpn_port_min,
        }
    )
    return X3UIClient(node_settings)


def x3ui_client_for_config(config: DirectNodeConfig, settings: Settings | None = None) -> X3UIClient:
    settings = settings or get_settings()
    base_url, web_base_path = split_panel_base_url(config.api_base_url)
    node_settings = settings.model_copy(
        update={
            "x3ui_mode": "live",
            "x3ui_base_url": base_url,
            "x3ui_web_base_path": web_base_path,
            "x3ui_username": config.api_username,
            "x3ui_password": config.api_password,
            "my_3x_ui_login": "",
            "my_3x_ui_password": "",
            "x3ui_tls_verify": config.api_verify_tls,
            "x3ui_timeout_seconds": config.api_timeout_seconds,
            "vless_public_host": config.public_host or config.public_ip,
            "vless_public_port": config.vpn_port_min,
        }
    )
    return X3UIClient(node_settings)


def render_admin_direct_node_audit(result: DirectNodeAuditResult) -> str:
    lines = [
        "🧪 <b>Direct-node admin audit</b>",
        "",
        f"Node: <code>{escape(result.node_id)}</code>",
        f"Mode: <b>{'dry-run' if result.dry_run else 'create-ready'}</b>",
        "",
        "<b>Checks</b>",
    ]
    for check in result.checks:
        lines.append(f"{status_icon(check.status)} {escape(check.title)}: {escape(check.detail)}")

    lines.extend(["", "<b>Planned slots</b>"])
    if not result.planned_slots:
        lines.append("Нет готового плана слотов.")
    for slot in result.planned_slots:
        lines.append(
            f"• <code>{slot.slot_number:06d}</code> {escape(slot.template_code)} "
            f"port <code>{slot.port}</code> - {escape(slot.name)}"
        )

    lines.extend(["", "Ничего не создано и не изменено. Это только audit/dry-run для админского теста."])
    if result.enabled_for_create and result.dry_run:
        lines.append("Создание закрыто флагом DRY_RUN_FIRST=true.")
    elif not result.enabled_for_create:
        lines.append("Создание закрыто флагами ADMIN_TEST/ENABLE_DIRECT_NODE/ADMIN_TEST_CREATE_SUB.")
    return "\n".join(lines)


def render_admin_direct_node_provision(result: DirectNodeProvisionResult) -> str:
    status = "переиспользован" if result.reused else "создан" if result.created else "проверен"
    lines = [
        "🧪 <b>Direct-node admin create</b>",
        "",
        f"Статус: <b>{escape(status)}</b>",
        f"Профилей в sub: <b>{result.profiles_count}</b>",
        "Sub link:",
        f"<code>{escape(result.subscription_url)}</code>",
        "",
        "<b>Checks</b>",
    ]
    for check in result.checks:
        lines.append(f"{status_icon(check.status)} {escape(check.title)}: {escape(check.detail)}")
    return "\n".join(lines)


def render_inbound_name(
    settings: Settings,
    node_config: DirectNodeConfig,
    slot_number: int,
    template_code: str,
) -> str:
    return settings.inbound_name_template.format(
        flag=node_config.country_flag or settings.admin_test_country_flag,
        node_code=node_config.country_code or settings.admin_test_country_code,
        slot_number=f"{slot_number:06d}",
        protocol=template_code,
    )


def build_public_link(
    settings: Settings,
    node_config: DirectNodeConfig,
    slot: DirectInboundSlot,
    template: DirectProtocolTemplate,
    client_id: str,
    secret_value: str,
) -> str:
    label = quote(slot.name, safe="")
    host = node_config.public_host or node_config.public_ip
    if template.x3ui_protocol == "vless":
        query_items = [
            ("type", template.transport),
            ("security", "reality"),
            ("encryption", "none"),
            ("pbk", template.reality_public_key),
            ("fp", template.reality_utls or "chrome"),
            ("spx", template.reality_spider_x or "/"),
            ("flow", DEFAULT_REALITY_FLOW),
        ]
        if template.reality_short_id:
            query_items.append(("sid", template.reality_short_id))
        sni = reality_sni(template.reality_dest)
        if sni:
            query_items.append(("sni", sni))
        return f"vless://{client_id}@{host}:{slot.port}?{urlencode(query_items, quote_via=quote)}#{label}"

    sni = settings.h3_tls_sni or node_config.public_host or host
    return f"hysteria2://{quote(secret_value, safe='')}@{host}:{slot.port}?{urlencode({'sni': sni})}#{label}"


def direct_link_for_slot(
    slot: DirectInboundSlot,
    links: tuple[str, ...],
    node_config: DirectNodeConfig,
) -> str | None:
    for link in links:
        try:
            parsed = urlsplit(link)
            link_port = parsed.port
        except ValueError:
            continue
        if link_port != slot.port:
            continue
        return rewrite_link_endpoint(link, node_config.public_host or node_config.public_ip, slot.port)
    return None


def rewrite_link_endpoint(link: str, host: str, port: int) -> str:
    parsed = urlsplit(link)
    userinfo = ""
    if "@" in parsed.netloc:
        userinfo = parsed.netloc.rsplit("@", 1)[0] + "@"
    return urlunsplit((parsed.scheme, f"{userinfo}{host}:{port}", parsed.path, parsed.query, parsed.fragment))


def parse_reserved_ports(raw: str) -> set[int]:
    ports: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_raw, end_raw = item.split("-", 1)
            if start_raw.strip().isdigit() and end_raw.strip().isdigit():
                start = int(start_raw)
                end = int(end_raw)
                ports.update(range(min(start, end), max(start, end) + 1))
            continue
        if item.isdigit():
            ports.add(int(item))
    return ports


def split_panel_base_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value.strip())
    base_url = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return base_url, parsed.path.strip("/")


def clean_dest(value: str) -> str:
    value = value.strip()
    if value.startswith("[") and "](" in value and value.endswith(")"):
        value = value.rsplit("(", 1)[1].rstrip(")")
    if "://" in value:
        parsed = urlsplit(value)
        return parsed.netloc or parsed.path
    return value


def reality_sni(dest: str) -> str:
    host = clean_dest(dest).split(":", 1)[0].strip()
    return host


def reality_server_names(dest: str) -> list[str]:
    sni = reality_sni(dest)
    return [sni] if sni else []


def safe_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def status_icon(status: str) -> str:
    if status == "ok":
        return "✅"
    if status == "warn":
        return "⚠️"
    return "❌"
