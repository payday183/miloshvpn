from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting

PAYMENT_PROVIDER_DONATIONALERTS = "donationalerts"
PAYMENT_PROVIDER_MANUAL_SBP = "manual_sbp"
PAYMENT_PROVIDER_HYBRID = "hybrid"
PAYMENT_PROVIDER_SETTING_KEY = "active_payment_provider"

VALID_PAYMENT_PROVIDERS = {
    PAYMENT_PROVIDER_DONATIONALERTS,
    PAYMENT_PROVIDER_MANUAL_SBP,
    PAYMENT_PROVIDER_HYBRID,
}

PAYMENT_PROVIDER_LABELS = {
    PAYMENT_PROVIDER_DONATIONALERTS: "DonationAlerts",
    PAYMENT_PROVIDER_MANUAL_SBP: "Ручная модерация SBP",
    PAYMENT_PROVIDER_HYBRID: "Гибрид DonationAlerts + админ",
}


@dataclass(frozen=True)
class PaymentProviderInfo:
    code: str
    label: str


def payment_provider_label(provider: str | None) -> str:
    return PAYMENT_PROVIDER_LABELS.get(provider or "", provider or "не выбран")


async def get_active_payment_provider(session: AsyncSession) -> str:
    setting = await session.scalar(select(Setting).where(Setting.key == PAYMENT_PROVIDER_SETTING_KEY))
    provider = setting.value if setting else PAYMENT_PROVIDER_DONATIONALERTS
    return provider if provider in VALID_PAYMENT_PROVIDERS else PAYMENT_PROVIDER_DONATIONALERTS


async def set_active_payment_provider(session: AsyncSession, provider: str) -> str:
    if provider not in VALID_PAYMENT_PROVIDERS:
        raise ValueError("Unknown payment provider")

    setting = await session.scalar(select(Setting).where(Setting.key == PAYMENT_PROVIDER_SETTING_KEY))
    if setting is None:
        setting = Setting(key=PAYMENT_PROVIDER_SETTING_KEY, value=provider)
        session.add(setting)
    else:
        setting.value = provider
    await session.flush()
    return provider


def list_payment_providers() -> list[PaymentProviderInfo]:
    return [
        PaymentProviderInfo(code=PAYMENT_PROVIDER_DONATIONALERTS, label=PAYMENT_PROVIDER_LABELS[PAYMENT_PROVIDER_DONATIONALERTS]),
        PaymentProviderInfo(code=PAYMENT_PROVIDER_MANUAL_SBP, label=PAYMENT_PROVIDER_LABELS[PAYMENT_PROVIDER_MANUAL_SBP]),
        PaymentProviderInfo(code=PAYMENT_PROVIDER_HYBRID, label=PAYMENT_PROVIDER_LABELS[PAYMENT_PROVIDER_HYBRID]),
    ]
