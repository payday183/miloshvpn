from backend.models.orm import VPNAccessORM
from backend.services.subscription_service import SubscriptionService, NoActiveSubscriptionError
from shared.postgres import SessionLocal
from shared.utils import generate_secret


class VPNAccessNotFoundError(Exception):
    pass


class VPNService:
    def __init__(self, subscription_service: SubscriptionService, settings):
        self.subscription_service = subscription_service
        self.settings = settings

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
            if existing:
                existing.expires_at = sub.ends_at
                db.commit()
                db.refresh(existing)
                return existing

            access = VPNAccessORM(
                user_id=user_id,
                server_name=server_name or self.settings.default_vpn_server,
                protocol=protocol or self.settings.default_vpn_protocol,
                credential_id=generate_secret("vpn"),
                access_url="mock://vpn",
                config_blob="mock",
                expires_at=sub.ends_at,
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
            db.commit()
            db.refresh(access)
            return access
        finally:
            db.close()
