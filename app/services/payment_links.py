from decimal import Decimal
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import get_settings
from app.models import Order


def payment_page_url(order: Order | int) -> str:
    if isinstance(order, Order):
        return public_url(f"/pay/{order.id}", {"code": order.payment_code})
    return public_url(f"/pay/{order}")


def donation_url_for_order(order: Order, amount: Decimal | None = None, *, email: str | None = None) -> str:
    settings = get_settings()
    if not settings.donationalerts_donate_url:
        return ""

    parts = urlsplit(settings.donationalerts_donate_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["amount"] = format_amount(amount or order.amount_rub)
    query["message"] = order.payment_code
    if email:
        query["email"] = email
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def public_url(path: str, query: Mapping[str, str] | None = None) -> str:
    settings = get_settings()
    base = settings.admin_panel_url or f"http://localhost:{settings.app_port}/admin"
    parts = urlsplit(base)
    if not parts.scheme or not parts.netloc:
        parts = urlsplit(f"http://localhost:{settings.app_port}/admin")
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query or {}), ""))


def format_amount(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"))) if value == value.quantize(Decimal("1")) else str(value)
