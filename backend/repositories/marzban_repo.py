from __future__ import annotations

from datetime import datetime

from backend.models.orm import (
    MarzbanServerMetricORM,
    MarzbanServerORM,
    MarzbanServerSnapshotORM,
    UserServerAssignmentORM,
)
from shared.postgres import SessionLocal


class MarzbanServerRepository:
    def _get_db(self):
        return SessionLocal()

    def list_servers(self, enabled_only: bool = False) -> list[MarzbanServerORM]:
        db = self._get_db()
        try:
            query = db.query(MarzbanServerORM)
            if enabled_only:
                query = query.filter_by(is_enabled=True)
            return query.order_by(MarzbanServerORM.id.asc()).all()
        finally:
            db.close()

    def get_server(self, server_id: int) -> MarzbanServerORM | None:
        db = self._get_db()
        try:
            return db.query(MarzbanServerORM).filter_by(id=server_id).first()
        finally:
            db.close()

    def get_server_by_name(self, name: str) -> MarzbanServerORM | None:
        db = self._get_db()
        try:
            return db.query(MarzbanServerORM).filter_by(name=name).first()
        finally:
            db.close()

    def save_server(self, server: MarzbanServerORM) -> MarzbanServerORM:
        db = self._get_db()
        try:
            merged = db.merge(server)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()

    def create_server(
        self,
        *,
        name: str,
        location: str | None,
        api_base_url: str,
        dashboard_url: str | None,
        username: str | None,
        password: str | None,
        access_token: str | None,
        inbound_tags: str | None,
        is_enabled: bool,
        allow_new_users: bool,
        weight: int,
        max_users: int | None,
        max_traffic_bytes: int | None,
        status: str = "unknown",
    ) -> MarzbanServerORM:
        db = self._get_db()
        try:
            server = MarzbanServerORM(
                name=name,
                location=location,
                api_base_url=api_base_url,
                dashboard_url=dashboard_url,
                username=username,
                password=password,
                access_token=access_token,
                inbound_tags=inbound_tags,
                is_enabled=is_enabled,
                allow_new_users=allow_new_users,
                weight=weight,
                max_users=max_users,
                max_traffic_bytes=max_traffic_bytes,
                status=status,
            )
            db.add(server)
            db.commit()
            db.refresh(server)
            return server
        finally:
            db.close()

    def get_metric(self, server_id: int) -> MarzbanServerMetricORM | None:
        db = self._get_db()
        try:
            return db.query(MarzbanServerMetricORM).filter_by(server_id=server_id).first()
        finally:
            db.close()

    def list_metrics(self) -> list[MarzbanServerMetricORM]:
        db = self._get_db()
        try:
            return db.query(MarzbanServerMetricORM).order_by(MarzbanServerMetricORM.server_id.asc()).all()
        finally:
            db.close()

    def create_metric(
        self,
        *,
        server_id: int,
        active_users: int = 0,
        active_subscriptions: int = 0,
        total_traffic_bytes: int = 0,
        traffic_24h_bytes: int = 0,
        cpu_percent: float | None = None,
        memory_percent: float | None = None,
        network_rx_bytes: int = 0,
        network_tx_bytes: int = 0,
        load_score: float | None = None,
        is_overloaded: bool = False,
        collected_at: datetime | None = None,
    ) -> MarzbanServerMetricORM:
        db = self._get_db()
        try:
            metric = MarzbanServerMetricORM(
                server_id=server_id,
                active_users=active_users,
                active_subscriptions=active_subscriptions,
                total_traffic_bytes=total_traffic_bytes,
                traffic_24h_bytes=traffic_24h_bytes,
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                network_rx_bytes=network_rx_bytes,
                network_tx_bytes=network_tx_bytes,
                load_score=load_score,
                is_overloaded=is_overloaded,
                collected_at=collected_at,
            )
            db.add(metric)
            db.commit()
            db.refresh(metric)
            return metric
        finally:
            db.close()

    def save_metric(self, metric: MarzbanServerMetricORM) -> MarzbanServerMetricORM:
        db = self._get_db()
        try:
            merged = db.merge(metric)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()

    def create_snapshot(
        self,
        *,
        server_id: int,
        active_users: int = 0,
        total_traffic_bytes: int = 0,
        traffic_24h_bytes: int = 0,
        cpu_percent: float | None = None,
        memory_percent: float | None = None,
        load_score: float | None = None,
        is_overloaded: bool = False,
        collected_at: datetime | None = None,
    ) -> MarzbanServerSnapshotORM:
        db = self._get_db()
        try:
            snapshot = MarzbanServerSnapshotORM(
                server_id=server_id,
                active_users=active_users,
                total_traffic_bytes=total_traffic_bytes,
                traffic_24h_bytes=traffic_24h_bytes,
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                load_score=load_score,
                is_overloaded=is_overloaded,
                collected_at=collected_at,
            )
            db.add(snapshot)
            db.commit()
            db.refresh(snapshot)
            return snapshot
        finally:
            db.close()

    def list_snapshots(
        self,
        server_id: int,
        *,
        limit: int = 24,
    ) -> list[MarzbanServerSnapshotORM]:
        db = self._get_db()
        try:
            return (
                db.query(MarzbanServerSnapshotORM)
                .filter_by(server_id=server_id)
                .order_by(MarzbanServerSnapshotORM.collected_at.desc(), MarzbanServerSnapshotORM.id.desc())
                .limit(limit)
                .all()
            )
        finally:
            db.close()

    def get_active_assignment(self, user_id: int) -> UserServerAssignmentORM | None:
        db = self._get_db()
        try:
            return (
                db.query(UserServerAssignmentORM)
                .filter_by(user_id=user_id, is_active=True)
                .order_by(UserServerAssignmentORM.assigned_at.desc(), UserServerAssignmentORM.id.desc())
                .first()
            )
        finally:
            db.close()

    def deactivate_assignments(self, user_id: int, released_at: datetime) -> None:
        db = self._get_db()
        try:
            assignments = (
                db.query(UserServerAssignmentORM)
                .filter_by(user_id=user_id, is_active=True)
                .all()
            )
            for assignment in assignments:
                assignment.is_active = False
                assignment.released_at = released_at
            db.commit()
        finally:
            db.close()

    def create_assignment(
        self,
        *,
        user_id: int,
        server_id: int,
        reason: str | None,
        decision_payload: str | None,
        score: float | None,
        assigned_at: datetime,
    ) -> UserServerAssignmentORM:
        db = self._get_db()
        try:
            assignment = UserServerAssignmentORM(
                user_id=user_id,
                server_id=server_id,
                reason=reason,
                decision_payload=decision_payload,
                score=score,
                is_active=True,
                assigned_at=assigned_at,
            )
            db.add(assignment)
            db.commit()
            db.refresh(assignment)
            return assignment
        finally:
            db.close()
