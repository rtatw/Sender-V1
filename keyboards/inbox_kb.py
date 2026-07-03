from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_inbox_message_keyboard(msg_id: int, translated: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if translated:
        builder.button(text="🔄 Показать оригинал", callback_data=f"show_orig_{msg_id}")
    else:
        builder.button(text="🈶 Перевести", callback_data=f"inbox_translate_{msg_id}")
    builder.button(text="📝 Написать еще", callback_data=f"write_more_{msg_id}")
    builder.button(text="🔗 Создать ссылку", callback_data=f"inbox_link_{msg_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_write_more_keyboard(msg_id: int, presets: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in presets:
        builder.button(text=p.name[:30], callback_data=f"preset_reply_{p.id}_{msg_id}")
    builder.button(text="✍️ Написать свой текст", callback_data=f"write_custom_text_{msg_id}")
    builder.button(text="❌ Отмена", callback_data="cancel_write")
    builder.adjust(1)
    return builder.as_markup()


def get_send_preview_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✈️ Отправить", callback_data=f"send_custom_{msg_id}")
    builder.button(text="❌ Отмена", callback_data="cancel_write")
    builder.adjust(1)
    return builder.as_markup()


def get_preset_reply_keyboard(presets: list, msg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in presets:
        builder.button(text=p.name[:30], callback_data=f"preset_reply_{p.id}_{msg_id}")
    builder.button(text="🔙 Назад", callback_data=f"write_more_{msg_id}")
    builder.adjust(1)
    return builder.as_markup()
