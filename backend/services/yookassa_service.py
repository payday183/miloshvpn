from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from backend.config import settings
from backend.services.admin_setting_service import AdminSettingService


class YooKassaConfigurationError(Exception):
    pass


class YooKassaRequestError(Exception):
    pass


class YooKassaService:
    def __init__(self, admin_setting_service: AdminSettingService) -> None:
        self.admin_setting_service = admin_setting_service

    def get_runtime_config(self) -> dict[str, str]:
        shop_id_setting = self.admin_setting_service.setting_repo.get_by_key("yookassa_shop_id")
        secret_key_setting = self.admin_setting_service.setting_repo.get_by_key("yookassa_secret_key")
        mode_setting = self.admin_setting_service.setting_repo.get_by_key("yookassa_mode")

        shop_id = (shop_id_setting.value if shop_id_setting and shop_id_setting.value else settings.yookassa_shop_id).strip()
        secret_key = (
            secret_key_setting.value
            if secret_key_setting and secret_key_setting.value
            else settings.yookassa_secret_key
        ).strip()
        mode = (mode_setting.value if mode_setting and mode_setting.value else settings.yookassa_mode).strip() or "test"

        if not shop_id or not secret_key:
            raise YooKassaConfigurationError("YooKassa credentials are not configured.")

        return {
            "shop_id": shop_id,
            "secret_key": secret_key,
            "mode": mode,
            "api_base_url": settings.yookassa_api_base_url,
        }

    def create_payment(
        self,
        *,
        amount: float,
        currency: str,
        idempotence_key: str,
        return_url: str,
        description: str,
        metadata: dict[str, object],
        payment_method_type: str = "sbp",
    ) -> dict[str, Any]:
        payload = {
            "amount": {
                "value": self._format_amount(amount),
                "currency": currency,
            },
            "payment_method_data": {
                "type": payment_method_type,
            },
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
            "capture": True,
            "description": description,
            "metadata": metadata,
        }
        return self._request(
            "POST",
            "/payments",
            payload=payload,
            idempotence_key=idempotence_key,
        )

    def get_payment(self, provider_payment_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/payments/{provider_payment_id}",
        )

    def get_public_state(self) -> dict[str, object]:
        shop_id_setting = self.admin_setting_service.setting_repo.get_by_key("yookassa_shop_id")
        secret_key_setting = self.admin_setting_service.setting_repo.get_by_key("yookassa_secret_key")
        mode_setting = self.admin_setting_service.setting_repo.get_by_key("yookassa_mode")
        return {
            "configured": bool(
                (shop_id_setting and shop_id_setting.value) or settings.yookassa_shop_id
            )
            and bool(
                (secret_key_setting and secret_key_setting.value) or settings.yookassa_secret_key
            ),
            "shop_id": shop_id_setting.value if shop_id_setting and shop_id_setting.value else settings.yookassa_shop_id or None,
            "mode": mode_setting.value if mode_setting and mode_setting.value else settings.yookassa_mode,
            "secret_key_present": bool(
                (secret_key_setting and secret_key_setting.value) or settings.yookassa_secret_key
            ),
            "webhook_url": f"{settings.public_base_url}/api/webhooks/yookassa",
            "return_url": f"{settings.public_base_url}/webapp/user",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        idempotence_key: str | None = None,
    ) -> dict[str, Any]:
        config = self.get_runtime_config()
        headers: dict[str, str] = {}
        if idempotence_key:
            headers["Idempotence-Key"] = idempotence_key

        try:
            with httpx.Client(
                base_url=config["api_base_url"],
                auth=(config["shop_id"], config["secret_key"]),
                timeout=20.0,
            ) as client:
                response = client.request(
                    method,
                    path,
                    json=payload,
                    headers=headers,
                )
        except httpx.RequestError as error:
            raise YooKassaRequestError("Failed to reach YooKassa API.") from error

        if response.is_error:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise YooKassaRequestError(f"YooKassa API error: {detail}")

        data = response.json()
        if not isinstance(data, dict):
            raise YooKassaRequestError("YooKassa API returned an unexpected response.")
        return data

    def _format_amount(self, amount: float) -> str:
        value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format(value, "f")
