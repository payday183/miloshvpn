from backend.models.orm import UserORM
from backend.repositories.user_repo import UserRepository
from backend.config import settings


class UserNotFoundError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class UserService:
    def __init__(self, user_repo: UserRepository) -> None:
        self.user_repo = user_repo

    def register_user(
        self, telegram_id: int, username: str | None = None
    ) -> UserORM:
        existing = self.user_repo.get_by_telegram_id(telegram_id)

        role = self._resolve_role(telegram_id, existing)

        if existing is not None:
            if username and existing.username != username:
                existing.username = username

            if existing.role != role:
                existing.role = role

            self.user_repo.save(existing)
            return existing

        return self.user_repo.create(
            telegram_id=telegram_id,
            username=username,
            role=role,
        )

    def get_user(self, user_id: int) -> UserORM | None:
        return self.user_repo.get(user_id)

    def require_user(self, user_id: int) -> UserORM:
        user = self.get_user(user_id)
        if user is None:
            raise UserNotFoundError(f"User {user_id} was not found.")
        return user

    def require_user_by_telegram_id(self, telegram_id: int) -> UserORM:
        user = self.user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            raise UserNotFoundError(f"User with telegram_id {telegram_id} was not found.")
        return user

    def assign_moderator(
        self,
        actor_telegram_id: int,
        target_telegram_id: int,
        phone: str,
    ) -> UserORM:
        actor = self.require_user_by_telegram_id(actor_telegram_id)
        if actor.role != "admin":
            raise PermissionDeniedError("Only admin can assign moderators.")

        existing = self.user_repo.get_by_telegram_id(target_telegram_id)

        if target_telegram_id in settings.admin_ids or (
            existing is not None and existing.role == "admin"
        ):
            raise PermissionDeniedError("Admin role cannot be changed.")

        if existing is None:
            return self.user_repo.create(
                telegram_id=target_telegram_id,
                username=None,
                role="moder",
                phone=phone,
            )

        if existing.role != "moder" or existing.phone != phone:
            existing.role = "moder"
            existing.phone = phone
            existing = self.user_repo.save(existing)

        return existing

    def remove_moderator(self, actor_telegram_id: int, target_telegram_id: int) -> UserORM:
        actor = self.require_user_by_telegram_id(actor_telegram_id)
        if actor.role != "admin":
            raise PermissionDeniedError("Only admin can remove moderators.")

        user = self.user_repo.get_by_telegram_id(target_telegram_id)
        if user is None or user.role != "moder":
            raise PermissionDeniedError("Not a moderator")

        user.role = "user"
        return self.user_repo.save(user)

    def list_users(self) -> list[UserORM]:
        return self.user_repo.list_all()

    def _resolve_role(self, telegram_id: int, existing: UserORM | None = None) -> str:
        if telegram_id in settings.admin_ids:
            return "admin"

        if existing is not None and existing.role == "moder":
            return "moder"

        return "user"
