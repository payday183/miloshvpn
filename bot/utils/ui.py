from __future__ import annotations

from typing import Any

from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)


def render_keyboard(layout: list[list[dict[str, str]]]):
    if not layout:
        return None

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=button["text"]) for button in row]
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
