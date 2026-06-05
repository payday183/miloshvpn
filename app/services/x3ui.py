from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from uuid import uuid4

import httpx

from app.config import Settings, get_settings


@dataclass(frozen=True)
class ProvisionedClient:
    client_uuid: str
    email: str
    vless_uri: str


class X3UIError(RuntimeError):
    pass


class X3UIClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def create_client(
        self,
        *,
        email: str,
        telegram_id: int | None,
        expires_at: datetime | None,
        traffic_gb: int | None,
    ) -> ProvisionedClient:
        client_uuid = str(uuid4())
        vless_uri = self.build_vless_uri(client_uuid=client_uuid, label=email)

        if self.settings.x3ui_mode == "mock":
            return ProvisionedClient(client_uuid=client_uuid, email=email, vless_uri=vless_uri)

        expiry_ms = int(expires_at.timestamp() * 1000) if expires_at else 0
        total_gb = int(Decimal(traffic_gb or 0) * Decimal(1024**3))
        payload = {
            "id": self.settings.x3ui_inbound_id,
            "settings": json.dumps({
                "clients": [
                    {
                        "id": client_uuid,
                        "email": email,
                        "enable": True,
                        "expiryTime": expiry_ms,
                        "totalGB": total_gb,
                        "tgId": str(telegram_id or ""),
                        "subId": client_uuid.replace("-", "")[:16],
                    }
                ]
            }),
        }

        async with self._client() as client:
            await self._login(client)
            response = await client.post("/panel/api/inbounds/addClient", json=payload)
            self._raise_for_x3ui(response)

        return ProvisionedClient(client_uuid=client_uuid, email=email, vless_uri=vless_uri)

    async def revoke_client(self, *, client_uuid: str) -> None:
        if self.settings.x3ui_mode == "mock":
            return

        async with self._client() as client:
            await self._login(client)
            response = await client.post(
                f"/panel/api/inbounds/{self.settings.x3ui_inbound_id}/delClient/{client_uuid}"
            )
            if response.status_code == 404:
                return
            self._raise_for_x3ui(response)

    def build_vless_uri(self, *, client_uuid: str, label: str) -> str:
        safe_label = label.replace(" ", "_")
        return (
            f"vless://{client_uuid}@{self.settings.vless_public_host}:{self.settings.vless_public_port}"
            f"?{self.settings.vless_query}#{safe_label}"
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.settings.x3ui_base_url, timeout=20.0, follow_redirects=True)

    async def _login(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/login",
            data={"username": self.settings.x3ui_username, "password": self.settings.x3ui_password},
        )
        self._raise_for_x3ui(response)

    @staticmethod
    def _raise_for_x3ui(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise X3UIError(f"3x-ui HTTP error: {exc.response.status_code} {exc.response.text[:200]}") from exc

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            data = response.json()
            if data.get("success") is False:
                raise X3UIError(f"3x-ui API error: {data}")
