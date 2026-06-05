from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.config import Settings, get_settings
from app.models import VpnNode


@dataclass(frozen=True)
class ProvisionedClient:
    client_uuid: str
    email: str
    vless_uri: str


@dataclass(frozen=True)
class NodeSnapshot:
    status: str
    latency_ms: int | None
    remote_clients: int
    remote_enabled_clients: int
    traffic_up_bytes: int
    traffic_down_bytes: int
    cpu_percent: int | None
    memory_percent: int | None
    disk_percent: int | None
    error: str | None = None


@dataclass(frozen=True)
class X3UITarget:
    mode: str
    base_url: str
    username: str
    password: str
    inbound_id: int
    public_host: str
    public_port: int
    vless_query: str


class X3UIError(RuntimeError):
    pass


class X3UIClient:
    def __init__(self, settings: Settings | None = None, node: VpnNode | None = None) -> None:
        self.settings = settings or get_settings()
        self.target = self._target_from_node(node)

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

        if self.target.mode == "mock":
            return ProvisionedClient(client_uuid=client_uuid, email=email, vless_uri=vless_uri)

        expiry_ms = int(expires_at.timestamp() * 1000) if expires_at else 0
        total_gb = int(Decimal(traffic_gb or 0) * Decimal(1024**3))
        legacy_payload = {
            "id": self.target.inbound_id,
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
            response = await client.post(
                "/panel/api/clients/add",
                json={
                    "client": {
                        "email": email,
                        "uuid": client_uuid,
                        "subId": client_uuid.replace("-", "")[:16],
                        "totalGB": total_gb,
                        "expiryTime": expiry_ms,
                        "tgId": telegram_id or 0,
                        "limitIp": 0,
                        "enable": True,
                    },
                    "inboundIds": [self.target.inbound_id],
                },
            )
            if response.status_code == 404:
                response = await client.post("/panel/api/inbounds/addClient", json=legacy_payload)
                self._raise_for_x3ui(response)
            else:
                self._raise_for_x3ui(response)
                client_uuid = await self._client_uuid_by_email(client, email) or client_uuid

        vless_uri = self.build_vless_uri(client_uuid=client_uuid, label=email)
        return ProvisionedClient(client_uuid=client_uuid, email=email, vless_uri=vless_uri)

    async def revoke_client(self, *, client_uuid: str, email: str | None = None) -> None:
        if self.target.mode == "mock":
            return

        async with self._client() as client:
            await self._login(client)
            if email:
                response = await client.post(
                    f"/panel/api/clients/del/{quote(email, safe='')}",
                    params={"keepTraffic": 0},
                )
                if response.status_code != 404:
                    self._raise_for_x3ui(response)
                    return

            response = await client.post(f"/panel/api/inbounds/{self.target.inbound_id}/delClient/{client_uuid}")
            if response.status_code != 404:
                self._raise_for_x3ui(response)

    async def collect_snapshot(self) -> NodeSnapshot:
        if self.target.mode == "mock":
            return NodeSnapshot(
                status="online",
                latency_ms=0,
                remote_clients=0,
                remote_enabled_clients=0,
                traffic_up_bytes=0,
                traffic_down_bytes=0,
                cpu_percent=0,
                memory_percent=0,
                disk_percent=0,
            )

        started = time.monotonic()
        try:
            async with self._client() as client:
                await self._login(client)
                status_response = await client.get("/panel/api/server/status")
                self._raise_for_x3ui(status_response)
                inbound_response = await client.get("/panel/api/inbounds/list")
                self._raise_for_x3ui(inbound_response)
                latency_ms = int((time.monotonic() - started) * 1000)
                status_data = self._payload_data(status_response)
                inbound_data = self._payload_data(inbound_response)
                return self._snapshot_from_payload(status_data, inbound_data, latency_ms)
        except Exception as exc:
            return NodeSnapshot(
                status="offline",
                latency_ms=None,
                remote_clients=0,
                remote_enabled_clients=0,
                traffic_up_bytes=0,
                traffic_down_bytes=0,
                cpu_percent=None,
                memory_percent=None,
                disk_percent=None,
                error=str(exc)[:500],
            )

    def build_vless_uri(self, *, client_uuid: str, label: str) -> str:
        safe_label = label.replace(" ", "_")
        return (
            f"vless://{client_uuid}@{self.target.public_host}:{self.target.public_port}"
            f"?{self.target.vless_query}#{safe_label}"
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.target.base_url, timeout=20.0, follow_redirects=True)

    async def _login(self, client: httpx.AsyncClient) -> None:
        csrf_token = await self._csrf_token(client)
        client.headers["X-Requested-With"] = "XMLHttpRequest"
        if csrf_token:
            client.headers["X-CSRF-Token"] = csrf_token

        response = await client.post(
            "/login",
            data={
                "username": self.target.username,
                "password": self.target.password,
                "twoFactorCode": "",
            },
        )
        self._raise_for_x3ui(response)

    @staticmethod
    async def _csrf_token(client: httpx.AsyncClient) -> str | None:
        response = await client.get("/csrf-token", headers={"X-Requested-With": "XMLHttpRequest"})
        if response.status_code >= 400:
            return None

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return None

        payload = response.json()
        token = payload.get("obj") if isinstance(payload, dict) and payload.get("success") is True else None
        return token if isinstance(token, str) and token else None

    async def _client_uuid_by_email(self, client: httpx.AsyncClient, email: str) -> str | None:
        response = await client.get(f"/panel/api/clients/get/{quote(email, safe='')}")
        if response.status_code == 404:
            return None

        self._raise_for_x3ui(response)
        payload = self._payload_data(response)
        if isinstance(payload, dict):
            client_payload = payload.get("client") if isinstance(payload.get("client"), dict) else payload
            uuid = client_payload.get("uuid")
            if isinstance(uuid, str) and uuid:
                return uuid
        return None

    def _target_from_node(self, node: VpnNode | None) -> X3UITarget:
        if node is not None:
            return X3UITarget(
                mode=node.mode,
                base_url=node.base_url,
                username=node.username,
                password=node.password,
                inbound_id=node.inbound_id,
                public_host=node.public_host,
                public_port=node.public_port,
                vless_query=node.vless_query,
            )

        return X3UITarget(
            mode=self.settings.x3ui_mode,
            base_url=self.settings.x3ui_base_url,
            username=self.settings.x3ui_username,
            password=self.settings.x3ui_password,
            inbound_id=self.settings.x3ui_inbound_id,
            public_host=self.settings.vless_public_host,
            public_port=self.settings.vless_public_port,
            vless_query=self.settings.vless_query,
        )

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

    @staticmethod
    def _payload_data(response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return {}
        payload = response.json()
        if isinstance(payload, dict) and "obj" in payload:
            return payload["obj"]
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    @classmethod
    def _snapshot_from_payload(cls, status_data: Any, inbound_data: Any, latency_ms: int) -> NodeSnapshot:
        inbounds = inbound_data if isinstance(inbound_data, list) else []
        remote_clients = 0
        remote_enabled_clients = 0
        traffic_up = 0
        traffic_down = 0

        for inbound in inbounds:
            if not isinstance(inbound, dict):
                continue
            traffic_up += cls._int_value(inbound.get("up"))
            traffic_down += cls._int_value(inbound.get("down"))
            settings = cls._json_value(inbound.get("settings"))
            clients = settings.get("clients") if isinstance(settings, dict) else None
            if isinstance(clients, list):
                remote_clients += len(clients)
                remote_enabled_clients += sum(
                    1 for client in clients if not isinstance(client, dict) or client.get("enable", True)
                )
            client_stats = inbound.get("clientStats")
            if isinstance(client_stats, list):
                remote_clients = max(remote_clients, len(client_stats))
                traffic_up += sum(
                    cls._int_value(client.get("up")) for client in client_stats if isinstance(client, dict)
                )
                traffic_down += sum(
                    cls._int_value(client.get("down")) for client in client_stats if isinstance(client, dict)
                )

        return NodeSnapshot(
            status="online",
            latency_ms=latency_ms,
            remote_clients=remote_clients,
            remote_enabled_clients=remote_enabled_clients,
            traffic_up_bytes=traffic_up,
            traffic_down_bytes=traffic_down,
            cpu_percent=cls._percent_from_status(status_data, "cpu"),
            memory_percent=cls._percent_from_status(status_data, "mem"),
            disk_percent=cls._percent_from_status(status_data, "disk"),
        )

    @staticmethod
    def _json_value(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _int_value(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _percent_from_status(cls, status_data: Any, key: str) -> int | None:
        if not isinstance(status_data, dict):
            return None
        candidates = {
            "cpu": ["cpu", "cpuPercent", "cpuUsage", "cpu_usage"],
            "mem": ["mem", "memory", "memPercent", "memoryPercent", "mem_usage"],
            "disk": ["disk", "diskPercent", "diskUsage", "disk_usage"],
        }[key]
        for candidate in candidates:
            value = status_data.get(candidate)
            percent = cls._extract_percent(value)
            if percent is not None:
                return percent
        return None

    @classmethod
    def _extract_percent(cls, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            numeric = float(value)
            if 0 <= numeric <= 1:
                numeric *= 100
            return max(0, min(100, int(numeric)))
        if isinstance(value, dict):
            used = value.get("used", value.get("current"))
            total = value.get("total")
            if used is not None and total:
                return max(0, min(100, int((float(used) / float(total)) * 100)))
            for key in ("percent", "usage", "usedPercent"):
                if key in value:
                    return cls._extract_percent(value[key])
        return None
