import json
from typing import Optional
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import (
    UserSettings, ParsedItem, EmailAccount, Proxy, Template,
    Subject, ReceiveEmail, IncomingMessage, GlobalSettings, AdminRole,
)


async def get_settings(session: AsyncSession, user_id: int) -> Optional[UserSettings]:
    return await session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))


async def get_or_create_settings(session: AsyncSession, user_id: int,
                                 display_name: str = "", username: str = "") -> UserSettings:
    s = await get_settings(session, user_id)
    if s is None:
        s = UserSettings(user_id=user_id, display_name=display_name, username=username)
        session.add(s)
        await session.commit()
    else:
        # Update name if provided
        if display_name and s.display_name != display_name:
            s.display_name = display_name
        if username and s.username != username:
            s.username = username
        if display_name or username:
            await session.commit()
    return s


async def update_user_info(session: AsyncSession, user_id: int,
                           display_name: str, username: str) -> None:
    s = await get_settings(session, user_id)
    if s:
        changed = False
        if display_name and s.display_name != display_name:
            s.display_name = display_name; changed = True
        if username and s.username != username:
            s.username = username; changed = True
        if changed:
            await session.commit()


async def get_all_users(session: AsyncSession) -> list[UserSettings]:
    return list(await session.scalars(
        select(UserSettings).order_by(UserSettings.display_name, UserSettings.user_id)))


# ─── AdminRole ────────────────────────────────────────────────────

async def get_admin_role(session: AsyncSession, user_id: int) -> Optional[AdminRole]:
    return await session.scalar(select(AdminRole).where(AdminRole.user_id == user_id))


async def get_all_admins(session: AsyncSession) -> list[AdminRole]:
    return list(await session.scalars(select(AdminRole).order_by(AdminRole.created_at)))


async def set_admin_role(session: AsyncSession, user_id: int, role: str,
                         permissions: Optional[dict] = None) -> AdminRole:
    ar = await get_admin_role(session, user_id)
    if ar is None:
        from database.models import get_default_assistant_permissions
        perms = json.dumps(permissions) if permissions else get_default_assistant_permissions()
        ar = AdminRole(user_id=user_id, role=role, permissions=perms)
        session.add(ar)
    else:
        ar.role = role
        if permissions:
            ar.permissions = json.dumps(permissions)
    await session.commit()
    return ar


async def delete_admin_role(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(AdminRole).where(AdminRole.user_id == user_id))
    await session.commit()


def is_superadmin(user_id: int) -> bool:
    from config import ADMIN_ID
    return user_id == ADMIN_ID


async def get_global_settings(session: AsyncSession) -> Optional[GlobalSettings]:
    return await session.scalar(select(GlobalSettings).where(GlobalSettings.id == 1))


async def upsert_global_settings(session: AsyncSession, **kwargs) -> GlobalSettings:
    gs = await get_global_settings(session)
    if gs is None:
        gs = GlobalSettings(id=1, **kwargs)
        session.add(gs)
    else:
        for k, v in kwargs.items():
            setattr(gs, k, v)
    await session.commit()
    return gs


async def get_proxies(session: AsyncSession, user_id: int) -> list[Proxy]:
    return list(await session.scalars(
        select(Proxy).where(Proxy.user_id == user_id).order_by(Proxy.created_at)))


async def get_proxy_by_id(session: AsyncSession, proxy_id: int) -> Optional[Proxy]:
    return await session.scalar(select(Proxy).where(Proxy.id == proxy_id))


async def delete_proxy(session: AsyncSession, proxy_id: int) -> None:
    prx = await get_proxy_by_id(session, proxy_id)
    if prx:
        await session.delete(prx)
        await session.commit()


async def delete_all_proxies(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(Proxy).where(Proxy.user_id == user_id))
    await session.commit()


async def get_proxy_count(session: AsyncSession, user_id: int) -> int:
    proxies = await get_proxies(session, user_id)
    return len(proxies)


async def get_templates(session: AsyncSession, user_id: int, template_type: str = "") -> list[Template]:
    q = select(Template).where(Template.user_id == user_id)
    if template_type:
        q = q.where(Template.type == template_type)
    return list(await session.scalars(q.order_by(Template.created_at)))


async def get_template_by_id(session: AsyncSession, template_id: int) -> Optional[Template]:
    return await session.scalar(select(Template).where(Template.id == template_id))


async def delete_template(session: AsyncSession, template_id: int, user_id: int) -> None:
    tpl = await get_template_by_id(session, template_id)
    if tpl and tpl.user_id == user_id:
        await session.delete(tpl)
        await session.commit()


async def delete_all_templates(session: AsyncSession, user_id: int, template_type: str = "") -> None:
    q = delete(Template).where(Template.user_id == user_id)
    if template_type:
        q = q.where(Template.type == template_type)
    await session.execute(q)
    await session.commit()


async def get_subjects(session: AsyncSession, user_id: int) -> list[Subject]:
    return list(await session.scalars(select(Subject).where(Subject.user_id == user_id).order_by(Subject.created_at)))


async def get_subject_by_id(session: AsyncSession, subject_id: int) -> Optional[Subject]:
    return await session.scalar(select(Subject).where(Subject.id == subject_id))


async def delete_all_subjects(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(Subject).where(Subject.user_id == user_id))
    await session.commit()


async def get_email_accounts(session: AsyncSession, user_id: int, only_valid: bool = False) -> list[EmailAccount]:
    q = select(EmailAccount).where(EmailAccount.user_id == user_id)
    if only_valid:
        q = q.where(EmailAccount.is_valid == True)
    return list(await session.scalars(q.order_by(EmailAccount.created_at)))


async def get_email_account_by_id(session: AsyncSession, account_id: int) -> Optional[EmailAccount]:
    return await session.scalar(select(EmailAccount).where(EmailAccount.id == account_id))


async def delete_email_account(session: AsyncSession, account_id: int, user_id: int) -> None:
    acc = await get_email_account_by_id(session, account_id)
    if acc and acc.user_id == user_id:
        await session.delete(acc)
        await session.commit()


async def delete_all_email_accounts(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(EmailAccount).where(EmailAccount.user_id == user_id))
    await session.commit()


async def get_receive_emails(session: AsyncSession, user_id: int) -> list[ReceiveEmail]:
    return list(await session.scalars(select(ReceiveEmail).where(ReceiveEmail.user_id == user_id).order_by(ReceiveEmail.created_at)))


async def get_receive_email_by_id(session: AsyncSession, rec_id: int) -> Optional[ReceiveEmail]:
    return await session.scalar(select(ReceiveEmail).where(ReceiveEmail.id == rec_id))


async def delete_receive_email(session: AsyncSession, rec_id: int, user_id: int) -> None:
    rec = await get_receive_email_by_id(session, rec_id)
    if rec and rec.user_id == user_id:
        await session.delete(rec)
        await session.commit()


async def get_incoming_message(session: AsyncSession, msg_id: int) -> Optional[IncomingMessage]:
    return await session.scalar(select(IncomingMessage).where(IncomingMessage.id == msg_id))


async def get_parsed_items(session: AsyncSession, user_id: int, status: str = "") -> list[ParsedItem]:
    q = select(ParsedItem).where(ParsedItem.user_id == user_id)
    if status:
        q = q.where(ParsedItem.status == status)
    return list(await session.scalars(q))


async def get_parsed_item_by_nickname(session: AsyncSession, user_id: int, nickname: str) -> Optional[ParsedItem]:
    return await session.scalar(
        select(ParsedItem).where(ParsedItem.user_id == user_id, ParsedItem.nickname == nickname)
    )


async def get_pending_item_count(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count(ParsedItem.id)).where(
            ParsedItem.user_id == user_id, ParsedItem.status == "pending"
        )
    )


async def get_last_parsed_item_with_link(session: AsyncSession, user_id: int) -> Optional[ParsedItem]:
    return await session.scalar(
        select(ParsedItem).where(ParsedItem.user_id == user_id, ParsedItem.link != "").order_by(ParsedItem.id.desc())
    )


async def get_all_user_ids(session: AsyncSession) -> list[int]:
    result = await session.execute(select(UserSettings.user_id))
    return [row[0] for row in result.fetchall()]
