from __future__ import annotations

from typing import Any

from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)


def render_button(button: dict[str, Any]) -> KeyboardButton:
    text = str(button.get("text", ""))
    web_app_url = button.get("web_app")
    if isinstance(web_app_url, str) and web_app_url.strip():
        return KeyboardButton(
            text=text,
            web_app=WebAppInfo(url=web_app_url.strip()),
        )
    return KeyboardButton(text=text)


def render_keyboard(layout: list[list[dict[str, Any]]]):
    if not layout:
        return None

    return ReplyKeyboardMarkup(
        keyboard=[
            [render_button(button) for button in row]
            for row in layout
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def answer_with_ui(target: Message, response: dict[str, Any]) -> None:
    ui = response["ui"]
    reply_markup = render_keyboard(ui["keyboard"])

    await target.answer(
        ui["text"],
        reply_markup=reply_markup,
    )
