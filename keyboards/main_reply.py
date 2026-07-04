from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Меню")]
        ],
        resize_keyboard=True,
    )
