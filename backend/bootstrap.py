from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def _run_migrations() -> None:
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    command.upgrade(alembic_config, "head")


def bootstrap() -> None:
    _run_migrations()
    from backend.api.routes import services

    services.plan_service.ensure_default_catalog()
    services.content_service.ensure_default_blocks()
    services.admin_setting_service.ensure_default_yookassa_settings()


def main() -> None:
    bootstrap()


if __name__ == "__main__":
    main()
