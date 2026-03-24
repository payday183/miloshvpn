from __future__ import annotations

from backend.models.orm import PlanORM, PlanPeriodORM
from shared.postgres import SessionLocal


class PlanRepository:
    def _get_db(self):
        return SessionLocal()

    def list_plans(self, active_only: bool = False) -> list[PlanORM]:
        db = self._get_db()
        try:
            query = db.query(PlanORM)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.order_by(PlanORM.sort_order.asc(), PlanORM.id.asc()).all()
        finally:
            db.close()

    def get_plan(self, plan_id: int) -> PlanORM | None:
        db = self._get_db()
        try:
            return db.query(PlanORM).filter_by(id=plan_id).first()
        finally:
            db.close()

    def save_plan(self, plan: PlanORM) -> PlanORM:
        db = self._get_db()
        try:
            merged = db.merge(plan)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()

    def create_plan(
        self,
        code: str,
        name: str,
        description: str | None = None,
        is_active: bool = True,
        sort_order: int = 0,
    ) -> PlanORM:
        db = self._get_db()
        try:
            plan = PlanORM(
                code=code,
                name=name,
                description=description,
                is_active=is_active,
                sort_order=sort_order,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            return plan
        finally:
            db.close()

    def list_periods(
        self,
        plan_id: int | None = None,
        active_only: bool = False,
    ) -> list[PlanPeriodORM]:
        db = self._get_db()
        try:
            query = db.query(PlanPeriodORM)
            if plan_id is not None:
                query = query.filter_by(plan_id=plan_id)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.order_by(PlanPeriodORM.sort_order.asc(), PlanPeriodORM.id.asc()).all()
        finally:
            db.close()

    def get_period(self, period_id: int) -> PlanPeriodORM | None:
        db = self._get_db()
        try:
            return db.query(PlanPeriodORM).filter_by(id=period_id).first()
        finally:
            db.close()

    def save_period(self, period: PlanPeriodORM) -> PlanPeriodORM:
        db = self._get_db()
        try:
            merged = db.merge(period)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()

    def create_period(
        self,
        *,
        plan_id: int,
        period_key: str,
        label: str,
        period_months: int,
        duration_days: int,
        price_amount: float,
        currency: str = "RUB",
        is_active: bool = True,
        sort_order: int = 0,
    ) -> PlanPeriodORM:
        db = self._get_db()
        try:
            period = PlanPeriodORM(
                plan_id=plan_id,
                period_key=period_key,
                label=label,
                period_months=period_months,
                duration_days=duration_days,
                price_amount=price_amount,
                currency=currency,
                is_active=is_active,
                sort_order=sort_order,
            )
            db.add(period)
            db.commit()
            db.refresh(period)
            return period
        finally:
            db.close()
