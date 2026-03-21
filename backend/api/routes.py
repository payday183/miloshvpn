from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from backend.config import settings
from backend.db import engine
from backend.models.orm import Base
from backend.repositories.payment_repo import PaymentRepository
from backend.repositories.user_repo import UserRepository
from backend.services.bot_service import BotService
from backend.services.payment_service import PaymentNotFoundError, PaymentService
from backend.services.subscription_service import (
    NoActiveSubscriptionError,
    SubscriptionService,
)
from backend.services.user_service import PermissionDeniedError, UserNotFoundError, UserService
from backend.services.vpn_service import VPNAccessNotFoundError, VPNService

# ✅ DB INIT
try:
    from fastapi import APIRouter, FastAPI, HTTPException
except ImportError:
    APIRouter = None
    FastAPI = None

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
    return {
        "id": sub.id,
        "user_id": sub.user_id,
        "plan": sub.plan,
        "status": sub.status,
        "ends_at": str(sub.ends_at),
    }


def vpn_to_dict(access):
    return {
        "id": access.id,
        "user_id": access.user_id,
        "server": access.server_name,
        "protocol": access.protocol,
        "access_url": access.access_url,
    }


# =========================

@dataclass(slots=True)
class ServiceContainer:
    user_service: UserService
    bot_service: BotService
    payment_service: PaymentService
    subscription_service: SubscriptionService
    vpn_service: VPNService


def build_services() -> ServiceContainer:
    user_repo = UserRepository()
    payment_repo = PaymentRepository()

    subscription_service = SubscriptionService(
        default_duration_days=settings.default_subscription_days
    )

    user_service = UserService(user_repo=user_repo)
    payment_service = PaymentService(
        payment_repo=payment_repo,
        subscription_service=subscription_service,
    )

    vpn_service = VPNService(
        subscription_service=subscription_service,
        settings=settings,
    )

    bot_service = BotService(
        user_service=user_service,
        payment_service=payment_service,
        subscription_service=subscription_service,
        vpn_service=vpn_service,
    )
    payment_service.bot_service = bot_service

    return ServiceContainer(
        user_service=user_service,
        bot_service=bot_service,
        payment_service=payment_service,
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


# =========================

def _to_http_exception(error: Exception) -> HTTPException:
    if isinstance(error, UserNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, PermissionDeniedError):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, PaymentNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, VPNAccessNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, NoActiveSubscriptionError):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=500, detail=str(error))


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

    return router


def create_app():
    app = FastAPI(title="MiloshVPN Backend")
    Base.metadata.create_all(bind=engine)
    app.include_router(build_router())
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
