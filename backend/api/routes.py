from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.db import engine
from backend.models.orm import Base
from backend.repositories.admin_setting_repo import AdminSettingRepository
from backend.repositories.content_repo import ContentRepository
from backend.repositories.marzban_repo import MarzbanServerRepository
from backend.repositories.payment_repo import PaymentRepository
from backend.repositories.payment_webhook_repo import PaymentWebhookEventRepository
from backend.repositories.plan_repo import PlanRepository
from backend.repositories.user_repo import UserRepository
from backend.repositories.webapp_session_repo import WebAppSessionRepository
from backend.services.admin_setting_service import (
    AdminSettingNotFoundError,
    AdminSettingService,
)
from backend.services.bot_service import BotService
from backend.services.content_service import ContentBlockNotFoundError, ContentService
from backend.services.marzban_service import (
    MarzbanService,
    MarzbanServerNotFoundError,
    MarzbanServerValidationError,
)
from backend.services.payment_service import (
    PaymentAccessDeniedError,
    PaymentNotFoundError,
    PaymentService,
)
from backend.services.plan_service import PlanPeriodNotFoundError, PlanService
from backend.services.subscription_service import (
    NoActiveSubscriptionError,
    SubscriptionService,
)
from backend.services.user_service import PermissionDeniedError, UserNotFoundError, UserService
from backend.services.vpn_service import VPNAccessNotFoundError, VPNService
from backend.services.webapp_auth_service import (
    InvalidTelegramAuthError,
    WebAppAuthService,
    WebAppSessionNotFoundError,
)
from backend.services.yookassa_service import (
    YooKassaConfigurationError,
    YooKassaRequestError,
    YooKassaService,
)

# ✅ DB INIT
try:
    from fastapi import APIRouter, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:
    APIRouter = None
    FastAPI = None
    Request = None
    FileResponse = None
    StaticFiles = None

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str) -> None:
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)


# =========================
# 🔧 SERIALIZERS (ВАЖНО)
# =========================

def user_to_dict(user):
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "role": user.role,
        "phone": user.phone,
    }


def payment_to_dict(payment):
    return {
        "id": payment.id,
        "user_id": payment.user_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "plan": payment.plan,
        "duration_days": payment.duration_days,
        "payment_code": payment.payment_code,
        "recipient_phone": payment.recipient_phone,
    }


def subscription_to_dict(sub):
    days_left = services.subscription_service.get_days_left(sub)
    return {
        "id": sub.id,
        "user_id": sub.user_id,
        "plan": sub.plan,
        "status": sub.status,
        "started_at": str(sub.started_at) if sub.started_at is not None else None,
        "ends_at": str(sub.ends_at),
        "days_left": days_left,
        "source_payment_id": sub.source_payment_id,
        "is_active": bool(sub.status == "active" and (days_left or 0) > 0),
    }


def vpn_to_dict(access):
    return {
        "id": access.id,
        "user_id": access.user_id,
        "server_id": access.server_id,
        "server": access.server_name,
        "protocol": access.protocol,
        "access_url": access.access_url,
        "subscription_url": access.subscription_url,
        "credential_id": access.credential_id,
        "external_user_id": access.external_user_id,
        "expires_at": str(access.expires_at) if access.expires_at is not None else None,
        "last_synced_at": str(access.last_synced_at) if access.last_synced_at is not None else None,
        "sync_status": access.sync_status,
        "usage_bytes": access.usage_bytes,
        "last_used_at": str(access.last_used_at) if access.last_used_at is not None else None,
        "provisioning_error": access.provisioning_error,
        "connection_key": access.subscription_url or access.access_url,
    }


def plan_period_to_dict(period):
    return {
        "id": period.id,
        "plan_id": period.plan_id,
        "period_key": period.period_key,
        "label": period.label,
        "period_months": period.period_months,
        "duration_days": period.duration_days,
        "price_amount": period.price_amount,
        "currency": period.currency,
        "is_active": period.is_active,
        "sort_order": period.sort_order,
    }


def content_block_to_dict(block):
    return {
        "id": block.id,
        "key": block.key,
        "title": block.title,
        "body": block.body,
        "format": block.format,
        "is_active": block.is_active,
    }


def admin_setting_to_dict(setting):
    return {
        "id": setting.id,
        "key": setting.key,
        "value": setting.value,
        "is_secret": setting.is_secret,
        "updated_by_telegram_id": setting.updated_by_telegram_id,
    }


# =========================

@dataclass(slots=True)
class ServiceContainer:
    user_service: UserService
    bot_service: BotService
    payment_service: PaymentService
    plan_service: PlanService
    content_service: ContentService
    admin_setting_service: AdminSettingService
    marzban_service: MarzbanService
    webapp_auth_service: WebAppAuthService
    yookassa_service: YooKassaService
    subscription_service: SubscriptionService
    vpn_service: VPNService


def build_services() -> ServiceContainer:
    user_repo = UserRepository()
    payment_repo = PaymentRepository()
    plan_repo = PlanRepository()
    content_repo = ContentRepository()
    marzban_repo = MarzbanServerRepository()
    admin_setting_repo = AdminSettingRepository()
    webapp_session_repo = WebAppSessionRepository()
    payment_webhook_repo = PaymentWebhookEventRepository()

    subscription_service = SubscriptionService(
        default_duration_days=settings.default_subscription_days
    )

    user_service = UserService(user_repo=user_repo)
    plan_service = PlanService(plan_repo=plan_repo)
    content_service = ContentService(content_repo=content_repo)
    admin_setting_service = AdminSettingService(setting_repo=admin_setting_repo)
    marzban_service = MarzbanService(repo=marzban_repo)
    yookassa_service = YooKassaService(admin_setting_service=admin_setting_service)
    webapp_auth_service = WebAppAuthService(
        user_service=user_service,
        session_repo=webapp_session_repo,
    )
    payment_service = PaymentService(
        payment_repo=payment_repo,
        subscription_service=subscription_service,
        plan_service=plan_service,
        yookassa_service=yookassa_service,
        webhook_repo=payment_webhook_repo,
    )

    vpn_service = VPNService(
        subscription_service=subscription_service,
        settings=settings,
        marzban_service=marzban_service,
    )

    bot_service = BotService(
        user_service=user_service,
        payment_service=payment_service,
        subscription_service=subscription_service,
        vpn_service=vpn_service,
        webapp_auth_service=webapp_auth_service,
    )
    payment_service.bot_service = bot_service

    return ServiceContainer(
        user_service=user_service,
        bot_service=bot_service,
        payment_service=payment_service,
        plan_service=plan_service,
        content_service=content_service,
        admin_setting_service=admin_setting_service,
        marzban_service=marzban_service,
        webapp_auth_service=webapp_auth_service,
        yookassa_service=yookassa_service,
        subscription_service=subscription_service,
        vpn_service=vpn_service,
    )


services = build_services()


# =========================
# API LOGIC
# =========================

def register_user(payload: dict[str, Any], container: ServiceContainer = services):
    user = container.user_service.register_user(
        telegram_id=int(payload["telegram_id"]),
        username=payload.get("username"),
    )
    return user_to_dict(user)


def open_bot_session(payload: dict[str, Any], container: ServiceContainer = services):
    return container.bot_service.open_session(
        telegram_id=int(payload["telegram_id"]),
        username=payload.get("username"),
    )


def handle_bot_message(payload: dict[str, Any], container: ServiceContainer = services):
    return container.bot_service.handle_message(
        telegram_id=int(payload["telegram_id"]),
        username=payload.get("username"),
        text=payload.get("text"),
    )


def create_payment(payload: dict[str, Any], container: ServiceContainer = services):
    container.user_service.require_user(int(payload["user_id"]))

    payment = container.payment_service.create_payment(
        user_id=int(payload["user_id"]),
        amount=float(payload["amount"]),
        currency=str(payload.get("currency", "RUB")),
        plan=str(payload.get("plan", "month")),
        duration_days=int(payload["duration_days"]) if "duration_days" in payload else None,
        provider=str(payload.get("provider", "manual")),
        external_id=payload.get("external_id"),
    )

    return payment_to_dict(payment)


def confirm_payment(payment_id: int, container: ServiceContainer = services):
    payment, subscription = container.payment_service.confirm_payment(payment_id)

    return {
        "payment": payment_to_dict(payment),
        "subscription": subscription_to_dict(subscription),
    }


def issue_vpn_access(user_id: int, payload=None, container: ServiceContainer = services):
    container.user_service.require_user(user_id)

    payload = payload or {}

    access = container.vpn_service.provision_access(
        user_id=user_id,
        server_name=payload.get("server_name"),
        protocol=payload.get("protocol"),
    )

    return vpn_to_dict(access)


def revoke_vpn_access(user_id: int, container: ServiceContainer = services):
    container.user_service.require_user(user_id)

    access = container.vpn_service.revoke_access(user_id)

    return vpn_to_dict(access)


def healthcheck(container: ServiceContainer = services):
    return {
        "status": "ok",
        "users": len(container.user_service.list_users()),
    }


def authenticate_webapp(payload: dict[str, Any], container: ServiceContainer = services):
    user, session = container.webapp_auth_service.authenticate_telegram_webapp(
        init_data=str(payload.get("init_data", "")),
        scope=str(payload.get("scope", "user")),
    )
    return {
        "session": {
            "token": session.session_token,
            "scope": session.scope,
            "expires_at": str(session.expires_at),
        },
        "user": user_to_dict(user),
    }


def resolve_webapp_session(
    session_token: str,
    container: ServiceContainer = services,
    required_scope: str | None = None,
):
    user, session = container.webapp_auth_service.require_session(
        session_token=session_token,
        required_scope=required_scope,
    )
    return user, session


def get_webapp_profile(
    session_token: str,
    container: ServiceContainer = services,
):
    user, session = resolve_webapp_session(session_token, container)
    subscription = container.subscription_service.get_active_subscription(user.id)
    access = container.vpn_service.get_access(user.id)
    return {
        "session": {
            "token": session.session_token,
            "scope": session.scope,
            "expires_at": str(session.expires_at),
        },
        "user": user_to_dict(user),
        "subscription": subscription_to_dict(subscription) if subscription else None,
        "vpn_access": vpn_to_dict(access) if access else None,
    }


def get_webapp_subscription(
    session_token: str,
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container)
    subscription = container.subscription_service.get_active_subscription(user.id)
    if subscription is None:
        subscription = container.subscription_service.get_latest_subscription(user.id)
    return {
        "subscription": subscription_to_dict(subscription) if subscription else None,
    }


def get_webapp_access(
    session_token: str,
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container)
    access = container.vpn_service.get_access(user.id)
    return {
        "vpn_access": vpn_to_dict(access) if access else None,
    }


def list_webapp_plans(
    session_token: str,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container)
    return {
        "plans": container.plan_service.list_public_catalog(),
    }


def list_webapp_content(
    session_token: str,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container)
    return {
        "content": container.content_service.list_public_blocks(),
    }


def create_webapp_payment(
    session_token: str,
    payload: dict[str, Any],
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container)
    payment = container.payment_service.create_webapp_payment(
        user_id=user.id,
        plan_period_id=int(payload["plan_period_id"]),
        return_url=str(payload.get("return_url") or container.yookassa_service.get_public_state()["return_url"]),
        payment_method_type=str(payload.get("payment_method_type") or "sbp"),
    )
    return {
        "payment": container.payment_service.payment_to_web_dict(payment),
    }


def get_webapp_payment(
    session_token: str,
    payment_id: int,
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container)
    payment = container.payment_service.get_payment_for_user(payment_id, user.id)
    return {
        "payment": container.payment_service.payment_to_web_dict(payment),
    }


def refresh_webapp_payment(
    session_token: str,
    payment_id: int,
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container)
    payment = container.payment_service.refresh_webapp_payment(
        payment_id=payment_id,
        user_id=user.id,
    )
    return {
        "payment": container.payment_service.payment_to_web_dict(payment),
    }


def get_latest_webapp_payment(
    session_token: str,
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container)
    payment = container.payment_service.get_latest_payment(user.id)
    return {
        "payment": (
            container.payment_service.payment_to_web_dict(payment)
            if payment is not None
            else None
        )
    }


def get_admin_dashboard(
    session_token: str,
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(
        session_token,
        container,
        required_scope="admin",
    )
    analytics = container.payment_service.get_admin_analytics()
    analytics["plans"] = len(container.plan_service.list_plans(active_only=False))
    analytics["content_blocks"] = len(container.content_service.list_blocks(active_only=False))
    analytics["servers"] = len(container.marzban_service.list_servers(enabled_only=False))
    analytics["actor"] = user_to_dict(user)
    return analytics


def list_admin_plans(
    session_token: str,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    plans = container.plan_service.list_plans(active_only=False)
    periods = container.plan_service.list_periods(active_only=False)
    periods_by_plan: dict[int, list[dict[str, object]]] = {}
    for period in periods:
        periods_by_plan.setdefault(period.plan_id, []).append(plan_period_to_dict(period))
    return {
        "plans": [
            {
                "id": plan.id,
                "code": plan.code,
                "name": plan.name,
                "description": plan.description,
                "is_active": plan.is_active,
                "sort_order": plan.sort_order,
                "periods": periods_by_plan.get(plan.id, []),
            }
            for plan in plans
        ]
    }


def update_admin_plan_period(
    session_token: str,
    period_id: int,
    payload: dict[str, Any],
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    period = container.plan_service.update_period(
        period_id,
        label=payload.get("label"),
        price_amount=float(payload["price_amount"]) if "price_amount" in payload else None,
        duration_days=int(payload["duration_days"]) if "duration_days" in payload else None,
        period_months=int(payload["period_months"]) if "period_months" in payload else None,
        is_active=bool(payload["is_active"]) if "is_active" in payload else None,
        sort_order=int(payload["sort_order"]) if "sort_order" in payload else None,
    )
    return {"period": plan_period_to_dict(period)}


def list_admin_content(
    session_token: str,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    return {
        "content": [
            content_block_to_dict(block)
            for block in container.content_service.list_blocks(active_only=False)
        ]
    }


def update_admin_content(
    session_token: str,
    key: str,
    payload: dict[str, Any],
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container, required_scope="admin")
    block = container.content_service.update_block(
        key,
        title=payload.get("title"),
        body=payload.get("body"),
        format=payload.get("format"),
        is_active=bool(payload["is_active"]) if "is_active" in payload else None,
        updated_by_telegram_id=user.telegram_id,
    )
    return {"content_block": content_block_to_dict(block)}


def get_admin_yookassa(
    session_token: str,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    return {
        "settings": container.admin_setting_service.get_yookassa_settings(),
        "public_state": container.yookassa_service.get_public_state(),
    }


def update_admin_yookassa(
    session_token: str,
    payload: dict[str, Any],
    container: ServiceContainer = services,
):
    user, _ = resolve_webapp_session(session_token, container, required_scope="admin")
    return {
        "settings": container.admin_setting_service.update_yookassa_settings(
            payload,
            updated_by_telegram_id=user.telegram_id,
        ),
        "public_state": container.yookassa_service.get_public_state(),
    }


def list_admin_servers(
    session_token: str,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    return {
        "servers": container.marzban_service.list_servers_with_metrics(enabled_only=False),
    }


def create_admin_server(
    session_token: str,
    payload: dict[str, Any],
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    server = container.marzban_service.create_server(payload)
    return {
        "server": container.marzban_service.server_to_dict(server),
    }


def update_admin_server(
    session_token: str,
    server_id: int,
    payload: dict[str, Any],
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    server = container.marzban_service.update_server(server_id, payload)
    return {
        "server": container.marzban_service.server_to_dict(server),
    }


def check_admin_server(
    session_token: str,
    server_id: int,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    return container.marzban_service.check_server_connection(server_id)


def get_admin_server_metrics(
    session_token: str,
    server_id: int,
    container: ServiceContainer = services,
):
    resolve_webapp_session(session_token, container, required_scope="admin")
    return container.marzban_service.get_server_metrics(server_id)


def handle_yookassa_webhook(
    payload: dict[str, Any],
    container: ServiceContainer = services,
):
    return container.payment_service.handle_yookassa_webhook(payload)


# =========================

def _to_http_exception(error: Exception) -> HTTPException:
    if isinstance(error, UserNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, PermissionDeniedError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, InvalidTelegramAuthError):
        return HTTPException(status_code=401, detail=str(error))
    if isinstance(error, WebAppSessionNotFoundError):
        return HTTPException(status_code=401, detail=str(error))
    if isinstance(error, PaymentNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, PaymentAccessDeniedError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, PlanPeriodNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ContentBlockNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, AdminSettingNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, MarzbanServerNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, MarzbanServerValidationError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, VPNAccessNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, NoActiveSubscriptionError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, YooKassaConfigurationError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, YooKassaRequestError):
        return HTTPException(status_code=502, detail=str(error))
    return HTTPException(status_code=500, detail=str(error))


def _extract_session_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer ") :].strip()
    return request.headers.get("X-WebApp-Session", "").strip()


def build_router(container: ServiceContainer = services):
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def route_healthcheck():
        return healthcheck(container)

    @router.post("/users")
    def route_register_user(payload: dict):
        try:
            return register_user(payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/bot/session")
    def route_open_bot_session(payload: dict):
        try:
            return open_bot_session(payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/bot/messages")
    def route_handle_bot_message(payload: dict):
        try:
            return handle_bot_message(payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/webapp/auth/telegram")
    def route_webapp_auth(payload: dict):
        try:
            return authenticate_webapp(payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/webapp/me")
    def route_webapp_me(request: Request):
        try:
            return get_webapp_profile(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/webapp/subscription")
    def route_webapp_subscription(request: Request):
        try:
            return get_webapp_subscription(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/webapp/access")
    def route_webapp_access(request: Request):
        try:
            return get_webapp_access(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/webapp/plans")
    def route_webapp_plans(request: Request):
        try:
            return list_webapp_plans(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/webapp/content")
    def route_webapp_content(request: Request):
        try:
            return list_webapp_content(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/webapp/payments")
    def route_webapp_create_payment(payload: dict, request: Request):
        try:
            return create_webapp_payment(
                _extract_session_token(request),
                payload,
                container,
            )
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/webapp/payments/latest")
    def route_webapp_latest_payment(request: Request):
        try:
            return get_latest_webapp_payment(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/webapp/payments/{payment_id}")
    def route_webapp_get_payment(payment_id: int, request: Request):
        try:
            return get_webapp_payment(
                _extract_session_token(request),
                payment_id,
                container,
            )
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/webapp/payments/{payment_id}/refresh")
    def route_webapp_refresh_payment(payment_id: int, request: Request):
        try:
            return refresh_webapp_payment(
                _extract_session_token(request),
                payment_id,
                container,
            )
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/payments")
    def route_create_payment(payload: dict):
        try:
            return create_payment(payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/payments/{payment_id}/confirm")
    def route_confirm_payment(payment_id: int):
        try:
            return confirm_payment(payment_id, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/users/{user_id}/vpn")
    def route_issue_vpn_access(user_id: int, payload: dict | None = None):
        try:
            return issue_vpn_access(user_id, payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.delete("/users/{user_id}/vpn")
    def route_revoke_vpn_access(user_id: int):
        try:
            return revoke_vpn_access(user_id, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/admin/dashboard")
    def route_admin_dashboard(request: Request):
        try:
            return get_admin_dashboard(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/admin/plans")
    def route_admin_plans(request: Request):
        try:
            return list_admin_plans(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.patch("/admin/plan-periods/{period_id}")
    def route_admin_update_plan_period(period_id: int, payload: dict, request: Request):
        try:
            return update_admin_plan_period(
                _extract_session_token(request),
                period_id,
                payload,
                container,
            )
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/admin/content")
    def route_admin_content(request: Request):
        try:
            return list_admin_content(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.patch("/admin/content/{key}")
    def route_admin_update_content(key: str, payload: dict, request: Request):
        try:
            return update_admin_content(
                _extract_session_token(request),
                key,
                payload,
                container,
            )
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/admin/yookassa")
    def route_admin_yookassa(request: Request):
        try:
            return get_admin_yookassa(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.patch("/admin/yookassa")
    def route_admin_update_yookassa(payload: dict, request: Request):
        try:
            return update_admin_yookassa(
                _extract_session_token(request),
                payload,
                container,
            )
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/admin/servers")
    def route_admin_servers(request: Request):
        try:
            return list_admin_servers(_extract_session_token(request), container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/admin/servers")
    def route_admin_create_server(payload: dict, request: Request):
        try:
            return create_admin_server(_extract_session_token(request), payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.patch("/admin/servers/{server_id}")
    def route_admin_update_server(server_id: int, payload: dict, request: Request):
        try:
            return update_admin_server(
                _extract_session_token(request),
                server_id,
                payload,
                container,
            )
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/admin/servers/{server_id}/check")
    def route_admin_check_server(server_id: int, request: Request):
        try:
            return check_admin_server(_extract_session_token(request), server_id, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.get("/admin/servers/{server_id}/metrics")
    def route_admin_server_metrics(server_id: int, request: Request):
        try:
            return get_admin_server_metrics(_extract_session_token(request), server_id, container)
        except Exception as error:
            raise _to_http_exception(error)

    @router.post("/webhooks/yookassa")
    def route_yookassa_webhook(payload: dict):
        try:
            return handle_yookassa_webhook(payload, container)
        except Exception as error:
            raise _to_http_exception(error)

    return router


def create_app():
    root_dir = Path(__file__).resolve().parents[2]
    webapp_dir = root_dir / "webapp"
    app = FastAPI(title="MiloshVPN Backend")
    app.include_router(build_router())
    if StaticFiles is not None and webapp_dir.exists():
        shared_dir = webapp_dir / "shared"
        if shared_dir.exists():
            app.mount(
                "/webapp-assets",
                StaticFiles(directory=str(shared_dir)),
                name="webapp-assets",
            )

        @app.get("/webapp/user", include_in_schema=False)
        def route_webapp_user_page():
            return FileResponse(webapp_dir / "user" / "index.html")

        @app.get("/webapp/admin", include_in_schema=False)
        def route_webapp_admin_page():
            return FileResponse(webapp_dir / "admin" / "index.html")

    return app


def main():
    if "--serve" in sys.argv:
        import uvicorn

        uvicorn.run(
            create_app(),
            host=settings.api_host,
            port=settings.api_port,
            loop="uvloop",
        )
        return

    print(json.dumps({"status": "ok"}, indent=2))


if __name__ == "__main__":
    main()
