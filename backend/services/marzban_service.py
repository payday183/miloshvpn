from __future__ import annotations

import json
from math import ceil
from typing import Any

import httpx

from backend.models.orm import (
    MarzbanServerMetricORM,
    MarzbanServerORM,
    MarzbanServerSnapshotORM,
)
from backend.repositories.marzban_repo import MarzbanServerRepository
from shared.utils import utc_now


class MarzbanServerNotFoundError(Exception):
    pass


class MarzbanServerValidationError(Exception):
    pass


class NoMarzbanServersAvailableError(Exception):
    pass


class MarzbanService:
    def __init__(
        self,
        repo: MarzbanServerRepository,
        *,
        request_timeout: float = 5.0,
    ) -> None:
        self.repo = repo
        self.request_timeout = request_timeout

    def list_servers(self, enabled_only: bool = False) -> list[MarzbanServerORM]:
        return self.repo.list_servers(enabled_only=enabled_only)

    def list_servers_with_metrics(self, enabled_only: bool = False) -> list[dict[str, object]]:
        metrics = {metric.server_id: metric for metric in self.repo.list_metrics()}
        return [
            self.server_to_dict(server, metrics.get(server.id))
            for server in self.repo.list_servers(enabled_only=enabled_only)
        ]

    def get_server(self, server_id: int) -> MarzbanServerORM:
        server = self.repo.get_server(server_id)
        if server is None:
            raise MarzbanServerNotFoundError(f"Marzban server {server_id} was not found.")
        return server

    def get_server_by_name(self, name: str) -> MarzbanServerORM:
        server = self.repo.get_server_by_name(name)
        if server is None:
            raise MarzbanServerNotFoundError(f"Marzban server '{name}' was not found.")
        return server

    def create_server(self, payload: dict[str, Any]) -> MarzbanServerORM:
        name = self._clean_required(payload.get("name"), "Server name is required.")
        api_base_url = self._normalize_url(
            payload.get("api_base_url"),
            "Marzban API URL is required.",
        )
        if self.repo.get_server_by_name(name) is not None:
            raise MarzbanServerValidationError(f"Server '{name}' already exists.")

        server = self.repo.create_server(
            name=name,
            location=self._clean_optional(payload.get("location")),
            api_base_url=api_base_url,
            dashboard_url=self._normalize_url(payload.get("dashboard_url"), allow_empty=True),
            username=self._clean_optional(payload.get("username")),
            password=self._clean_optional(payload.get("password")),
            access_token=self._clean_optional(payload.get("access_token")),
            inbound_tags=self._normalize_inbound_tags(payload.get("inbound_tags")),
            is_enabled=self._coerce_bool(payload.get("is_enabled"), default=True),
            allow_new_users=self._coerce_bool(payload.get("allow_new_users"), default=True),
            weight=self._coerce_int(payload.get("weight"), default=100),
            max_users=self._coerce_optional_int(payload.get("max_users")),
            max_traffic_bytes=self._coerce_optional_int(payload.get("max_traffic_bytes")),
            status="unknown",
        )
        self._ensure_metric(server.id)
        return server

    def update_server(self, server_id: int, payload: dict[str, Any]) -> MarzbanServerORM:
        server = self.get_server(server_id)

        if "name" in payload:
            name = self._clean_required(payload.get("name"), "Server name is required.")
            existing = self.repo.get_server_by_name(name)
            if existing is not None and existing.id != server.id:
                raise MarzbanServerValidationError(f"Server '{name}' already exists.")
            server.name = name
        if "location" in payload:
            server.location = self._clean_optional(payload.get("location"))
        if "api_base_url" in payload:
            server.api_base_url = self._normalize_url(
                payload.get("api_base_url"),
                "Marzban API URL is required.",
            )
        if "dashboard_url" in payload:
            server.dashboard_url = self._normalize_url(payload.get("dashboard_url"), allow_empty=True)
        if "username" in payload:
            server.username = self._clean_optional(payload.get("username"))
        if "password" in payload:
            server.password = self._clean_optional(payload.get("password"))
        if "access_token" in payload:
            server.access_token = self._clean_optional(payload.get("access_token"))
        if "inbound_tags" in payload:
            server.inbound_tags = self._normalize_inbound_tags(payload.get("inbound_tags"))
        if "is_enabled" in payload:
            server.is_enabled = self._coerce_bool(payload.get("is_enabled"), default=server.is_enabled)
        if "allow_new_users" in payload:
            server.allow_new_users = self._coerce_bool(
                payload.get("allow_new_users"),
                default=server.allow_new_users,
            )
        if "weight" in payload:
            server.weight = self._coerce_int(payload.get("weight"), default=server.weight or 100)
        if "max_users" in payload:
            server.max_users = self._coerce_optional_int(payload.get("max_users"))
        if "max_traffic_bytes" in payload:
            server.max_traffic_bytes = self._coerce_optional_int(payload.get("max_traffic_bytes"))

        return self.repo.save_server(server)

    def get_server_metrics(self, server_id: int, *, limit: int = 24) -> dict[str, object]:
        server = self.get_server(server_id)
        metric = self._ensure_metric(server.id)
        snapshots = self.repo.list_snapshots(server.id, limit=limit)
        return {
            "server": self.server_to_dict(server, metric),
            "metric": self.metric_to_dict(metric),
            "snapshots": [self.snapshot_to_dict(snapshot) for snapshot in snapshots],
        }

    def check_server_connection(self, server_id: int) -> dict[str, object]:
        server = self.get_server(server_id)
        metric = self._ensure_metric(server.id)
        now = utc_now()
        reachable = False
        detail = "Unknown check result."
        try:
            with httpx.Client(timeout=self.request_timeout, follow_redirects=True) as client:
                response = client.get(server.api_base_url)
            reachable = response.status_code < 500
            if reachable:
                if self._has_auth(server):
                    server.status = "healthy"
                    detail = (
                        f"Server reachable, HTTP {response.status_code}. "
                        "Credentials are stored for the next provisioning step."
                    )
                else:
                    server.status = "degraded"
                    detail = (
                        f"Server reachable, HTTP {response.status_code}, "
                        "but Marzban credentials are not configured yet."
                    )
                server.last_error = None
            else:
                server.status = "degraded"
                detail = f"Server responded with HTTP {response.status_code}."
                server.last_error = detail
        except httpx.HTTPError as error:
            server.status = "offline"
            detail = f"Server is not reachable: {error}"
            server.last_error = detail

        server.last_checked_at = now
        server = self.repo.save_server(server)

        metric.collected_at = now
        metric.load_score = self._calculate_load_score(server, metric)
        metric.is_overloaded = self._is_overloaded(server, metric)
        metric = self.repo.save_metric(metric)
        self.repo.create_snapshot(
            server_id=server.id,
            active_users=metric.active_users,
            total_traffic_bytes=metric.total_traffic_bytes,
            traffic_24h_bytes=metric.traffic_24h_bytes,
            cpu_percent=metric.cpu_percent,
            memory_percent=metric.memory_percent,
            load_score=metric.load_score,
            is_overloaded=metric.is_overloaded,
            collected_at=now,
        )

        return {
            "server": self.server_to_dict(server, metric),
            "check": {
                "reachable": reachable,
                "detail": detail,
                "checked_at": now.isoformat(),
            },
        }

    def select_server_for_user(self, user_id: int) -> tuple[MarzbanServerORM, MarzbanServerMetricORM, dict[str, object]]:
        active_assignment = self.repo.get_active_assignment(user_id)
        if active_assignment is not None:
            assigned_server = self.repo.get_server(active_assignment.server_id)
            if assigned_server is not None and assigned_server.is_enabled:
                metric = self._ensure_metric(assigned_server.id)
                decision = {
                    "reason": "reuse_existing_assignment",
                    "score": self._calculate_server_score(assigned_server, metric),
                }
                return assigned_server, metric, decision

        candidates: list[tuple[float, MarzbanServerORM, MarzbanServerMetricORM, dict[str, object]]] = []
        for server in self.repo.list_servers(enabled_only=True):
            if not server.allow_new_users:
                continue
            metric = self._ensure_metric(server.id)
            status = (server.status or "unknown").lower()
            if status == "offline":
                continue

            users_ratio = self._ratio(metric.active_users, server.max_users)
            traffic_ratio = self._ratio(metric.traffic_24h_bytes, server.max_traffic_bytes)
            overloaded = self._is_overloaded(server, metric)
            if overloaded:
                continue

            score = self._calculate_server_score(server, metric)
            decision = {
                "status": status,
                "weight": server.weight,
                "active_users": metric.active_users,
                "traffic_24h_bytes": metric.traffic_24h_bytes,
                "users_ratio": users_ratio,
                "traffic_ratio": traffic_ratio,
                "score": score,
            }
            candidates.append((score, server, metric, decision))

        if not candidates:
            raise NoMarzbanServersAvailableError("No enabled Marzban server is available for new users.")

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, server, metric, decision = candidates[0]
        now = utc_now()
        self.repo.deactivate_assignments(user_id, released_at=now)
        self.repo.create_assignment(
            user_id=user_id,
            server_id=server.id,
            reason="auto_balance",
            decision_payload=json.dumps(decision),
            score=score,
            assigned_at=now,
        )
        return server, metric, decision

    def server_to_dict(
        self,
        server: MarzbanServerORM,
        metric: MarzbanServerMetricORM | None = None,
    ) -> dict[str, object]:
        resolved_metric = metric or self.repo.get_metric(server.id)
        return {
            "id": server.id,
            "name": server.name,
            "location": server.location,
            "api_base_url": server.api_base_url,
            "dashboard_url": server.dashboard_url,
            "username": server.username,
            "password": server.password,
            "access_token": server.access_token,
            "inbound_tags": server.inbound_tags or "",
            "is_enabled": server.is_enabled,
            "allow_new_users": server.allow_new_users,
            "weight": server.weight,
            "max_users": server.max_users,
            "max_traffic_bytes": server.max_traffic_bytes,
            "status": server.status,
            "last_checked_at": self._datetime_to_str(server.last_checked_at),
            "last_error": server.last_error,
            "metrics": self.metric_to_dict(resolved_metric) if resolved_metric is not None else None,
        }

    def metric_to_dict(self, metric: MarzbanServerMetricORM) -> dict[str, object]:
        return {
            "server_id": metric.server_id,
            "active_users": metric.active_users,
            "active_subscriptions": metric.active_subscriptions,
            "total_traffic_bytes": metric.total_traffic_bytes,
            "traffic_24h_bytes": metric.traffic_24h_bytes,
            "cpu_percent": metric.cpu_percent,
            "memory_percent": metric.memory_percent,
            "network_rx_bytes": metric.network_rx_bytes,
            "network_tx_bytes": metric.network_tx_bytes,
            "load_score": metric.load_score,
            "is_overloaded": metric.is_overloaded,
            "collected_at": self._datetime_to_str(metric.collected_at),
        }

    def snapshot_to_dict(self, snapshot: MarzbanServerSnapshotORM) -> dict[str, object]:
        return {
            "id": snapshot.id,
            "server_id": snapshot.server_id,
            "active_users": snapshot.active_users,
            "total_traffic_bytes": snapshot.total_traffic_bytes,
            "traffic_24h_bytes": snapshot.traffic_24h_bytes,
            "cpu_percent": snapshot.cpu_percent,
            "memory_percent": snapshot.memory_percent,
            "load_score": snapshot.load_score,
            "is_overloaded": snapshot.is_overloaded,
            "collected_at": self._datetime_to_str(snapshot.collected_at),
        }

    def _ensure_metric(self, server_id: int) -> MarzbanServerMetricORM:
        metric = self.repo.get_metric(server_id)
        if metric is not None:
            return metric
        return self.repo.create_metric(server_id=server_id, collected_at=utc_now())

    def _calculate_server_score(
        self,
        server: MarzbanServerORM,
        metric: MarzbanServerMetricORM,
    ) -> float:
        score = float(server.weight or 100)
        score -= float(metric.active_users or 0) * 1.5
        score -= self._ratio(metric.active_users, server.max_users) * 40
        score -= self._ratio(metric.traffic_24h_bytes, server.max_traffic_bytes) * 45
        score -= float(metric.cpu_percent or 0.0) * 0.25
        score -= float(metric.memory_percent or 0.0) * 0.2
        if (server.status or "unknown").lower() == "degraded":
            score -= 15
        return round(score, 4)

    def _calculate_load_score(
        self,
        server: MarzbanServerORM,
        metric: MarzbanServerMetricORM,
    ) -> float:
        value = (
            self._ratio(metric.active_users, server.max_users) * 50
            + self._ratio(metric.traffic_24h_bytes, server.max_traffic_bytes) * 35
            + float(metric.cpu_percent or 0.0) * 0.1
            + float(metric.memory_percent or 0.0) * 0.05
            + float(metric.active_users or 0) * 0.15
        )
        return round(value, 4)

    def _is_overloaded(
        self,
        server: MarzbanServerORM,
        metric: MarzbanServerMetricORM,
    ) -> bool:
        if server.max_users and metric.active_users >= server.max_users:
            return True
        if server.max_traffic_bytes and metric.traffic_24h_bytes >= server.max_traffic_bytes:
            return True
        if metric.cpu_percent is not None and metric.cpu_percent >= 95:
            return True
        if metric.memory_percent is not None and metric.memory_percent >= 95:
            return True
        return False

    def _has_auth(self, server: MarzbanServerORM) -> bool:
        return bool(server.access_token or (server.username and server.password))

    def _ratio(self, value: int | None, limit: int | None) -> float:
        if value is None or limit is None or limit <= 0:
            return 0.0
        return min(max(value / limit, 0.0), 10.0)

    def _clean_required(self, value: object, error: str) -> str:
        resolved = self._clean_optional(value)
        if not resolved:
            raise MarzbanServerValidationError(error)
        return resolved

    def _clean_optional(self, value: object) -> str | None:
        if value is None:
            return None
        resolved = str(value).strip()
        return resolved or None

    def _normalize_url(self, value: object, error: str | None = None, *, allow_empty: bool = False) -> str | None:
        resolved = self._clean_optional(value)
        if resolved is None:
            if allow_empty:
                return None
            raise MarzbanServerValidationError(error or "URL is required.")
        if not resolved.startswith(("http://", "https://")):
            raise MarzbanServerValidationError("Server URL must start with http:// or https://")
        return resolved.rstrip("/")

    def _normalize_inbound_tags(self, value: object) -> str | None:
        raw = self._clean_optional(value)
        if raw is None:
            return None
        items = [
            item.strip()
            for chunk in raw.replace("\n", ",").split(",")
            for item in [chunk]
            if item.strip()
        ]
        return ", ".join(items) if items else None

    def _coerce_bool(self, value: object, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    def _coerce_int(self, value: object, *, default: int) -> int:
        if value in (None, ""):
            return default
        return int(str(value))

    def _coerce_optional_int(self, value: object) -> int | None:
        if value in (None, "", "null"):
            return None
        return int(str(value))

    def _datetime_to_str(self, value) -> str | None:
        if value is None:
            return None
        return value.isoformat()
