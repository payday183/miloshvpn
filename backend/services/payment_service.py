import random
import time
import json

from backend.models.orm import PaymentORM
from backend.repositories.payment_repo import PaymentRepository
from backend.repositories.user_repo import UserRepository
from backend.services.subscription_service import SubscriptionService
from shared.queue import create_job
from shared.redis import redis_client
from shared.utils import generate_payment_code


class PaymentNotFoundError(Exception):
    pass


class PaymentService:
    META_KEY_PREFIX = "payment_meta"
    PAYMENT_ASSIGN_KEY_PREFIX = "payment_assign"

    def __init__(
        self,
        payment_repo: PaymentRepository,
        subscription_service: SubscriptionService,
    ) -> None:
        self.payment_repo = payment_repo
        self.subscription_service = subscription_service
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
