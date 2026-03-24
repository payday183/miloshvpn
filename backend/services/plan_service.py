from __future__ import annotations

from backend.models.orm import PlanORM, PlanPeriodORM
from backend.repositories.plan_repo import PlanRepository


class PlanNotFoundError(Exception):
    pass


class PlanPeriodNotFoundError(Exception):
    pass


class PlanService:
    DEFAULT_PLAN_CODE = "vpn"
    DEFAULT_PLAN_NAME = "VPN"
    DEFAULT_PLAN_DESCRIPTION_RU = "Базовый VPN-тариф"
    DEFAULT_PLAN_DESCRIPTION_EN = "Default VPN subscription plan"
    DEFAULT_PERIODS = {
        "1_month": {"label_ru": "1 месяц", "label_en": "1 month", "months": 1, "days": 30, "amount": 300.0, "sort_order": 10},
        "3_month": {"label_ru": "3 месяца", "label_en": "3 months", "months": 3, "days": 90, "amount": 800.0, "sort_order": 20},
        "6_month": {"label_ru": "6 месяцев", "label_en": "6 months", "months": 6, "days": 180, "amount": 1500.0, "sort_order": 30},
        "12_month": {"label_ru": "12 месяцев", "label_en": "12 months", "months": 12, "days": 365, "amount": 2800.0, "sort_order": 40},
    }

    def __init__(self, plan_repo: PlanRepository) -> None:
        self.plan_repo = plan_repo

    def list_plans(self, active_only: bool = False) -> list[PlanORM]:
        return self.plan_repo.list_plans(active_only=active_only)

    def list_periods(
        self,
        plan_id: int | None = None,
        active_only: bool = False,
    ) -> list[PlanPeriodORM]:
        return self.plan_repo.list_periods(plan_id=plan_id, active_only=active_only)

    def list_public_catalog(self) -> list[dict[str, object]]:
        plans = self.list_plans(active_only=True)
        periods = self.list_periods(active_only=True)

        periods_by_plan: dict[int, list[PlanPeriodORM]] = {}
        for period in periods:
            periods_by_plan.setdefault(period.plan_id, []).append(period)

        catalog: list[dict[str, object]] = []
        for plan in plans:
            catalog.append(
                {
                    "id": plan.id,
                    "code": plan.code,
                    "name": plan.name,
                    "description": plan.description,
                    "is_active": plan.is_active,
                    "sort_order": plan.sort_order,
                    "periods": [
                        self.period_to_dict(period)
                        for period in periods_by_plan.get(plan.id, [])
                    ],
                }
            )
        return catalog

    def ensure_default_catalog(self) -> None:
        plans = self.plan_repo.list_plans(active_only=False)
        plan = next((item for item in plans if item.code == self.DEFAULT_PLAN_CODE), None)

        if plan is None:
            plan = self.plan_repo.create_plan(
                code=self.DEFAULT_PLAN_CODE,
                name=self.DEFAULT_PLAN_NAME,
                description=self.DEFAULT_PLAN_DESCRIPTION_RU,
                is_active=True,
                sort_order=10,
            )
        elif plan.description in {None, "", self.DEFAULT_PLAN_DESCRIPTION_EN}:
            plan.description = self.DEFAULT_PLAN_DESCRIPTION_RU
            plan = self.plan_repo.save_plan(plan)

        periods = {
            period.period_key: period
            for period in self.plan_repo.list_periods(plan_id=plan.id, active_only=False)
        }

        for period_key, values in self.DEFAULT_PERIODS.items():
            period = periods.get(period_key)
            if period is None:
                self.plan_repo.create_period(
                    plan_id=plan.id,
                    period_key=period_key,
                    label=values["label_ru"],
                    period_months=int(values["months"]),
                    duration_days=int(values["days"]),
                    price_amount=float(values["amount"]),
                    currency="RUB",
                    is_active=True,
                    sort_order=int(values["sort_order"]),
                )
                continue

            if period.label in {"", values["label_en"]}:
                period.label = str(values["label_ru"])
            if not period.period_months:
                period.period_months = int(values["months"])
            if not period.duration_days:
                period.duration_days = int(values["days"])
            if not period.sort_order:
                period.sort_order = int(values["sort_order"])
            self.plan_repo.save_period(period)

    def get_period(self, period_id: int) -> PlanPeriodORM:
        period = self.plan_repo.get_period(period_id)
        if period is None:
            raise PlanPeriodNotFoundError(f"Период тарифа {period_id} не найден.")
        return period

    def update_period(
        self,
        period_id: int,
        *,
        label: str | None = None,
        price_amount: float | None = None,
        duration_days: int | None = None,
        period_months: int | None = None,
        is_active: bool | None = None,
        sort_order: int | None = None,
    ) -> PlanPeriodORM:
        period = self.get_period(period_id)
        if label is not None:
            period.label = label
        if price_amount is not None:
            period.price_amount = float(price_amount)
        if duration_days is not None:
            period.duration_days = int(duration_days)
        if period_months is not None:
            period.period_months = int(period_months)
        if is_active is not None:
            period.is_active = bool(is_active)
        if sort_order is not None:
            period.sort_order = int(sort_order)
        return self.plan_repo.save_period(period)

    def period_to_dict(self, period: PlanPeriodORM) -> dict[str, object]:
        return {
            "id": period.id,
            "plan_id": period.plan_id,
            "period_key": period.period_key,
            "label": period.label,
            "period_months": period.period_months,
            "duration_days": period.duration_days,
            "price_amount": period.price_amount,
            "currency": period.currency,
            "is_active": period.is_active,
            "sort_order": period.sort_order,
        }
