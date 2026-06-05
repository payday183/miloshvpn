from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "miloshvpn"
    app_port: int = 8080
    environment: str = "dev"

    database_url: str = "postgresql+asyncpg://milosh:milosh_password@postgres:5432/miloshvpn"
    redis_url: str = "redis://redis:6379/0"

    bot_token: str = ""
    bot_username: str = ""
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    admin_web_token: str = ""
    admin_panel_url: str = "http://localhost:8081/admin"

    donationalerts_token: str = ""
    donationalerts_donate_url: str = ""
    donationalerts_poll_interval_seconds: int = 45
    donationalerts_fetch_limit: int = 50
    donationalerts_fetch_pages: int = 3

    x3ui_mode: Literal["mock", "live"] = "mock"
    x3ui_base_url: str = "http://x3ui:2053"
    x3ui_username: str = "admin"
    x3ui_password: str = "admin"
    x3ui_inbound_id: int = 1
    x3ui_max_clients: int = 10
    node_selection_mode: Literal["active", "least_loaded"] = "least_loaded"
    node_overload_cpu_percent: int = 85
    node_overload_memory_percent: int = 90
    node_overload_disk_percent: int = 90
    vless_public_host: str = "127.0.0.1"
    vless_public_port: int = 8443
    vless_query: str = "type=tcp&security=none"

    public_key_enabled: bool = True
    public_key_rotate_hours: int = 24
    public_key_chat_id: str = ""
    node_status_poll_interval_seconds: int = 60
    expired_subscription_cleanup_enabled: bool = True
    expired_subscription_cleanup_interval_seconds: int = 300

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [int(item) for item in value if str(item).strip()]
        return [int(item.strip()) for item in str(value).split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
