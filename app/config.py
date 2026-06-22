from functools import lru_cache
from decimal import Decimal
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
    payment_amount_tolerance_rub: Decimal = Decimal("2.00")

    x3ui_mode: Literal["mock", "live"] = "mock"
    x3ui_base_url: str = "https://host.docker.internal:24475"
    x3ui_web_base_path: str = ""
    x3ui_sub_base_url: str = ""
    x3ui_tls_verify: bool = False
    x3ui_timeout_seconds: int = 20
    x3ui_username: str = "admin"
    x3ui_password: str = "admin"
    x3ui_api_token: str = ""
    my_3x_ui_login: str = ""
    my_3x_ui_password: str = ""
    x3ui_inbound_id: int = 1
    x3ui_max_clients: int = 10
    x3ui_user_inbound_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    x3ui_admin_inbound_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    x3ui_public_inbound_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    x3ui_user_limit_ip: int = 2
    x3ui_admin_limit_ip: int = 1
    x3ui_public_limit_ip: int = 0
    direct_node_user_limit_ip: int = 0
    node_selection_mode: Literal["active", "least_loaded"] = "least_loaded"
    node_overload_cpu_percent: int = 85
    node_overload_memory_percent: int = 90
    node_overload_disk_percent: int = 90
    vless_public_host: str = "127.0.0.1"
    vless_public_port: int = 8443
    vless_query: str = "type=tcp&security=none"
    admin_reality_inbound_id: int = 2
    admin_reality_public_host: str = ""
    admin_reality_public_port: int = 443
    admin_reality_public_key: str = ""
    admin_reality_vless_query: str = ""

    public_key_enabled: bool = True
    public_key_rotate_hours: int = 24
    public_key_post_interval_hours: int = 48
    public_key_traffic_gb: int = 500
    public_key_post_hour_msk: int = 14
    public_key_chat_id: str = ""
    free_trial_enabled: bool = True
    free_trial_days: int = 7
    free_trial_traffic_gb: int = 500
    node_status_poll_interval_seconds: int = 3600
    expired_subscription_cleanup_enabled: bool = True
    expired_subscription_cleanup_interval_seconds: int = 86400

    app_env: str = ""
    node_provisioning_mode: Literal["disabled", "direct_node"] = "disabled"
    enable_direct_node_provisioning: bool = False
    enable_node_agent: bool = False
    dry_run_first: bool = True

    admin_test_enabled: bool = False
    admin_test_user_id: int = 0
    admin_test_telegram_id: int = 0
    admin_test_node_id: str = "de-1"
    admin_test_country_code: str = "DE"
    admin_test_country_flag: str = "🇩🇪"
    admin_test_create_sub: bool = False
    admin_test_profiles_count: int = 5
    admin_direct_node_id: str = ""

    subscription_mode: str = "single_sub_with_5_profiles"
    subscription_title_template: str = "🇩🇪 Germany Test {user_id}"
    subscription_base_url: str = ""
    subscription_public_host: str = ""
    subscription_token_secret: str = ""

    node_de_1_id: str = "de-1"
    node_de_1_name: str = "Germany-1"
    node_de_1_country_code: str = "DE"
    node_de_1_country_flag: str = "🇩🇪"
    node_de_1_public_host: str = ""
    node_de_1_public_ip: str = ""
    node_de_1_3xui_base_url: str = ""
    node_de_1_3xui_username: str = ""
    node_de_1_3xui_password: str = ""
    node_de_1_3xui_verify_tls: bool = False
    node_de_1_3xui_timeout_seconds: int = 20
    node_de_1_agent_url: str = ""
    node_de_1_agent_token: str = ""
    node_de_1_agent_verify_tls: bool = False
    node_de_1_agent_timeout_seconds: int = 20
    node_de_1_vpn_port_min: int = 30000
    node_de_1_vpn_port_max: int = 39999
    node_de_1_reserved_ports: str = "22,443,44217,2096,5353,6881-6999,6969,51413,1337,2710"

    port_allocator_scope: Literal["per_node"] = "per_node"
    profile_speed_limit_mbit: int = 40
    profile_speed_limit_label: str = "5MBps"
    tc_limit_enabled: bool = True
    firewall_auto_open_ports: bool = True
    firewall_provider: str = "ufw"
    inbound_pool_enabled: bool = True
    inbound_delete_on_user_move: bool = False
    inbound_reuse_free_slots: bool = True
    inbound_slot_id_prefix: str = "DE"
    inbound_name_template: str = "{flag} {node_code}-SLOT-{slot_number}-{protocol}"
    client_email_template: str = "u{user_id}-{protocol}-{slot_number}"
    client_remark_template: str = "admin-test-u{user_id}-{protocol}"

    r1_reality_public_key: str = ""
    r1_reality_private_key: str = ""
    r1_reality_dest: str = "www.amd.com:443"
    r1_reality_utls: str = "safari"
    r1_reality_spider_x: str = "/"
    r2_reality_public_key: str = ""
    r2_reality_private_key: str = ""
    r2_reality_dest: str = "www.sony.com:443"
    r2_reality_utls: str = "edge"
    r2_reality_spider_x: str = "/"
    r2_transport: str = "xhttp"
    r2_xhttp_path: str = "/"
    r2_xhttp_mode: str = "auto"
    r2_xhttp_padding_bytes: str = "100-1000"
    h3_protocol: str = "hysteria"
    h3_tls_enabled: bool = True
    h3_tls_sni: str = ""
    h3_tls_cert_file: str = "/root/cert/ip/fullchain.pem"
    h3_tls_key_file: str = "/root/cert/ip/privkey.pem"
    h3_tls_min_version: str = "1.2"
    h3_tls_max_version: str = "1.3"
    h3_utls: str = "safari"
    h3_ocsp_stapling_seconds: int = 3600
    h3_decrypt: str = ""
    h3_encrypt: str = ""
    h3_auth: str = "X25519"
    h3_vision_seed: str = "testseed"
    r4_reality_public_key: str = ""
    r4_reality_private_key: str = ""
    r4_reality_dest: str = "www.intel.com:443"
    r4_reality_utls: str = "edge"
    r4_reality_spider_x: str = "/"
    r5_reality_public_key: str = ""
    r5_reality_private_key: str = ""
    r5_reality_dest: str = "www.amd.com:443"
    r5_reality_utls: str = "random"
    r5_reality_spider_x: str = "/"

    @field_validator(
        "admin_ids",
        "x3ui_user_inbound_ids",
        "x3ui_admin_inbound_ids",
        "x3ui_public_inbound_ids",
        mode="before",
    )
    @classmethod
    def parse_int_list(cls, value: object) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [int(item) for item in value if str(item).strip()]
        return [int(item.strip()) for item in str(value).split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
