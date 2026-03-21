from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

try:
    from bot.api import BackendError, handle_message
    from bot.utils.ui import answer_with_ui
except ModuleNotFoundError:
    from api import BackendError, handle_message
    from utils.ui import answer_with_ui

router = Router()


@router.message()
async def process_incoming_message(message: Message) -> None:
    try:
        response = await handle_message(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            text=message.text,
        )
    except BackendError as error:
        await message.answer(str(error))
        return

    await answer_with_ui(message, response)
