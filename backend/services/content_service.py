from __future__ import annotations

from backend.models.orm import ContentBlockORM
from backend.repositories.content_repo import ContentRepository


class ContentBlockNotFoundError(Exception):
    pass


class ContentService:
    DEFAULT_BLOCKS = {
        "purchase_intro": {
            "title_ru": "До покупки",
            "title_en": "Purchase intro",
            "body_ru": "Выберите тариф и завершите оплату.",
            "body_en": "Choose a plan and complete payment.",
        },
        "post_purchase_intro": {
            "title_ru": "После покупки",
            "title_en": "Post purchase intro",
            "body_ru": "Оплата прошла. Используйте ваш ключ доступа ниже.",
            "body_en": "Payment completed. Use your access key below.",
        },
        "connection_instructions": {
            "title_ru": "Инструкция по подключению",
            "title_en": "Connection instructions",
            "body_ru": "Установите клиент, вставьте ключ и подключитесь.",
            "body_en": "Install a client app, paste the key, and connect.",
        },
    }

    def __init__(self, content_repo: ContentRepository) -> None:
        self.content_repo = content_repo

    def list_blocks(self, active_only: bool = False) -> list[ContentBlockORM]:
        return self.content_repo.list_blocks(active_only=active_only)

    def list_public_blocks(self) -> list[dict[str, object]]:
        return [self.block_to_dict(block) for block in self.list_blocks(active_only=True)]

    def ensure_default_blocks(self) -> None:
        existing = {
            block.key: block
            for block in self.list_blocks(active_only=False)
        }
        for key, values in self.DEFAULT_BLOCKS.items():
            block = existing.get(key)
            if block is None:
                self.content_repo.create(
                    key=key,
                    title=values["title_ru"],
                    body=values["body_ru"],
                    format="markdown",
                    is_active=True,
                )
                continue

            if block.title in {None, "", values["title_en"]}:
                block.title = values["title_ru"]
            if not block.body or block.body == values["body_en"]:
                block.body = values["body_ru"]
            self.content_repo.save(block)

    def get_default_title(self, key: str) -> str | None:
        values = self.DEFAULT_BLOCKS.get(key)
        if values is None:
            return None
        return str(values["title_ru"])

    def get_default_body(self, key: str) -> str | None:
        values = self.DEFAULT_BLOCKS.get(key)
        if values is None:
            return None
        return str(values["body_ru"])

    def get_by_key(self, key: str) -> ContentBlockORM:
        block = self.content_repo.get_by_key(key)
        if block is None:
            raise ContentBlockNotFoundError(f"Контент-блок '{key}' не найден.")
        return block

    def update_block(
        self,
        key: str,
        *,
        title: str | None = None,
        body: str | None = None,
        format: str | None = None,
        is_active: bool | None = None,
        updated_by_telegram_id: int | None = None,
    ) -> ContentBlockORM:
        block = self.get_by_key(key)
        if title is not None:
            block.title = title
        if body is not None:
            block.body = body
        if format is not None:
            block.format = format
        if is_active is not None:
            block.is_active = bool(is_active)
        if updated_by_telegram_id is not None:
            block.updated_by_telegram_id = updated_by_telegram_id
        return self.content_repo.save(block)

    def block_to_dict(self, block: ContentBlockORM) -> dict[str, object]:
        return {
            "id": block.id,
            "key": block.key,
            "title": block.title,
            "body": block.body,
            "format": block.format,
            "is_active": block.is_active,
        }
