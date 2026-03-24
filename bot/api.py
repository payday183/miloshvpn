from __future__ import annotations

from typing import Any

import httpx

try:
    from bot.config import settings
except ModuleNotFoundError:
    from config import settings


class BackendError(Exception):
    pass


BotResponse = dict[str, Any]


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"Сервер вернул ошибку {response.status_code}."

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail

    return f"Сервер вернул ошибку {response.status_code}."


async def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=settings.backend_url,
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        ) as client:
            response = await client.request(method, path, json=payload)
    except httpx.RequestError as error:
        raise BackendError("Не удалось связаться с сервером.") from error

    if response.is_error:
        raise BackendError(_extract_error_detail(response))

    data = response.json()
    if not isinstance(data, dict):
        raise BackendError("Сервер вернул неожиданный ответ.")

    return data


def _validate_bot_response(data: dict[str, Any]) -> BotResponse:
    if not isinstance(data.get("user"), dict):
        raise BackendError("Сервер не вернул user.")

    ui = data.get("ui")
    if not isinstance(ui, dict):
        raise BackendError("Сервер не вернул ui.")

    if not isinstance(ui.get("text"), str):
        raise BackendError("Сервер не вернул ui.text.")

    if not isinstance(ui.get("keyboard"), list):
        raise BackendError("Сервер не вернул ui.keyboard.")

    for row in ui["keyboard"]:
        if not isinstance(row, list):
            raise BackendError("Сервер вернул некорректный ui.keyboard.")
        for button in row:
            if not isinstance(button, dict):
                raise BackendError("Сервер вернул некорректную кнопку.")
            if not isinstance(button.get("text"), str):
                raise BackendError("Сервер вернул кнопку без text.")
            if not isinstance(button.get("action"), str):
                raise BackendError("Сервер вернул кнопку без action.")
            web_app = button.get("web_app")
            if web_app is not None and not isinstance(web_app, str):
                raise BackendError("Сервер вернул кнопку с некорректным web_app.")

    return data


async def handle_message(
    telegram_id: int,
    username: str | None,
    text: str | None,
) -> BotResponse:
    data = await _request(
        "POST",
        "/api/bot/messages",
        payload={
            "telegram_id": telegram_id,
            "username": username,
            "text": text,
        },
    )

    return _validate_bot_response(data)
