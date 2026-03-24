import random
import time
import json
from datetime import datetime, timezone

from backend.models.orm import PaymentORM
from backend.repositories.payment_repo import PaymentRepository
from backend.repositories.payment_webhook_repo import PaymentWebhookEventRepository
from backend.repositories.user_repo import UserRepository
from backend.services.plan_service import PlanService
from backend.services.subscription_service import SubscriptionService
from backend.services.yookassa_service import (
    YooKassaConfigurationError,
    YooKassaRequestError,
    YooKassaService,
)
from shared.queue import create_job
from shared.redis import redis_client
from shared.utils import generate_payment_code, generate_secret, utc_now


class PaymentNotFoundError(Exception):
    pass


class PaymentAccessDeniedError(Exception):
    pass


class PaymentService:
    META_KEY_PREFIX = "payment_meta"
    PAYMENT_ASSIGN_KEY_PREFIX = "payment_assign"

    def __init__(
        self,
        payment_repo: PaymentRepository,
        subscription_service: SubscriptionService,
        plan_service: PlanService | None = None,
        yookassa_service: YooKassaService | None = None,
        webhook_repo: PaymentWebhookEventRepository | None = None,
    ) -> None:
        self.payment_repo = payment_repo
        self.subscription_service = subscription_service
        self.plan_service = plan_service
        self.yookassa_service = yookassa_service
        self.webhook_repo = webhook_repo
        self.bot_service = None

    def create_payment(
        self,
        user_id: int,
        amount: float,
        currency: str = "RUB",
        plan: str = "month",
        duration_days: int | None = None,
        provider: str = "manual",
        external_id: str | None = None,
    ):
        code = self._generate_unique_code()
        moderator_tg_id = self._assign_moderator(
            bot_service=self.bot_service,
            user_id=user_id,
        )
        moderator = None
        if moderator_tg_id is not None:
            moderator = UserRepository().get_by_telegram_id(moderator_tg_id)

        phone = moderator.phone if moderator and moderator.phone else "нет доступных модеров"

        duration = duration_days or self.subscription_service.default_duration_days
        payment = self.payment_repo.create(
            user_id=user_id,
            amount=amount,
            currency=currency,
            provider=provider,
            external_id=external_id,
            metadata={
                "plan": plan,
                "duration_days": duration,
            },
        )
        payment.payment_code = code
        payment.recipient_phone = phone
        payment = self.payment_repo.save(payment)
        if moderator_tg_id is not None:
            self._store_payment_assignment(payment.id, moderator_tg_id)
        self._store_payment_meta(
            payment_id=payment.id,
            plan=plan,
            duration_days=duration,
        )
        return payment

    def get_payment(self, payment_id: int) -> PaymentORM:
        payment = self.payment_repo.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment {payment_id} was not found.")
        return payment

    def get_payment_for_user(self, payment_id: int, user_id: int) -> PaymentORM:
        payment = self.get_payment(payment_id)
        if payment.user_id != user_id:
            raise PaymentAccessDeniedError("Payment does not belong to this user.")
        return payment

    def list_payments_for_user(self, user_id: int, limit: int = 20) -> list[PaymentORM]:
        return self.payment_repo.list_for_user(user_id)[:limit]

    def get_latest_open_payment(self, user_id: int) -> PaymentORM | None:
        for payment in self.payment_repo.list_for_user(user_id):
            if payment.status in {"pending", "submitted"}:
                return payment
        return None

    def submit_payment(self, payment_id: int) -> PaymentORM:
        payment = self.get_payment(payment_id)
        if payment.status in {"submitted", "completed"}:
            return payment

        payment.status = "submitted"
        return self.payment_repo.save(payment)

    def confirm_payment(self, payment_id: int):
        payment = self.get_payment(payment_id)

        if payment.status == "completed":
            subscription = self.subscription_service.require_active_subscription(payment.user_id)
            return payment, subscription

        payment.status = "completed"
        payment = self.payment_repo.save(payment)

        moder_id = self._load_payment_assignment(payment.id)
        if moder_id is None:
            moder_id = self._load_assigned_moder(payment.user_id)
        if moder_id is not None:
            self._record_moder_payment(moder_id)
            self._dec_moder_load(moder_id)

        plan = getattr(payment, "plan", None)
        days = getattr(payment, "duration_days", None)
        if not plan or days is None:
            meta = self._load_payment_meta(payment.id)
            plan = plan or meta.get("plan", "month")
            if days is None:
                days = meta.get(
                    "duration_days",
                    self.subscription_service.default_duration_days,
                )
        subscription = self.subscription_service.activate_subscription(
            user_id=payment.user_id,
            plan=str(plan),
            days=int(days or self.subscription_service.default_duration_days),
            source_payment_id=payment.id,
        )
        self._delete_payment_meta(payment.id)
        create_job(
            "queue:vpn:create",
            {"user_id": payment.user_id},
        )
        self._delete_payment_assignment(payment.id)
        return payment, subscription

    def list_recent_payments(self, limit: int = 20) -> list[PaymentORM]:
        return self.payment_repo.list_recent(limit=limit)

    def find_by_code(self, code: str) -> PaymentORM | None:
        return self.payment_repo.get_by_code(code)

    def find_by_provider_payment_id(self, provider_payment_id: str) -> PaymentORM | None:
        return self.payment_repo.get_by_provider_payment_id(provider_payment_id)

    def get_latest_payment(self, user_id: int) -> PaymentORM | None:
        payments = self.payment_repo.list_for_user(user_id)
        return payments[0] if payments else None

    def create_webapp_payment(
        self,
        *,
        user_id: int,
        plan_period_id: int,
        return_url: str,
        payment_method_type: str = "sbp",
    ) -> PaymentORM:
        if self.plan_service is None or self.yookassa_service is None:
            raise YooKassaConfigurationError("YooKassa payment flow is not configured.")

        period = self.plan_service.get_period(plan_period_id)
        idempotence_key = generate_secret("yookassa")
        metadata_payload = {
            "plan_period_id": period.id,
            "period_key": period.period_key,
            "payment_method_type": payment_method_type,
        }
        payment = self.payment_repo.create(
            user_id=user_id,
            amount=float(period.price_amount),
            currency=str(period.currency),
            provider="yookassa",
            external_id=None,
            metadata={
                "plan": period.period_key,
                "duration_days": period.duration_days,
            },
            idempotence_key=idempotence_key,
            metadata_json=json.dumps(metadata_payload),
        )

        try:
            external_payment = self.yookassa_service.create_payment(
                amount=float(period.price_amount),
                currency=str(period.currency),
                idempotence_key=idempotence_key,
                return_url=return_url,
                description=f"MiloshVPN {period.label}",
                metadata={
                    "local_payment_id": payment.id,
                    "user_id": user_id,
                    "plan_period_id": period.id,
                    "period_key": period.period_key,
                },
                payment_method_type=payment_method_type,
            )
        except (YooKassaConfigurationError, YooKassaRequestError) as error:
            payment.status = "failed"
            payment.error_message = str(error)
            self.payment_repo.save(payment)
            raise

        self._apply_provider_payload(payment, external_payment)
        return self.payment_repo.save(payment)

    def refresh_webapp_payment(
        self,
        *,
        payment_id: int,
        user_id: int,
    ) -> PaymentORM:
        payment = self.get_payment_for_user(payment_id, user_id)
        if payment.provider != "yookassa" or not payment.provider_payment_id:
            return payment
        if self.yookassa_service is None:
            return payment
        provider_payment = self.yookassa_service.get_payment(payment.provider_payment_id)
        self._apply_provider_payload(payment, provider_payment)
        payment = self.payment_repo.save(payment)
        self._finalize_from_provider_state(payment)
        return self.get_payment(payment.id)

    def handle_yookassa_webhook(self, payload: dict[str, object]) -> dict[str, object]:
        event_type = str(payload.get("event") or "unknown")
        object_payload = payload.get("object")
        if not isinstance(object_payload, dict):
            raise YooKassaRequestError("Webhook payload does not contain payment object.")

        provider_payment_id = object_payload.get("id")
        if not isinstance(provider_payment_id, str) or not provider_payment_id:
            raise YooKassaRequestError("Webhook payload does not contain payment id.")

        payment = self.find_by_provider_payment_id(provider_payment_id)
        webhook_event = None
        if self.webhook_repo is not None:
            webhook_event = self.webhook_repo.create(
                provider="yookassa",
                event_type=event_type,
                provider_object_id=provider_payment_id,
                payment_id=payment.id if payment else None,
                payload=json.dumps(payload),
            )

        if self.yookassa_service is None:
            raise YooKassaConfigurationError("YooKassa service is not configured.")

        provider_payment = self.yookassa_service.get_payment(provider_payment_id)
        if payment is None:
            local_payment_id = provider_payment.get("metadata", {}).get("local_payment_id")
            if isinstance(local_payment_id, int):
                payment = self.payment_repo.get(local_payment_id)
            elif isinstance(local_payment_id, str) and local_payment_id.isdigit():
                payment = self.payment_repo.get(int(local_payment_id))

        if payment is None:
            if webhook_event is not None:
                webhook_event.error_message = "Local payment was not found."
                webhook_event.is_processed = True
                webhook_event.processed_at = utc_now()
                self.webhook_repo.save(webhook_event)
            raise PaymentNotFoundError("Local payment for YooKassa webhook was not found.")

        self._apply_provider_payload(payment, provider_payment)
        payment = self.payment_repo.save(payment)
        self._finalize_from_provider_state(payment)

        if webhook_event is not None:
            webhook_event.is_processed = True
            webhook_event.processed_at = utc_now()
            self.webhook_repo.save(webhook_event)

        return {
            "status": payment.status,
            "payment_id": payment.id,
            "provider_payment_id": payment.provider_payment_id,
        }

    def list_pending_payments(self) -> list[PaymentORM]:
        return [
            payment
            for payment in self.payment_repo.list_recent(50)
            if payment.status == "submitted"
        ]

    def get_moder_stats(self) -> dict[int, dict[str, float | int]]:
        accepts = self._decode_hash(redis_client.hgetall("stats:moder:accepts"))
        missed = self._decode_hash(redis_client.hgetall("stats:moder:missed"))
        payments = self._decode_hash(redis_client.hgetall("stats:moder:payments"))
        reaction_total = self._decode_hash(
            redis_client.hgetall("stats:moder:reaction_total"),
            cast=float,
        )
        reaction_count = self._decode_hash(redis_client.hgetall("stats:moder:reaction_count"))

        result: dict[int, dict[str, float | int]] = {}
        all_ids = set(accepts) | set(missed) | set(payments) | set(reaction_total) | set(reaction_count)
        for moder_id in all_ids:
            count = int(reaction_count.get(moder_id, 0))
            total = float(reaction_total.get(moder_id, 0.0))
            avg = round(total / count, 2) if count else 0.0
            result[moder_id] = {
                "accepts": int(accepts.get(moder_id, 0)),
                "missed": int(missed.get(moder_id, 0)),
                "payments": int(payments.get(moder_id, 0)),
                "avg_reaction_seconds": avg,
            }

        return result

    def get_admin_analytics(self) -> dict[str, float | int]:
        users = UserRepository().list_all()
        payments = self.payment_repo.list_recent(1000)

        total_users = len(users)
        total_moders = len([user for user in users if user.role == "moder"])
        total_revenue = sum(
            payment.amount
            for payment in payments
            if payment.status == "completed"
        )
        total_payments = len(payments)

        return {
            "users": total_users,
            "moders": total_moders,
            "revenue": total_revenue,
            "payments": total_payments,
        }

    def payment_to_web_dict(self, payment: PaymentORM) -> dict[str, object]:
        return {
            "id": payment.id,
            "user_id": payment.user_id,
            "amount": payment.amount,
            "currency": payment.currency,
            "status": payment.status,
            "provider": payment.provider,
            "provider_payment_id": payment.provider_payment_id,
            "provider_status": payment.provider_status,
            "plan": payment.plan,
            "duration_days": payment.duration_days,
            "payment_method": payment.payment_method,
            "confirmation_url": payment.confirmation_url,
            "paid_at": self._datetime_to_str(payment.paid_at),
            "canceled_at": self._datetime_to_str(payment.canceled_at),
            "created_at": self._datetime_to_str(payment.created_at),
            "updated_at": self._datetime_to_str(payment.updated_at),
            "error_message": payment.error_message,
        }

    def _record_moder_accept(self, moder_id: int) -> None:
        redis_client.hincrby("stats:moder:accepts", moder_id, 1)

    def _record_moder_miss(self, moder_id: int) -> None:
        redis_client.hincrby("stats:moder:missed", moder_id, 1)

    def _record_moder_payment(self, moder_id: int) -> None:
        redis_client.hincrby("stats:moder:payments", moder_id, 1)

    def _record_moder_reaction(self, moder_id: int, reaction_seconds: float) -> None:
        redis_client.hincrbyfloat(
            "stats:moder:reaction_total",
            moder_id,
            round(max(reaction_seconds, 0.0), 3),
        )
        redis_client.hincrby("stats:moder:reaction_count", moder_id, 1)

    def _generate_unique_code(self) -> str:
        for _ in range(10):
            code = generate_payment_code()
            if self.find_by_code(code) is None:
                return code
        raise Exception("Failed to generate unique payment code")

    def _get_online_moders(self):
        repo = UserRepository()
        now = int(time.time())

        moders = []
        for user in repo.list_all():
            if user.role != "moder" or not user.phone:
                continue

            last_seen = redis_client.get(f"moder:last_seen:{user.telegram_id}")
            if not last_seen:
                continue

            last_seen = int(last_seen)
            if now - last_seen > 60:
                continue

            load = self._get_moder_load(user.telegram_id)
            if load >= 3:
                continue

            moders.append((user, load))

        moders.sort(key=lambda item: item[1])
        return [moder[0] for moder in moders]

    def _inc_moder_load(self, moder_id: int):
        key = f"moder:load:{moder_id}"
        redis_client.incr(key)
        redis_client.expire(key, 300)

    def _dec_moder_load(self, moder_id: int):
        key = f"moder:load:{moder_id}"
        value = self._get_moder_load(moder_id)
        if value > 0:
            redis_client.decr(key)

    def _get_moder_load(self, moder_id: int) -> int:
        return max(0, int(redis_client.get(f"moder:load:{moder_id}") or 0))

    def _send_offers(self, bot_service, moders, user_id: int) -> None:
        if bot_service is None:
            return
        for moder in moders:
            bot_service._create_moder_offer(moder.telegram_id, user_id)

    def _fallback_moder(self) -> int | None:
        repo = UserRepository()
        moders = [
            user
            for user in repo.list_all()
            if user.role == "moder" and user.phone
        ]
        if not moders:
            return None
        return random.choice(moders).telegram_id

    def _assign_moderator(self, bot_service, user_id: int) -> int | None:
        key = f"assign:{user_id}"
        redis_client.delete(key)

        moders = self._get_online_moders()
        if moders and bot_service:
            started_at = time.monotonic()

            for moder in moders:
                bot_service._create_moder_offer(moder.telegram_id, user_id)

            for _ in range(10):
                accepted = redis_client.get(key)
                if accepted:
                    moder_id = self._decode_int(accepted)
                    if moder_id:
                        self._inc_moder_load(moder_id)
                        self._record_moder_accept(moder_id)
                        self._record_moder_reaction(
                            moder_id,
                            time.monotonic() - started_at,
                        )
                        redis_client.set(key, moder_id, ex=300)
                        return moder_id

                time.sleep(0.05)

            for moder in moders:
                self._record_moder_miss(moder.telegram_id)

            fallback = moders[0]
            self._inc_moder_load(fallback.telegram_id)
            redis_client.set(key, fallback.telegram_id, ex=300)
            return fallback.telegram_id

        fallback = self._fallback_moder()
        if fallback:
            self._inc_moder_load(fallback)
            redis_client.set(key, fallback, ex=300)
        return fallback

    def _payment_assignment_key(self, payment_id: int) -> str:
        return f"{self.PAYMENT_ASSIGN_KEY_PREFIX}:{payment_id}"

    def _store_payment_assignment(self, payment_id: int, moder_id: int) -> None:
        redis_client.set(self._payment_assignment_key(payment_id), str(moder_id), ex=86400)

    def _load_payment_assignment(self, payment_id: int) -> int | None:
        return self._decode_int(redis_client.get(self._payment_assignment_key(payment_id)))

    def _delete_payment_assignment(self, payment_id: int) -> None:
        redis_client.delete(self._payment_assignment_key(payment_id))

    def _load_assigned_moder(self, user_id: int) -> int | None:
        return self._decode_int(redis_client.get(f"assign:{user_id}"))

    def _decode_hash(self, payload, cast=int):
        if not payload:
            return {}

        result = {}
        for key, value in payload.items():
            moder_id = self._decode_int(key)
            if moder_id is None:
                continue

            if isinstance(value, bytes):
                value = value.decode()

            result[moder_id] = cast(value)
        return result

    def _decode_int(self, value) -> int | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode()
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    def _meta_key(self, payment_id: int) -> str:
        return f"{self.META_KEY_PREFIX}:{payment_id}"

    def _store_payment_meta(self, payment_id: int, plan: str, duration_days: int) -> None:
        try:
            redis_client.set(
                self._meta_key(payment_id),
                json.dumps(
                    {
                        "plan": plan,
                        "duration_days": duration_days,
                    }
                ),
            )
        except Exception:
            return

    def _load_payment_meta(self, payment_id: int) -> dict[str, object]:
        try:
            raw_meta = redis_client.get(self._meta_key(payment_id))
        except Exception:
            return {}
        if not raw_meta:
            return {}
        return json.loads(raw_meta)

    def _delete_payment_meta(self, payment_id: int) -> None:
        try:
            redis_client.delete(self._meta_key(payment_id))
        except Exception:
            return

    def _apply_provider_payload(
        self,
        payment: PaymentORM,
        provider_payment: dict[str, object],
    ) -> None:
        status = provider_payment.get("status")
        if isinstance(status, str):
            payment.provider_status = status
            if status == "succeeded":
                payment.paid_at = self._parse_provider_datetime(provider_payment.get("paid_at")) or utc_now()
            elif status == "canceled":
                payment.status = "canceled"
                payment.canceled_at = self._parse_provider_datetime(provider_payment.get("canceled_at")) or utc_now()
            elif payment.status not in {"completed", "canceled"}:
                payment.status = "pending"

        provider_id = provider_payment.get("id")
        if isinstance(provider_id, str):
            payment.provider_payment_id = provider_id

        confirmation = provider_payment.get("confirmation")
        if isinstance(confirmation, dict):
            confirmation_url = confirmation.get("confirmation_url")
            if isinstance(confirmation_url, str):
                payment.confirmation_url = confirmation_url

        payment_method = provider_payment.get("payment_method")
        if isinstance(payment_method, dict):
            method_type = payment_method.get("type")
            if isinstance(method_type, str):
                payment.payment_method = method_type

        try:
            payment.metadata_json = json.dumps(provider_payment.get("metadata", {}))
        except TypeError:
            pass

    def _finalize_from_provider_state(self, payment: PaymentORM) -> None:
        if payment.provider_status == "succeeded":
            if payment.status != "completed":
                payment.status = "completed"
                self.payment_repo.save(payment)
            self.confirm_payment(payment.id)
            return
        if payment.provider_status == "canceled":
            payment.status = "canceled"
            self.payment_repo.save(payment)

    def _parse_provider_datetime(self, value) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def _datetime_to_str(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()
