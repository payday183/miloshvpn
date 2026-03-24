from __future__ import annotations
import os
from dataclasses import dataclass


def parse_admin_ids(value: str) -> set[int]:
    return {int(x.strip()) for x in value.split(",") if x.strip()}


@dataclass(frozen=True, slots=True)
class Settings:
    api_host: str
    api_port: int
    public_base_url: str

    default_subscription_days: int
    default_vpn_server: str
    default_vpn_protocol: str

    vpn_token_prefix: str
    bot_token: str

    admin_ids: set[int]
    yookassa_shop_id: str
    yookassa_secret_key: str
    yookassa_mode: str
    yookassa_api_base_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8000")),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "https://vpn.example.com"),

            default_subscription_days=int(os.getenv("DEFAULT_SUBSCRIPTION_DAYS", "30")),
            default_vpn_server=os.getenv("DEFAULT_VPN_SERVER", "ru-msk-1"),
            default_vpn_protocol=os.getenv("DEFAULT_VPN_PROTOCOL", "wireguard"),

            vpn_token_prefix=os.getenv("VPN_TOKEN_PREFIX", "miloshvpn"),
            bot_token=os.getenv("BOT_TOKEN", ""),

            admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
            yookassa_shop_id=os.getenv("YOOKASSA_SHOP_ID", "").strip(),
            yookassa_secret_key=os.getenv("YOOKASSA_SECRET_KEY", "").strip(),
            yookassa_mode=os.getenv("YOOKASSA_MODE", "test").strip() or "test",
            yookassa_api_base_url=os.getenv(
                "YOOKASSA_API_BASE_URL",
                "https://api.yookassa.ru/v3",
            ).rstrip("/"),
        )


settings = Settings.from_env()
