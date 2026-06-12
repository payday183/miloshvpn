import base64
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import time
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
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
    public_key: str = ""


class X3UIError(RuntimeError):
    pass


CLIENT_IP_LIMIT = 1
DEFAULT_REALITY_FLOW = "xtls-rprx-vision"
X25519_FIELD_SIZE = 2**255 - 19
X25519_A24 = 121665


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

        if self.target.mode == "mock":
            vless_uri = self.build_vless_uri(client_uuid=client_uuid, label=email)
            return ProvisionedClient(client_uuid=client_uuid, email=email, vless_uri=vless_uri)

        expiry_ms = int(expires_at.timestamp() * 1000) if expires_at else 0
        total_gb = int(Decimal(traffic_gb or 0) * Decimal(1024**3))

        async with self._client() as client:
            await self._login(client)
            inbound = await self._get_inbound(client, self.target.inbound_id)
            flow = self._client_flow_for_inbound(inbound)
            sub_id = client_uuid.replace("-", "")[:16]
            client_settings = {
                "id": client_uuid,
                "email": email,
                "enable": True,
                "expiryTime": expiry_ms,
                "totalGB": total_gb,
                "tgId": str(telegram_id or ""),
                "limitIp": CLIENT_IP_LIMIT,
                "subId": sub_id,
            }
            api_client = {
                "email": email,
                "id": client_uuid,
                "uuid": client_uuid,
                "subId": sub_id,
                "totalGB": total_gb,
                "expiryTime": expiry_ms,
                "tgId": telegram_id or 0,
                "limitIp": CLIENT_IP_LIMIT,
                "enable": True,
            }
            if flow:
                client_settings["flow"] = flow
                api_client["flow"] = flow

            legacy_payload = {
                "id": self.target.inbound_id,
                "settings": json.dumps({"clients": [client_settings]}),
            }

            response = await client.post("/panel/api/inbounds/addClient", json=legacy_payload)
            if response.status_code == 404:
                response = await client.post(
                    "/panel/api/clients/add",
                    json={
                        "client": api_client,
                        "inboundIds": [self.target.inbound_id],
                    },
                )
            self._raise_for_x3ui(response)

            inbound = await self._get_inbound(client, self.target.inbound_id)
            created_client = self._find_inbound_client(inbound, email=email, client_uuid=client_uuid)
            if created_client is None:
                fetched_uuid = await self._client_uuid_by_email(client, email)
                if fetched_uuid:
                    client_uuid = fetched_uuid
                    created_client = self._find_inbound_client(inbound, email=email, client_uuid=client_uuid)

            if created_client is None:
                raise X3UIError(
                    f"3x-ui did not add client {email!r} to inbound {self.target.inbound_id}; key was not issued"
                )

            client_uuid = self._client_uuid_from_settings(created_client) or client_uuid
            if self._inbound_security(inbound) == "reality":
                vless_uri = await self._build_reality_vless_uri(
                    client,
                    inbound=inbound,
                    client_settings=created_client,
                    client_uuid=client_uuid,
                    label=email,
                )
                self._validate_reality_vless_uri(
                    vless_uri,
                    inbound=inbound,
                    client_settings=created_client,
                    client_uuid=client_uuid,
                )
            else:
                vless_uri = self.build_vless_uri_from_inbound(
                    inbound=inbound,
                    client_settings=created_client,
                    client_uuid=client_uuid,
                    label=email,
                )
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
        query = self.target.vless_query.strip().lstrip("?")
        query_part = f"?{query}" if query else ""
        return (
            f"vless://{client_uuid}@{self.target.public_host}:{self.target.public_port}"
            f"{query_part}#{safe_label}"
        )

    def build_vless_uri_from_inbound(
        self,
        *,
        inbound: dict[str, Any],
        client_settings: dict[str, Any],
        client_uuid: str,
        label: str,
    ) -> str:
        safe_label = label.replace(" ", "_")
        port = self._int_value(inbound.get("port")) or self.target.public_port
        query = urlencode(self._vless_query_items_from_inbound(inbound, client_settings))
        return f"vless://{client_uuid}@{self.target.public_host}:{port}?{query}#{safe_label}"

    async def _build_reality_vless_uri(
        self,
        client: httpx.AsyncClient,
        *,
        inbound: dict[str, Any],
        client_settings: dict[str, Any],
        client_uuid: str,
        label: str,
    ) -> str:
        panel_link = await self._panel_vless_link(client, email=label)
        public_key = self._reality_public_key(inbound)
        if panel_link:
            return self._rewrite_reality_vless_link(
                panel_link,
                public_host=self.target.public_host,
                public_port=self.target.public_port,
                public_key=public_key,
                flow=self._string_value(client_settings.get("flow")) or self._vless_flow(),
                client_uuid=client_uuid,
            )

        return self.build_vless_uri_from_inbound(
            inbound=inbound,
            client_settings=client_settings,
            client_uuid=client_uuid,
            label=label,
        )

    def _vless_flow(self) -> str | None:
        query = self.target.vless_query.lstrip("?")
        for key, value in parse_qsl(query, keep_blank_values=False):
            if key == "flow" and value:
                return value
        return None

    async def _panel_vless_link(self, client: httpx.AsyncClient, *, email: str) -> str | None:
        response = await client.get(f"/panel/api/clients/links/{quote(email, safe='')}")
        if response.status_code == 404:
            return None

        self._raise_for_x3ui(response)
        payload = self._payload_data(response)
        links = self._string_list(payload)
        for link in links:
            if isinstance(link, str) and link.startswith("vless://"):
                return link
        return None

    @classmethod
    def _string_list(cls, payload: Any) -> list[str]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, str) and item]
        if isinstance(payload, dict):
            for key in ("links", "obj", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, str) and item]
        return []

    def _reality_public_key(self, inbound: dict[str, Any]) -> str:
        stream_settings = self._json_value(inbound.get("streamSettings"))
        reality_settings = self._json_value(stream_settings.get("realitySettings"))
        return self._resolve_reality_public_key(reality_settings)

    def _rewrite_reality_vless_link(
        self,
        link: str,
        *,
        public_host: str,
        public_port: int,
        public_key: str,
        flow: str | None,
        client_uuid: str,
    ) -> str:
        parsed = urlsplit(link)
        if parsed.scheme != "vless":
            raise X3UIError("3x-ui client link is not a VLESS link")

        query_items = list(parse_qsl(parsed.query, keep_blank_values=True))
        rewritten: list[tuple[str, str]] = []
        pbk_replaced = False
        flow_present = False
        encryption_present = False

        for key, value in query_items:
            if key == "pbk":
                rewritten.append((key, public_key))
                pbk_replaced = True
                continue
            if key == "encryption":
                rewritten.append((key, "none"))
                encryption_present = True
                continue
            if key == "flow" and flow:
                rewritten.append((key, flow))
                flow_present = True
                continue
            if key == "flow":
                flow_present = True
            rewritten.append((key, value))

        if not pbk_replaced:
            rewritten.append(("pbk", public_key))
        if not encryption_present:
            rewritten.append(("encryption", "none"))
        if flow and not flow_present:
            rewritten.append(("flow", flow))

        query = urlencode(rewritten, quote_via=quote)
        fragment = parsed.fragment
        return f"vless://{client_uuid}@{public_host}:{public_port}?{query}#{fragment}"

    def _validate_reality_vless_uri(
        self,
        link: str,
        *,
        inbound: dict[str, Any],
        client_settings: dict[str, Any],
        client_uuid: str,
    ) -> None:
        parsed = urlsplit(link)
        if parsed.scheme != "vless":
            raise X3UIError("Broken Reality link: not a VLESS URI")
        if parsed.username != client_uuid:
            raise X3UIError("Broken Reality link: unexpected UUID")
        if parsed.hostname != self.target.public_host:
            raise X3UIError("Broken Reality link: unexpected host")
        if parsed.port != self.target.public_port:
            raise X3UIError("Broken Reality link: unexpected port")

        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        expected_flow = self._string_value(client_settings.get("flow")) or self._vless_flow() or DEFAULT_REALITY_FLOW
        self._validate_reality_flow(expected_flow)
        stream_settings = self._json_value(inbound.get("streamSettings"))
        reality_settings = self._json_value(stream_settings.get("realitySettings"))
        expected_public_key = self._resolve_reality_public_key(reality_settings)
        expected_sni = (
            self._first_string(reality_settings.get("serverNames"))
            or self._string_value(self._json_value(reality_settings.get("settings")).get("serverName"))
        )
        expected_short_id = self._first_string(reality_settings.get("shortIds"), allow_blank=True)

        checks = {
            "type": "tcp",
            "security": "reality",
            "encryption": "none",
            "pbk": expected_public_key,
            "flow": expected_flow,
            "sni": expected_sni,
            "sid": expected_short_id,
        }
        for name, expected in checks.items():
            if expected is None:
                continue
            actual = query.get(name)
            if actual != expected:
                raise X3UIError(f"Broken Reality link: unexpected {name}")

        if not query.get("pbk"):
            raise X3UIError("Broken Reality link: empty pbk")

    async def _get_inbound(self, client: httpx.AsyncClient, inbound_id: int) -> dict[str, Any]:
        response = await client.get(f"/panel/api/inbounds/get/{inbound_id}")
        if response.status_code != 404:
            self._raise_for_x3ui(response)
            payload = self._payload_data(response)
            if isinstance(payload, dict):
                return payload
            raise X3UIError(f"3x-ui inbound {inbound_id} response has unexpected shape")

        response = await client.get("/panel/api/inbounds/list")
        self._raise_for_x3ui(response)
        payload = self._payload_data(response)
        inbounds = self._inbound_list(payload)
        for inbound in inbounds:
            if self._int_value(inbound.get("id")) == inbound_id:
                return inbound
        raise X3UIError(f"3x-ui inbound {inbound_id} was not found")

    @classmethod
    def _inbound_list(cls, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("inbounds", "items", "list"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @classmethod
    def _find_inbound_client(
        cls,
        inbound: dict[str, Any],
        *,
        email: str,
        client_uuid: str,
    ) -> dict[str, Any] | None:
        clients = cls._inbound_clients(inbound)
        for candidate in clients:
            if cls._client_uuid_from_settings(candidate) == client_uuid:
                return candidate
        for candidate in clients:
            if cls._string_value(candidate.get("email")) == email:
                return candidate
        return None

    @classmethod
    def _inbound_clients(cls, inbound: dict[str, Any]) -> list[dict[str, Any]]:
        settings = cls._json_value(inbound.get("settings"))
        clients = settings.get("clients")
        if not isinstance(clients, list):
            return []
        return [client for client in clients if isinstance(client, dict)]

    @classmethod
    def _client_uuid_from_settings(cls, client_settings: dict[str, Any]) -> str | None:
        for key in ("id", "uuid"):
            value = cls._string_value(client_settings.get(key))
            if value:
                return value
        return None

    def _client_flow_for_inbound(self, inbound: dict[str, Any]) -> str | None:
        configured = self._vless_flow()
        if configured:
            if self._inbound_security(inbound) == "reality":
                self._validate_reality_flow(configured)
            return configured

        if self._inbound_security(inbound) == "reality":
            return DEFAULT_REALITY_FLOW
        return None

    @staticmethod
    def _validate_reality_flow(flow: str) -> None:
        if flow != DEFAULT_REALITY_FLOW:
            raise X3UIError("Broken Reality link: unexpected flow")

    @classmethod
    def _inbound_security(cls, inbound: dict[str, Any]) -> str:
        stream_settings = cls._json_value(inbound.get("streamSettings"))
        return (cls._string_value(stream_settings.get("security")) or "none").lower()

    def _vless_query_items_from_inbound(
        self,
        inbound: dict[str, Any],
        client_settings: dict[str, Any],
    ) -> list[tuple[str, str]]:
        protocol = (self._string_value(inbound.get("protocol")) or "vless").lower()
        if protocol != "vless":
            raise X3UIError(f"3x-ui inbound {self.target.inbound_id} is {protocol!r}, expected 'vless'")

        inbound_settings = self._json_value(inbound.get("settings"))
        stream_settings = self._json_value(inbound.get("streamSettings"))
        network = (self._string_value(stream_settings.get("network")) or self._target_query_param("type") or "tcp")
        security = (
            self._string_value(stream_settings.get("security"))
            or self._target_query_param("security")
            or "none"
        )
        items = [
            ("type", network),
            ("security", security),
            ("encryption", self._string_value(inbound_settings.get("decryption")) or "none"),
        ]
        items.extend(self._transport_query_items(network, stream_settings))

        security_lower = security.lower()
        if security_lower == "reality":
            items.extend(self._reality_query_items(stream_settings))
        elif security_lower == "tls":
            items.extend(self._tls_query_items(stream_settings))

        flow = self._string_value(client_settings.get("flow")) or self._vless_flow()
        if flow:
            items.append(("flow", flow))
        return items

    def _resolve_reality_public_key(self, reality_settings: dict[str, Any]) -> str:
        nested_settings = self._json_value(reality_settings.get("settings"))
        inbound_public_key = (
            self._string_value(nested_settings.get("publicKey"))
            or self._string_value(reality_settings.get("publicKey"))
            or self._x25519_public_key_from_private(
                self._string_value(reality_settings.get("privateKey"))
            )
        )
        configured_public_key = self._string_value(self.target.public_key)

        if inbound_public_key and configured_public_key and configured_public_key != inbound_public_key:
            raise X3UIError("ADMIN_REALITY_PUBLIC_KEY does not match current 3x-ui Reality inbound privateKey")

        public_key = inbound_public_key or configured_public_key
        if not public_key:
            raise X3UIError("REALITY publicKey is empty; refusing to send broken VLESS link")
        return public_key

    @classmethod
    def _transport_query_items(
        cls,
        network: str,
        stream_settings: dict[str, Any],
    ) -> list[tuple[str, str]]:
        network_lower = network.lower()
        if network_lower == "tcp":
            tcp_settings = cls._json_value(stream_settings.get("tcpSettings"))
            header = cls._json_value(tcp_settings.get("header"))
            header_type = cls._string_value(header.get("type"))
            if header_type and header_type != "none":
                return [("headerType", header_type)]
            return []

        if network_lower in {"ws", "httpupgrade", "splithttp"}:
            settings_key = {
                "ws": "wsSettings",
                "httpupgrade": "httpupgradeSettings",
                "splithttp": "splithttpSettings",
            }[network_lower]
            transport_settings = cls._json_value(stream_settings.get(settings_key))
            items = []
            path = cls._string_value(transport_settings.get("path"))
            host = cls._string_value(transport_settings.get("host"))
            if path:
                items.append(("path", path))
            if host:
                items.append(("host", host))
            return items

        if network_lower == "grpc":
            grpc_settings = cls._json_value(stream_settings.get("grpcSettings"))
            items = []
            service_name = cls._string_value(grpc_settings.get("serviceName"))
            authority = cls._string_value(grpc_settings.get("authority"))
            mode = cls._string_value(grpc_settings.get("multiMode"))
            if service_name:
                items.append(("serviceName", service_name))
            if authority:
                items.append(("authority", authority))
            if mode:
                items.append(("mode", mode))
            return items

        return []

    def _reality_query_items(self, stream_settings: dict[str, Any]) -> list[tuple[str, str]]:
        reality_settings = self._json_value(stream_settings.get("realitySettings"))
        client_settings = self._json_value(reality_settings.get("settings"))
        public_key = self._resolve_reality_public_key(reality_settings)
        server_name = (
            self._first_string(reality_settings.get("serverNames"))
            or self._string_value(client_settings.get("serverName"))
        )
        short_id = self._first_string(reality_settings.get("shortIds"), allow_blank=True)
        fingerprint = (
            self._string_value(client_settings.get("fingerprint"))
            or self._string_value(reality_settings.get("fingerprint"))
            or "chrome"
        )
        spider_x = (
            self._string_value(client_settings.get("spiderX"))
            or self._string_value(reality_settings.get("spiderX"))
            or "/"
        )

        missing = []
        if not server_name:
            missing.append("serverNames[0]")
        if short_id is None:
            missing.append("shortIds[0]")
        if missing:
            raise X3UIError(f"3x-ui Reality inbound is missing {', '.join(missing)}")

        return [
            ("pbk", public_key),
            ("fp", fingerprint),
            ("sni", server_name),
            ("sid", short_id),
            ("spx", spider_x),
        ]

    @classmethod
    def _tls_query_items(cls, stream_settings: dict[str, Any]) -> list[tuple[str, str]]:
        tls_settings = cls._json_value(stream_settings.get("tlsSettings"))
        items = []
        server_name = cls._string_value(tls_settings.get("serverName"))
        fingerprint = cls._string_value(tls_settings.get("fingerprint"))
        alpn = tls_settings.get("alpn")
        if server_name:
            items.append(("sni", server_name))
        if fingerprint:
            items.append(("fp", fingerprint))
        if isinstance(alpn, list):
            alpn_value = ",".join(str(item) for item in alpn if str(item).strip())
            if alpn_value:
                items.append(("alpn", alpn_value))
        return items

    def _target_query_param(self, name: str) -> str | None:
        query = self.target.vless_query.lstrip("?")
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key == name:
                return value
        return None

    @classmethod
    def _x25519_public_key_from_private(cls, private_key: str | None) -> str | None:
        if not private_key:
            return None
        try:
            scalar = cls._decode_xray_key(private_key)
        except ValueError as exc:
            raise X3UIError("3x-ui Reality inbound has an invalid privateKey") from exc
        public_key = cls._x25519(scalar, bytes([9]) + bytes(31))
        return cls._encode_xray_key(public_key)

    @staticmethod
    def _decode_xray_key(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
        if len(decoded) != 32:
            raise ValueError("X25519 keys must be 32 bytes")
        return decoded

    @staticmethod
    def _encode_xray_key(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _x25519(scalar: bytes, point: bytes) -> bytes:
        k = bytearray(scalar)
        k[0] &= 248
        k[31] &= 127
        k[31] |= 64
        scalar_int = int.from_bytes(k, "little")
        x_1 = int.from_bytes(point, "little")
        x_2, z_2 = 1, 0
        x_3, z_3 = x_1, 1
        swap = 0

        for bit_index in range(254, -1, -1):
            bit = (scalar_int >> bit_index) & 1
            swap ^= bit
            if swap:
                x_2, x_3 = x_3, x_2
                z_2, z_3 = z_3, z_2
            swap = bit

            a = (x_2 + z_2) % X25519_FIELD_SIZE
            aa = (a * a) % X25519_FIELD_SIZE
            b = (x_2 - z_2) % X25519_FIELD_SIZE
            bb = (b * b) % X25519_FIELD_SIZE
            e = (aa - bb) % X25519_FIELD_SIZE
            c = (x_3 + z_3) % X25519_FIELD_SIZE
            d = (x_3 - z_3) % X25519_FIELD_SIZE
            da = (d * a) % X25519_FIELD_SIZE
            cb = (c * b) % X25519_FIELD_SIZE
            x_3 = ((da + cb) ** 2) % X25519_FIELD_SIZE
            z_3 = (x_1 * ((da - cb) ** 2)) % X25519_FIELD_SIZE
            x_2 = (aa * bb) % X25519_FIELD_SIZE
            z_2 = (e * (aa + X25519_A24 * e)) % X25519_FIELD_SIZE

        if swap:
            x_2, x_3 = x_3, x_2
            z_2, z_3 = z_3, z_2

        result = (x_2 * pow(z_2, X25519_FIELD_SIZE - 2, X25519_FIELD_SIZE)) % X25519_FIELD_SIZE
        return result.to_bytes(32, "little")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.target.base_url,
            timeout=20.0,
            follow_redirects=True,
            verify=self.settings.x3ui_tls_verify,
        )

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
            uuid = client_payload.get("uuid") or client_payload.get("id")
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
                public_key="",
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
            public_key="",
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
    def _string_value(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value)

    @classmethod
    def _first_string(cls, value: Any, *, allow_blank: bool = False) -> str | None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    if item or allow_blank:
                        return item
                    continue
                if item is not None:
                    string = cls._string_value(item)
                    if string or allow_blank:
                        return string or ""
        if isinstance(value, str) and (value or allow_blank):
            return value
        return None

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
