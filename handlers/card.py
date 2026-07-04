import html as html_mod
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_ID
from database.models import UserSettings, ParsedItem
from services.goo_service import generate_link
from services.team_config import resolve_profile_id, get_team_key, get_user_key_for_team

logger = logging.getLogger(__name__)
card_router = Router(name="card")


@card_router.callback_query(F.data == "settings_card")
async def card_menu(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа.", show_alert=True); return
    user_id = callback.from_user.id
    settings = await session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))

    if not settings or not settings.offer_key:
        await callback.message.answer(
            "❌ <b>Offer Key не установлен.</b>\n\nВведи /setoffer <UUID>\n"
            "UUID из кабинета: Инструменты → Трафик → Ваш оффер",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    item = await session.scalar(
        select(ParsedItem).where(ParsedItem.user_id == user_id, ParsedItem.link != "").order_by(ParsedItem.id.desc())
    )
    if not item:
        await callback.message.edit_text("❌ Нет товаров в базе. Сначала загрузи JSON.", parse_mode="HTML")
        await callback.answer()
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Tsum", callback_data="card_gen_Tsum")],
        [InlineKeyboardButton(text="Nurrp", callback_data="card_gen_Nurrp")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_settings")],
    ])
    await callback.message.edit_text(
        f"🔗 <b>Выберите команду</b>\n\n📦 <b>Товар:</b> {html_mod.escape(item.title or item.nickname)}\n"
        f"💰 <b>Цена:</b> {html_mod.escape(item.price or '-')}",
        parse_mode="HTML", reply_markup=kb,
    )
    await callback.answer()


@card_router.callback_query(F.data.startswith("card_gen_"))
async def card_generate(callback: CallbackQuery, session: AsyncSession):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Нет доступа.", show_alert=True); return
    team_code = callback.data.replace("card_gen_", "")
    team_key = get_team_key(team_code)
    if not team_key:
        await callback.message.edit_text(f"❌ Ключ для {team_code} не найден.", parse_mode="HTML")
        await callback.answer(); return

    user_id = callback.from_user.id
    settings = await session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    if not settings:
        await callback.answer("Ошибка.", show_alert=True); return

    profile_id = resolve_profile_id(settings, team_code)
    user_key = get_user_key_for_team(settings, team_code)
    if not user_key:
        await callback.message.edit_text(
            f"❌ <b>Ключ пользователя не установлен для {team_code}.</b>\nМеню → Profile 👤",
            parse_mode="HTML",
        )
        await callback.answer(); return

    item = await session.scalar(
        select(ParsedItem).where(ParsedItem.user_id == user_id, ParsedItem.link != "").order_by(ParsedItem.id.desc())
    )
    if not item:
        await callback.message.edit_text("❌ Нет товаров.", parse_mode="HTML"); await callback.answer(); return

    await callback.message.edit_text(
        f"🔗 <b>Генерация ссылки</b>\n\n🎮 <b>{team_code}</b>\n👤 <b>Профиль:</b> <code>{html_mod.escape(profile_id)}</code>\n"
        f"📦 <b>Товар:</b> {html_mod.escape(item.title or item.nickname)}\n"
        f"💰 <b>Цена:</b> {html_mod.escape(item.price or '-')}\n\n⏳ Генерирую...",
        parse_mode="HTML",
    )

    price_num = ""
    if item.price:
        try: price_num = float("".join(c for c in item.price if c.isdigit() or c == "."))
        except: pass

    ok, result = await generate_link(
        team_key=team_key, user_key=user_key, offer_key=settings.offer_key,
        name=item.title or "", price=str(price_num),
    )

    if ok:
        await callback.message.edit_text(f"✅ <b>Ссылка готова!</b>\n\n🔗 {html_mod.escape(result)}", parse_mode="HTML")
    else:
        await callback.message.edit_text(f"❌ <b>Ошибка:</b> {html_mod.escape(result[:300])}", parse_mode="HTML")
    await callback.answer()
