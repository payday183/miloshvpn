from backend.models.orm import VPNAccessORM
from backend.services.marzban_service import (
    MarzbanService,
    NoMarzbanServersAvailableError,
)
from backend.services.subscription_service import SubscriptionService, NoActiveSubscriptionError
from shared.postgres import SessionLocal
from shared.utils import generate_secret, utc_now


class VPNAccessNotFoundError(Exception):
    pass


class VPNService:
    def __init__(
        self,
        subscription_service: SubscriptionService,
        settings,
        marzban_service: MarzbanService | None = None,
    ):
        self.subscription_service = subscription_service
        self.settings = settings
        self.marzban_service = marzban_service

    def _get_db(self):
        return SessionLocal()

    def _get_active_access_query(self, db, user_id: int):
        return (
            db.query(VPNAccessORM)
            .filter_by(user_id=user_id, revoked=False)
            .order_by(VPNAccessORM.id.desc())
        )

    def get_access(self, user_id: int):
        db = self._get_db()
        try:
            return self._get_active_access_query(db, user_id).first()
        finally:
            db.close()

    def provision_access(self, user_id: int, server_name=None, protocol=None):
        sub = self.subscription_service.require_active_subscription(user_id)
        db = self._get_db()
        try:
            existing = self._get_active_access_query(db, user_id).first()
            selected_server = self._resolve_server_for_access(user_id, server_name)
            if existing:
                existing.expires_at = sub.ends_at
                if selected_server is not None and existing.server_id is None:
                    existing.server_id = selected_server.id
                    existing.server_name = selected_server.name
                if not existing.subscription_url and existing.access_url:
                    existing.subscription_url = existing.access_url
                if not existing.last_synced_at:
                    existing.last_synced_at = utc_now()
                if not existing.sync_status:
                    existing.sync_status = "mocked"
                db.commit()
                db.refresh(existing)
                return existing

            credential_id = generate_secret("vpn")
            access_material = self._build_access_material(
                credential_id=credential_id,
                server_name=selected_server.name if selected_server is not None else (server_name or self.settings.default_vpn_server),
            )
            access = VPNAccessORM(
                user_id=user_id,
                server_id=selected_server.id if selected_server is not None else None,
                server_name=selected_server.name if selected_server is not None else (server_name or self.settings.default_vpn_server),
                protocol=protocol or self.settings.default_vpn_protocol,
                credential_id=credential_id,
                access_url=access_material["access_url"],
                subscription_url=access_material["subscription_url"],
                config_blob="mock",
                expires_at=sub.ends_at,
                last_synced_at=utc_now(),
                sync_status=access_material["sync_status"],
                provisioning_error=access_material["provisioning_error"],
            )

            db.add(access)
            db.commit()
            db.refresh(access)
            return access
        finally:
            db.close()

    def revoke_access(self, user_id: int):
        db = self._get_db()
        try:
            access = self._get_active_access_query(db, user_id).first()
            if not access:
                raise VPNAccessNotFoundError()

            access.revoked = True
            db.commit()
            db.refresh(access)
            return access
        finally:
            db.close()

    def ensure_access_matches_subscription(self, user_id: int):
        db = self._get_db()
        try:
            try:
                sub = self.subscription_service.require_active_subscription(user_id)
            except NoActiveSubscriptionError:
                access = self._get_active_access_query(db, user_id).first()
                if access:
                    access.revoked = True
                    db.commit()
                    db.refresh(access)
                return access

            access = self._get_active_access_query(db, user_id).first()
            if not access:
                return None

            access.expires_at = sub.ends_at
            access.last_synced_at = utc_now()
            db.commit()
            db.refresh(access)
            return access
        finally:
            db.close()

    def _resolve_server_for_access(self, user_id: int, server_name: str | None):
        if self.marzban_service is None:
            return None
        if server_name:
            try:
                return self.marzban_service.get_server_by_name(server_name)
            except Exception:
                return None
        try:
            server, _, _ = self.marzban_service.select_server_for_user(user_id)
            return server
        except NoMarzbanServersAvailableError:
            return None

    def _build_access_material(self, *, credential_id: str, server_name: str) -> dict[str, str | None]:
        access_url = f"mock://{server_name}/{credential_id}"
        return {
            "access_url": access_url,
            "subscription_url": access_url,
            "sync_status": "mocked",
            "provisioning_error": (
                "Server selected by backend. Replace mock provisioning with real Marzban API calls in the next step."
            ),
        }
