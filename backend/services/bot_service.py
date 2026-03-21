from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

from backend.models.orm import PaymentORM, UserORM
from backend.services.payment_service import PaymentService
from backend.services.subscription_service import (
    NoActiveSubscriptionError,
    SubscriptionService,
)
from backend.services.user_service import PermissionDeniedError, UserService
from backend.services.vpn_service import VPNService
from shared.redis import redis_client


ACTION_PROFILE = "profile"
ACTION_BUY = "buy"
ACTION_VPN = "vpn"
ACTION_ADD_MODER = "add_moder"
ACTION_REMOVE_MODER = "remove_moder"
ACTION_FIND_CODE = "find_code"
ACTION_USERS = "users"
ACTION_MODERS = "moders"
ACTION_PAYMENTS = "payments"
ACTION_MODER_STATS = "moder_stats"
ACTION_ANALYTICS = "analytics"
ACTION_BACK = "back"

ACTION_PLAN_1 = "plan_1"
ACTION_PLAN_3 = "plan_3"
ACTION_PLAN_6 = "plan_6"
ACTION_PLAN_12 = "plan_12"

ACTION_CONFIRM_PAYMENT = "confirm_payment"

ACTION_ACCEPT_USER_PREFIX = "accept_user:"
ACTION_USER_DETAILS_PREFIX = "user:"
ACTION_USER_VPN_PREFIX = "user_vpn:"
ACTION_PAYMENT_CONFIRM_PREFIX = "payment_confirm:"

STATE_IDLE = "idle"
STATE_WAIT_MODERATOR_ID = "wait_moderator_telegram_id"
STATE_WAIT_MODER_PHONE = "wait_moder_phone"
STATE_WAIT_REMOVE_MODER = "wait_remove_moder"
STATE_WAIT_PAYMENT_CODE = "wait_payment_code"
STATE_TTL = int(os.getenv("BOT_STATE_TTL", "3600"))
ONLINE_TTL = int(os.getenv("MODER_ONLINE_TTL", "60"))

PLAN_CONFIG = {
    ACTION_PLAN_1: {"days": 30, "amount": 300, "plan": "1_month", "label": "1 мес"},
    ACTION_PLAN_3: {"days": 90, "amount": 800, "plan": "3_month", "label": "3 мес"},
    ACTION_PLAN_6: {"days": 180, "amount": 1500, "plan": "6_month", "label": "6 мес"},
    ACTION_PLAN_12: {"days": 365, "amount": 2800, "plan": "12_month", "label": "12 мес"},
}


def user_to_dict(user: UserORM) -> dict[str, Any]:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "role": user.role,
        "phone": user.phone,
    }


class BotService:
    def __init__(
        self,
        user_service: UserService,
        payment_service: PaymentService,
        subscription_service: SubscriptionService,
        vpn_service: VPNService,
    ) -> None:
        self.user_service = user_service
        self.payment_service = payment_service
        self.subscription_service = subscription_service
        self.vpn_service = vpn_service

    def open_session(
        self,
        telegram_id: int,
        username: str | None = None,
    ) -> dict[str, Any]:
        user = self.user_service.register_user(
            telegram_id=telegram_id,
            username=username,
        )
        return self._response(user, self._home_ui(user))

    def handle_message(
        self,
        telegram_id: int,
        text: str | None,
        username: str | None = None,
    ) -> dict[str, Any]:
        user = self.user_service.register_user(
            telegram_id=telegram_id,
            username=username,
        )
        if user.role == "moder":
            self._set_online(telegram_id)
        action = self._resolve_action(telegram_id, (text or "").strip())

        if not action.startswith(ACTION_ACCEPT_USER_PREFIX):
            offer_ui = self._check_moder_offer(telegram_id)
            if offer_ui is not None:
                return self._response(user, offer_ui)

        if action.startswith(ACTION_ACCEPT_USER_PREFIX):
            self._require_roles(user, {"moder"})
            assigned_user_id = self._parse_action_id(action, ACTION_ACCEPT_USER_PREFIX)
            if assigned_user_id is None:
                return self._response(
                    user,
                    {
                        "text": "Некорректный оффер.",
                        "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                    },
                )

            success = redis_client.set(
                f"assign:{assigned_user_id}",
                str(user.telegram_id),
                nx=True,
                ex=30,
            )
            redis_client.delete(f"offer:{user.telegram_id}")
            text = (
                "✅ Ты принял пользователя"
                if success
                else "❌ Уже принят другим модератором"
            )
            return self._response(
                user,
                {
                    "text": text,
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        if action == "/start":
            self._clear_state(telegram_id)
            return self._response(user, self._home_ui(user))

        state = self._get_state(telegram_id)
        if state == STATE_WAIT_MODERATOR_ID:
            return self._handle_moderator_id_step(user, action)
        if state.startswith(f"{STATE_WAIT_MODER_PHONE}:"):
            return self._handle_moderator_phone_step(user, state, action)
        if state == STATE_WAIT_REMOVE_MODER:
            return self._handle_remove_moderator_step(user, action)
        if state == STATE_WAIT_PAYMENT_CODE:
            return self._handle_payment_code_step(user, action)

        if action == ACTION_PROFILE:
            return self._response(user, self._profile_ui(user))

        if action == ACTION_BUY:
            return self._handle_buy(user)

        if action in PLAN_CONFIG:
            return self._handle_plan(user, action)

        if action == ACTION_CONFIRM_PAYMENT:
            return self._handle_submit_payment(user)

        if action == ACTION_VPN:
            return self._handle_vpn(user)

        if action == ACTION_USERS:
            return self._users_ui(user)

        if action == ACTION_MODERS:
            return self._moders_ui(user)

        if action == ACTION_ADD_MODER:
            return self._handle_add_moderator_request(user)

        if action == ACTION_REMOVE_MODER:
            return self._handle_remove_moderator_request(user)

        if action == ACTION_FIND_CODE:
            return self._handle_find_code_request(user)

        if action == ACTION_PAYMENTS:
            return self._payments_ui(user)

        if action == ACTION_MODER_STATS:
            return self._moder_stats_ui(user)

        if action == ACTION_ANALYTICS:
            return self._analytics_ui(user)

        if action == ACTION_BACK:
            self._clear_state(telegram_id)
            return self._response(user, self._home_ui(user))

        if action.startswith(ACTION_USER_DETAILS_PREFIX):
            return self._user_details_ui(
                user,
                self._parse_action_id(action, ACTION_USER_DETAILS_PREFIX),
            )

        if action.startswith(ACTION_USER_VPN_PREFIX):
            return self._issue_vpn_for_user(
                user,
                self._parse_action_id(action, ACTION_USER_VPN_PREFIX),
            )

        if action.startswith(ACTION_PAYMENT_CONFIRM_PREFIX):
            return self._confirm_payment_as_staff(
                user,
                self._parse_action_id(action, ACTION_PAYMENT_CONFIRM_PREFIX),
            )

        return self._response(user, self._home_ui(user))

    def _handle_buy(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"user"})
        return self._response(user, self._buy_ui())

    def _handle_plan(self, user: UserORM, action: str) -> dict[str, Any]:
        self._require_roles(user, {"user"})
        plan_config = PLAN_CONFIG[action]
        payment = self.payment_service.create_payment(
            user_id=user.id,
            amount=float(plan_config["amount"]),
            duration_days=int(plan_config["days"]),
            plan=str(plan_config["plan"]),
        )
        return self._response(
            user,
            {
                "text": (
                    "💳 Оплата\n\n"
                    f"Тариф: {plan_config['label']}\n"
                    f"Сумма: {plan_config['amount']} RUB\n\n"
                    "Перевод на номер:\n"
                    f"{payment.recipient_phone}\n\n"
                    "Комментарий:\n"
                    f"{payment.payment_code}\n\n"
                    "⚠️ Обязательно вставь код в комментарий\n\n"
                    "📘 Инструкция:\n"
                    "1. Установи WireGuard\n"
                    "2. Переведи сумму на номер выше\n"
                    "3. В комментарии укажи код платежа\n"
                    "4. После оплаты нажми «Я оплатил»\n"
                    "5. После подтверждения получишь доступ"
                ),
                "keyboard": [
                    [self._button("✅ Я оплатил", ACTION_CONFIRM_PAYMENT)],
                    [self._button("⬅️ Назад", ACTION_BACK)],
                ],
            },
        )

    def _handle_submit_payment(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"user"})
        payment = self.payment_service.get_latest_open_payment(user.id)
        if payment is None:
            return self._response(
                user,
                {
                    "text": "У тебя нет платежей, ожидающих проверки.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        if payment.status == "submitted":
            return self._response(
                user,
                {
                    "text": (
                        "Платеж уже в очереди на проверку.\n"
                        f"ID: {payment.id}\n"
                        f"Код: {payment.payment_code}\n"
                        "Ожидай подтверждения модератора."
                    ),
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        payment = self.payment_service.submit_payment(payment.id)
        return self._response(
            user,
            {
                "text": (
                    "Платеж отправлен в очередь на проверку ✅\n"
                    f"ID: {payment.id}\n"
                    f"Код: {payment.payment_code}\n"
                    "Как только модератор подтвердит оплату, подписка станет активной."
                ),
                "keyboard": [
                    [self._button("🔐 Мой VPN", ACTION_VPN)],
                    [self._button("⬅️ Назад", ACTION_BACK)],
                ],
            },
        )

    def _handle_vpn(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"user"})
        try:
            access = self.vpn_service.provision_access(user.id)
        except NoActiveSubscriptionError:
            return self._response(
                user,
                {
                    "text": "Нет активной подписки. Сначала оформи оплату.",
                    "keyboard": [
                        [self._button("💳 Купить подписку", ACTION_BUY)],
                        [self._button("⬅️ Назад", ACTION_BACK)],
                    ],
                },
            )

        return self._response(
            user,
            {
                "text": (
                    "VPN доступ готов.\n"
                    f"Сервер: {access.server_name}\n"
                    f"Протокол: {access.protocol}\n"
                    f"URL: {access.access_url}"
                ),
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _users_ui(self, actor: UserORM) -> dict[str, Any]:
        self._require_roles(actor, {"moder", "admin"})

        users = sorted(self.user_service.list_users(), key=lambda item: item.id, reverse=True)[:10]
        if not users:
            return self._response(
                actor,
                {
                    "text": "Пользователей пока нет.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        lines = []
        keyboard = []
        for user in users:
            lines.append(
                f"{user.id} | tg:{user.telegram_id} | {user.role} | {self._subscription_status_text(user.id)}"
            )
            keyboard.append(
                [self._button(self._user_button_label(user), f"{ACTION_USER_DETAILS_PREFIX}{user.id}")]
            )

        keyboard.append([self._button("⬅️ Назад", ACTION_BACK)])
        return self._response(
            actor,
            {
                "text": "Пользователи:\n" + "\n".join(lines),
                "keyboard": keyboard,
            },
        )

    def _user_details_ui(self, actor: UserORM, target_user_id: int | None) -> dict[str, Any]:
        self._require_roles(actor, {"moder", "admin"})
        if target_user_id is None:
            return self._response(actor, self._home_ui(actor, text="Некорректный пользователь."))

        target = self.user_service.require_user(target_user_id)
        return self._response(
            actor,
            {
                "text": (
                    "Пользователь\n"
                    f"ID: {target.id}\n"
                    f"Telegram ID: {target.telegram_id}\n"
                    f"Username: {self._username_text(target.username)}\n"
                    f"Телефон: {target.phone or '-'}\n"
                    f"Роль: {target.role}\n"
                    f"Подписка: {self._subscription_status_text(target.id)}\n"
                    f"VPN: {self._vpn_status_text(target.id)}"
                ),
                "keyboard": [
                    [self._button("🔐 Выдать VPN", f"{ACTION_USER_VPN_PREFIX}{target.id}")],
                    [self._button("⬅️ К пользователям", ACTION_USERS)],
                    [self._button("⬅️ В меню", ACTION_BACK)],
                ],
            },
        )

    def _issue_vpn_for_user(self, actor: UserORM, target_user_id: int | None) -> dict[str, Any]:
        self._require_roles(actor, {"moder", "admin"})
        if target_user_id is None:
            return self._response(actor, self._home_ui(actor, text="Некорректный пользователь."))

        target = self.user_service.require_user(target_user_id)
        try:
            access = self.vpn_service.provision_access(target.id)
        except NoActiveSubscriptionError:
            return self._response(
                actor,
                {
                    "text": (
                        "VPN не выдан.\n"
                        f"У пользователя {target.telegram_id} нет активной подписки."
                    ),
                    "keyboard": [
                        [self._button("⬅️ К пользователям", ACTION_USERS)],
                        [self._button("⬅️ В меню", ACTION_BACK)],
                    ],
                },
            )

        return self._response(
            actor,
            {
                "text": (
                    "VPN выдан.\n"
                    f"Пользователь: {target.telegram_id}\n"
                    f"URL: {access.access_url}"
                ),
                "keyboard": [
                    [self._button("⬅️ К пользователям", ACTION_USERS)],
                    [self._button("⬅️ В меню", ACTION_BACK)],
                ],
            },
        )

    def _payments_ui(self, actor: UserORM, notice: str | None = None) -> dict[str, Any]:
        self._require_roles(actor, {"admin"})

        payments = self.payment_service.list_recent_payments(limit=10)
        if not payments:
            return self._response(
                actor,
                {
                    "text": notice or "Платежей пока нет.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        lines = []
        keyboard = []
        for payment in payments:
            lines.append(self._payment_line(payment))
            if payment.status in {"pending", "submitted"}:
                keyboard.append(
                    [
                        self._button(
                            f"Подтвердить #{payment.id}",
                            f"{ACTION_PAYMENT_CONFIRM_PREFIX}{payment.id}",
                        )
                    ]
                )

        keyboard.append([self._button("⬅️ Назад", ACTION_BACK)])
        text = "\n".join(lines)
        if notice:
            text = notice + "\n\n" + text

        return self._response(
            actor,
            {
                "text": "Платежи:\n" + text,
                "keyboard": keyboard,
            },
        )

    def _moders_ui(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"admin"})

        moders = [
            candidate
            for candidate in self.user_service.list_users()
            if candidate.role == "moder"
        ]

        if not moders:
            return self._response(
                user,
                {
                    "text": "Модеров нет",
                    "keyboard": [
                        [self._button("➕ Добавить", ACTION_ADD_MODER)],
                        [self._button("➖ Удалить", ACTION_REMOVE_MODER)],
                        [self._button("⬅️ Назад", ACTION_BACK)],
                    ],
                },
            )

        text = "\n".join(
            f"{moder.telegram_id} | {moder.phone or '-'}"
            for moder in moders
        )
        return self._response(
            user,
            {
                "text": "Модеры:\n\n" + text,
                "keyboard": [
                    [self._button("➕ Добавить", ACTION_ADD_MODER)],
                    [self._button("➖ Удалить", ACTION_REMOVE_MODER)],
                    [self._button("⬅️ Назад", ACTION_BACK)],
                ],
            },
        )

    def _moder_stats_ui(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"admin"})

        stats = self.payment_service.get_moder_stats()
        moderators = [
            candidate
            for candidate in self.user_service.list_users()
            if candidate.role == "moder"
        ]

        if not moderators:
            return self._response(
                user,
                {
                    "text": "📊 Модераторов пока нет.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        lines = []
        for moderator in sorted(moderators, key=lambda item: item.telegram_id):
            values = stats.get(moderator.telegram_id, {})
            last_seen = redis_client.get(f"moder:last_seen:{moderator.telegram_id}")
            load = int(redis_client.get(f"moder:load:{moderator.telegram_id}") or 0)
            online = "online" if last_seen else "offline"
            lines.append(
                f"{moderator.telegram_id} | {moderator.phone or '-'} | {online}\n"
                f"Нагрузка: {load}\n"
                f"Принял: {values.get('accepts', 0)}\n"
                f"Пропустил: {values.get('missed', 0)}\n"
                f"Подтвердил оплат: {values.get('payments', 0)}\n"
                f"Среднее время реакции: {values.get('avg_reaction_seconds', 0.0)} сек"
            )

        return self._response(
            user,
            {
                "text": "📊 Статистика модераторов:\n\n" + "\n\n".join(lines),
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _analytics_ui(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"admin"})

        data = self.payment_service.get_admin_analytics()
        return self._response(
            user,
            {
                "text": (
                    "📊 Аналитика\n\n"
                    f"👥 Пользователи: {data['users']}\n"
                    f"👨‍💼 Модеры: {data['moders']}\n"
                    f"💰 Доход: {data['revenue']} RUB\n"
                    f"💳 Платежи: {data['payments']}"
                ),
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _confirm_payment_as_staff(self, actor: UserORM, payment_id: int | None) -> dict[str, Any]:
        self._require_roles(actor, {"moder", "admin"})
        if payment_id is None:
            return self._response(actor, self._home_ui(actor, text="Некорректный платеж."))

        payment, subscription = self.payment_service.confirm_payment(payment_id)
        notice = (
            "Платеж подтвержден ✅\n"
            f"Payment ID: {payment.id}\n"
            f"User ID: {payment.user_id}\n"
            f"Код: {payment.payment_code}\n"
            f"Подписка активна до: {self._format_datetime(subscription.ends_at)}"
        )
        if actor.role == "admin":
            return self._payments_ui(actor, notice=notice)

        return self._response(
            actor,
            {
                "text": notice,
                "keyboard": [
                    [self._button("🔍 Найти оплату", ACTION_FIND_CODE)],
                    [self._button("📋 Пользователи", ACTION_USERS)],
                    [self._button("⬅️ В меню", ACTION_BACK)],
                ],
            },
        )

    def _handle_add_moderator_request(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"admin"})
        self._set_state(user.telegram_id, STATE_WAIT_MODERATOR_ID)
        return self._response(
            user,
            {
                "text": "Отправь Telegram ID пользователя. Следующим сообщением пришли номер телефона модера.",
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _handle_moderator_id_step(self, user: UserORM, text: str) -> dict[str, Any]:
        if text == ACTION_BACK:
            self._clear_state(user.telegram_id)
            return self._response(user, self._home_ui(user, text="Создание модера отменено."))

        self._require_roles(user, {"admin"})
        if not text.isdigit():
            return self._response(
                user,
                {
                    "text": "Нужен числовой Telegram ID пользователя.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        self._set_state(user.telegram_id, f"{STATE_WAIT_MODER_PHONE}:{text}")
        return self._response(
            user,
            {
                "text": "Теперь отправь номер телефона модера.",
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _handle_moderator_phone_step(
        self,
        user: UserORM,
        state: str,
        text: str,
    ) -> dict[str, Any]:
        if text == ACTION_BACK:
            self._clear_state(user.telegram_id)
            return self._response(user, self._home_ui(user, text="Создание модера отменено."))

        self._require_roles(user, {"admin"})
        target_telegram_id = self._parse_state_id(state, f"{STATE_WAIT_MODER_PHONE}:")
        if target_telegram_id is None:
            self._clear_state(user.telegram_id)
            return self._response(user, self._home_ui(user, text="Состояние сброшено. Повтори создание модера."))

        if not text:
            return self._response(
                user,
                {
                    "text": "Нужен номер телефона модера.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        moderator = self.user_service.assign_moderator(
            actor_telegram_id=user.telegram_id,
            target_telegram_id=target_telegram_id,
            phone=text,
        )
        self._clear_state(user.telegram_id)
        return self._response(
            user,
            {
                "text": (
                    "Модератор назначен ✅\n"
                    f"Telegram ID: {moderator.telegram_id}\n"
                    f"Телефон: {moderator.phone or '-'}\n"
                    f"Роль: {moderator.role}"
                ),
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _handle_remove_moderator_request(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"admin"})
        self._set_state(user.telegram_id, STATE_WAIT_REMOVE_MODER)
        return self._response(
            user,
            {
                "text": "Отправь Telegram ID модера, которого нужно удалить.",
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _handle_remove_moderator_step(self, user: UserORM, text: str) -> dict[str, Any]:
        if text == ACTION_BACK:
            self._clear_state(user.telegram_id)
            return self._response(user, self._home_ui(user, text="Удаление модера отменено."))

        self._require_roles(user, {"admin"})
        if not text.isdigit():
            return self._response(
                user,
                {
                    "text": "Нужен числовой Telegram ID модера.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        moderator = self.user_service.remove_moderator(user.telegram_id, int(text))
        self._clear_state(user.telegram_id)
        return self._response(
            user,
            {
                "text": (
                    "Модератор удален ✅\n"
                    f"Telegram ID: {moderator.telegram_id}\n"
                    f"Новая роль: {moderator.role}"
                ),
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _handle_find_code_request(self, user: UserORM) -> dict[str, Any]:
        self._require_roles(user, {"moder", "admin"})
        self._set_state(user.telegram_id, STATE_WAIT_PAYMENT_CODE)
        return self._response(
            user,
            {
                "text": "Введи код платежа.",
                "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
            },
        )

    def _handle_payment_code_step(self, user: UserORM, text: str) -> dict[str, Any]:
        if text == ACTION_BACK:
            self._clear_state(user.telegram_id)
            return self._response(user, self._home_ui(user))

        self._require_roles(user, {"moder", "admin"})
        if not text:
            return self._response(
                user,
                {
                    "text": "Нужен код платежа.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        payment = self.payment_service.find_by_code(text)
        if payment is None:
            return self._response(
                user,
                {
                    "text": "Платеж с таким кодом не найден. Проверь код и отправь снова.",
                    "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
                },
            )

        self._clear_state(user.telegram_id)
        keyboard = [[self._button("⬅️ В меню", ACTION_BACK)]]
        if payment.status in {"pending", "submitted"}:
            keyboard.insert(
                0,
                [self._button("Подтвердить", f"{ACTION_PAYMENT_CONFIRM_PREFIX}{payment.id}")],
            )

        return self._response(
            user,
            {
                "text": (
                    "Платеж найден\n"
                    f"ID: {payment.id}\n"
                    f"User: {payment.user_id}\n"
                    f"Сумма: {int(payment.amount)} {payment.currency}\n"
                    f"Статус: {payment.status}\n"
                    f"Код: {payment.payment_code}"
                ),
                "keyboard": keyboard,
            },
        )

    def _home_ui(self, user: UserORM, text: str | None = None) -> dict[str, Any]:
        buttons: list[list[dict[str, str]]] = []

        if user.role == "user":
            buttons.extend(
                [
                    [self._button("💳 Купить подписку", ACTION_BUY)],
                    [self._button("🔐 Мой VPN", ACTION_VPN)],
                ]
            )

        if user.role in {"moder", "admin"}:
            buttons.extend(
                [
                    [self._button("📋 Пользователи", ACTION_USERS)],
                    [self._button("🔍 Найти оплату", ACTION_FIND_CODE)],
                ]
            )

        if user.role == "admin":
            buttons.extend(
                [
                    [self._button("👨‍💼 Модеры", ACTION_MODERS)],
                    [self._button("📊 Аналитика", ACTION_ANALYTICS)],
                    [self._button("➕ Добавить модера", ACTION_ADD_MODER)],
                    [self._button("➖ Удалить модера", ACTION_REMOVE_MODER)],
                    [self._button("💳 Платежи", ACTION_PAYMENTS)],
                    [self._button("📊 Статистика модеров", ACTION_MODER_STATS)],
                ]
            )

        buttons.append([self._button("👤 Профиль", ACTION_PROFILE)])
        return {
            "text": text or f"Меню ({user.role})",
            "keyboard": buttons,
        }

    def _profile_ui(self, user: UserORM) -> dict[str, Any]:
        return {
            "text": (
                "Профиль\n"
                f"Telegram ID: {user.telegram_id}\n"
                f"Username: {self._username_text(user.username)}\n"
                f"Телефон: {user.phone or '-'}\n"
                f"Роль: {user.role}\n"
                f"Статус подписки: {self._subscription_status_text(user.id)}\n"
                f"VPN: {self._vpn_status_text(user.id)}"
            ),
            "keyboard": [[self._button("⬅️ Назад", ACTION_BACK)]],
        }

    def _buy_ui(self) -> dict[str, Any]:
        return {
            "text": "Выбери тариф:",
            "keyboard": [
                [self._button("1 мес", ACTION_PLAN_1)],
                [self._button("3 мес", ACTION_PLAN_3)],
                [self._button("6 мес", ACTION_PLAN_6)],
                [self._button("12 мес", ACTION_PLAN_12)],
                [self._button("⬅️ Назад", ACTION_BACK)],
            ],
        }

    def _payment_line(self, payment: PaymentORM) -> str:
        code = payment.payment_code or "-"
        phone = payment.recipient_phone or "-"
        return (
            f"#{payment.id} | user:{payment.user_id} | {int(payment.amount)} RUB | "
            f"{payment.status} | {payment.plan} | {code} | {phone}"
        )

    def _subscription_status_text(self, user_id: int) -> str:
        active = self.subscription_service.get_active_subscription(user_id)
        if active is not None:
            return f"active до {self._format_datetime(active.ends_at)}"

        latest = self.subscription_service.get_latest_subscription(user_id)
        if latest is None:
            return "нет"

        return f"{latest.status} до {self._format_datetime(latest.ends_at)}"

    def _vpn_status_text(self, user_id: int) -> str:
        access = self.vpn_service.get_access(user_id)
        if access is None:
            return "не выдан"
        return f"активен ({access.access_url})"

    def _format_datetime(self, value: datetime | None) -> str:
        if value is None:
            return "-"
        return value.strftime("%Y-%m-%d %H:%M:%S")

    def _user_button_label(self, user: UserORM) -> str:
        return f"{user.id} | {self._username_text(user.username)}"

    def _username_text(self, username: str | None) -> str:
        if not username:
            return "-"
        return f"@{username}"

    def _button(self, text: str, action: str) -> dict[str, str]:
        return {
            "text": text,
            "action": action,
        }

    def _set_online(self, telegram_id: int) -> None:
        redis_client.set(
            f"moder:last_seen:{telegram_id}",
            int(time.time()),
            ex=ONLINE_TTL,
        )

    def _create_moder_offer(self, moder_tg_id: int, user_id: int) -> None:
        redis_client.set(
            f"offer:{moder_tg_id}",
            str(user_id),
            ex=15,
        )

    def _check_moder_offer(self, telegram_id: int) -> dict[str, Any] | None:
        user_id = redis_client.get(f"offer:{telegram_id}")
        if not user_id:
            return None

        if isinstance(user_id, bytes):
            user_id = user_id.decode()
        else:
            user_id = str(user_id)

        if redis_client.get(f"assign:{user_id}"):
            redis_client.delete(f"offer:{telegram_id}")
            return None

        return {
            "text": f"Новый пользователь {user_id}\nПринять?",
            "keyboard": [
                [self._button("✅ Принять", f"{ACTION_ACCEPT_USER_PREFIX}{user_id}")]
            ],
        }

    def _response(self, user: UserORM, ui: dict[str, Any]) -> dict[str, Any]:
        self._store_ui_actions(user.telegram_id, ui)
        return {
            "user": user_to_dict(user),
            "ui": {
                "text": str(ui.get("text", "")),
                "keyboard": ui.get("keyboard") or [],
            },
        }

    def _ui_actions_key(self, telegram_id: int) -> str:
        return f"bot:ui:{telegram_id}"

    def _store_ui_actions(self, telegram_id: int, ui: dict[str, Any]) -> None:
        keyboard = ui.get("keyboard") or []
        actions: dict[str, str] = {}
        for row in keyboard:
            if not isinstance(row, list):
                continue
            for button in row:
                if not isinstance(button, dict):
                    continue
                text = button.get("text")
                action = button.get("action")
                if isinstance(text, str) and isinstance(action, str):
                    actions[f"{text}|{action}"] = action

        if not actions:
            redis_client.delete(self._ui_actions_key(telegram_id))
            return

        redis_client.set(
            self._ui_actions_key(telegram_id),
            json.dumps(actions),
            ex=STATE_TTL,
        )

    def _resolve_action(self, telegram_id: int, raw_text: str) -> str:
        if not raw_text:
            return raw_text

        payload = redis_client.get(self._ui_actions_key(telegram_id))
        if not payload:
            return raw_text

        if isinstance(payload, bytes):
            payload = payload.decode()

        try:
            actions = json.loads(payload)
        except (TypeError, ValueError):
            return raw_text

        if not isinstance(actions, dict):
            return raw_text

        for key, value in actions.items():
            if raw_text in key and isinstance(value, str):
                return value
        return raw_text

    def _parse_action_id(self, action: str, prefix: str) -> int | None:
        raw_value = action[len(prefix) :]
        if not raw_value.isdigit():
            return None
        return int(raw_value)

    def _parse_state_id(self, state: str, prefix: str) -> int | None:
        raw_value = state[len(prefix) :]
        if not raw_value.isdigit():
            return None
        return int(raw_value)

    def _require_roles(self, user: UserORM, allowed_roles: set[str]) -> None:
        if user.role not in allowed_roles:
            raise PermissionDeniedError("Недостаточно прав.")

    def _state_key(self, telegram_id: int) -> str:
        return f"bot:state:{telegram_id}"

    def _get_state(self, telegram_id: int) -> str:
        value = redis_client.get(self._state_key(telegram_id))
        if value is None:
            return STATE_IDLE
        if isinstance(value, bytes):
            return value.decode()
        return str(value)

    def _set_state(self, telegram_id: int, state: str) -> None:
        redis_client.set(self._state_key(telegram_id), state, ex=STATE_TTL)

    def _clear_state(self, telegram_id: int) -> None:
        redis_client.delete(self._state_key(telegram_id))
