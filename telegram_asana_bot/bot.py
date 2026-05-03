from __future__ import annotations

import logging
import re
from enum import IntEnum, auto

from asana_client import AsanaAPIError, AsanaClient
from config import Settings, load_settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    Defaults,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class Step(IntEnum):
    WORKSPACE = auto()
    PROJECT = auto()
    SECTION = auto()
    ASSIGNEE = auto()
    TITLE = auto()


def _short_label(name: str, max_len: int = 56) -> str:
    name = (name or "").strip() or "(без названия)"
    return name if len(name) <= max_len else name[: max_len - 1] + "…"


def _keyboard_from_items(
    items: list[dict],
    *,
    prefix: str,
    page: int,
    per_page: int = 8,
) -> InlineKeyboardMarkup:
    total = len(items)
    start = page * per_page
    chunk = items[start : start + per_page]
    rows: list[list[InlineKeyboardButton]] = []
    for i, item in enumerate(chunk):
        idx = start + i
        label = _short_label(str(item.get("name", "")))
        rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}:{idx}")])

    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️ назад", callback_data=f"page:{prefix}:{page - 1}"))
    if start + per_page < total:
        nav.append(InlineKeyboardButton("вперёд ➡️", callback_data=f"page:{prefix}:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return
    uid = update.effective_user.id
    await update.message.reply_text(
        f"Твой Telegram user id: `{uid}`\n"
        f"Впиши его в файл .env в строку TELEGRAM_ALLOWED_USER_IDS (можно несколько через запятую).",
        parse_mode="Markdown",
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Привет! Я создаю задачи в Asana.\n\n"
        "Команды:\n"
        "• /new — новая задача (проект → колонка → ответственный → текст)\n"
        "• /cancel — отменить текущий сценарий\n"
        "• /myid — показать твой Telegram id (если настраиваешь доступ)\n",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Ок, отменила. Начни снова командой /new.")
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Ок, отменила. Начни снова командой /new.")
    context.user_data.clear()
    return ConversationHandler.END


async def new_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings: Settings = context.bot_data["settings"]
    client: AsanaClient = context.bot_data["asana"]

    context.user_data.clear()
    context.user_data["draft"] = {}

    if update.message:
        await update.message.reply_text("Секунду, загружаю данные из Asana…")

    try:
        if settings.asana_workspace_gid:
            context.user_data["draft"]["workspace_gid"] = settings.asana_workspace_gid
            return await _goto_projects(update, context)

        workspaces = await client.list_workspaces_for_me()
        if not workspaces:
            if update.message:
                await update.message.reply_text(
                    "Не нашла workspace в Asana. Проверь токен и что аккаунт не пустой."
                )
            return ConversationHandler.END

        if len(workspaces) == 1:
            context.user_data["draft"]["workspace_gid"] = str(workspaces[0]["gid"])
            return await _goto_projects(update, context)

        context.user_data["workspaces"] = workspaces
        context.user_data["ws_page"] = 0
        text = "Выбери **рабочее пространство** Asana (workspace):"
        markup = _keyboard_from_items(workspaces, prefix="ws", page=0)
        await _reply_or_edit(update, text, markup, parse_mode="Markdown")
        return Step.WORKSPACE
    except AsanaAPIError as exc:
        await _reply_error(update, str(exc))
        return ConversationHandler.END
    except Exception as exc:  # noqa: BLE001
        logger.exception("new_entry failed")
        await _reply_error(update, f"Ошибка: {exc}")
        return ConversationHandler.END


async def on_workspace_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.WORKSPACE
    await query.answer()
    m = re.match(r"^page:ws:(\d+)$", query.data)
    if not m:
        return Step.WORKSPACE
    page = int(m.group(1))
    items = context.user_data.get("workspaces") or []
    markup = _keyboard_from_items(items, prefix="ws", page=page)
    context.user_data["ws_page"] = page
    await query.edit_message_text(
        "Выбери **рабочее пространство** Asana (workspace):",
        reply_markup=markup,
        parse_mode="Markdown",
    )
    return Step.WORKSPACE


async def on_workspace_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.WORKSPACE
    await query.answer()
    m = re.match(r"^ws:(\d+)$", query.data)
    if not m:
        return await on_workspace_page(update, context)
    idx = int(m.group(1))
    items = context.user_data.get("workspaces") or []
    if idx < 0 or idx >= len(items):
        await query.edit_message_text("Кнопка устарела. Нажми /new ещё раз.")
        return ConversationHandler.END
    context.user_data["draft"]["workspace_gid"] = str(items[idx]["gid"])
    return await _goto_projects(update, context)


async def _goto_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    client: AsanaClient = context.bot_data["asana"]
    ws = str(context.user_data["draft"]["workspace_gid"])
    try:
        projects = await client.list_projects(ws)
        if not projects:
            await _reply_or_edit(
                update,
                "В этом workspace нет проектов (или нет доступа). Создай проект в Asana и попробуй /new снова.",
                None,
            )
            context.user_data.clear()
            return ConversationHandler.END
        context.user_data["projects"] = projects
        context.user_data["proj_page"] = 0
        markup = _keyboard_from_items(projects, prefix="proj", page=0)
        await _reply_or_edit(
            update,
            "Выбери **проект** в Asana:",
            markup,
            parse_mode="Markdown",
        )
        return Step.PROJECT
    except AsanaAPIError as exc:
        await _reply_error(update, str(exc))
        context.user_data.clear()
        return ConversationHandler.END


async def on_project_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.PROJECT
    await query.answer()
    m = re.match(r"^page:proj:(\d+)$", query.data)
    if not m:
        return Step.PROJECT
    page = int(m.group(1))
    items = context.user_data.get("projects") or []
    markup = _keyboard_from_items(items, prefix="proj", page=page)
    await query.edit_message_text("Выбери **проект** в Asana:", reply_markup=markup, parse_mode="Markdown")
    return Step.PROJECT


async def on_project_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.PROJECT
    await query.answer()
    if query.data.startswith("page:proj:"):
        return await on_project_page(update, context)
    m = re.match(r"^proj:(\d+)$", query.data)
    if not m:
        return Step.PROJECT
    idx = int(m.group(1))
    items = context.user_data.get("projects") or []
    if idx < 0 or idx >= len(items):
        await query.edit_message_text("Кнопка устарела. Нажми /new ещё раз.")
        return ConversationHandler.END

    project_gid = str(items[idx]["gid"])
    context.user_data["draft"]["project_gid"] = project_gid
    context.user_data["draft"]["project_name"] = str(items[idx].get("name", ""))

    client: AsanaClient = context.bot_data["asana"]
    try:
        sections = await client.list_sections(project_gid)
    except AsanaAPIError as exc:
        await _reply_error(update, str(exc))
        context.user_data.clear()
        return ConversationHandler.END

    if not sections:
        context.user_data["draft"]["section_gid"] = None
        return await _goto_assignees(
            update,
            context,
            lead="В этом проекте **нет колонок (секций)**. Задача будет без колонки.",
        )

    context.user_data["sections"] = sections
    markup = _keyboard_from_items(sections, prefix="sec", page=0)
    await _reply_or_edit(
        update,
        "Выбери **колонку** (секцию) в проекте:",
        markup,
        parse_mode="Markdown",
    )
    return Step.SECTION


async def on_section_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.SECTION
    await query.answer()
    m = re.match(r"^page:sec:(\d+)$", query.data)
    if not m:
        return Step.SECTION
    page = int(m.group(1))
    items = context.user_data.get("sections") or []
    markup = _keyboard_from_items(items, prefix="sec", page=page)
    await query.edit_message_text(
        "Выбери **колонку** (секцию) в проекте:",
        reply_markup=markup,
        parse_mode="Markdown",
    )
    return Step.SECTION


async def on_section_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.SECTION
    await query.answer()
    if query.data.startswith("page:sec:"):
        return await on_section_page(update, context)
    m = re.match(r"^sec:(\d+)$", query.data)
    if not m:
        return Step.SECTION
    idx = int(m.group(1))
    items = context.user_data.get("sections") or []
    if idx < 0 or idx >= len(items):
        await query.edit_message_text("Кнопка устарела. Нажми /new ещё раз.")
        return ConversationHandler.END

    context.user_data["draft"]["section_gid"] = str(items[idx]["gid"])
    context.user_data["draft"]["section_name"] = str(items[idx].get("name", ""))

    return await _goto_assignees(update, context)


def _assignee_keyboard(users: list[dict], page: int) -> InlineKeyboardMarkup:
    kb = _keyboard_from_items(users, prefix="asg", page=page)
    kb.inline_keyboard.insert(
        0,
        [InlineKeyboardButton("— без ответственного —", callback_data="asg:none")],
    )
    return kb


async def _goto_assignees(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    lead: str | None = None,
) -> int:
    try:
        await _load_users_if_needed(context)
    except AsanaAPIError as exc:
        await _reply_error(update, str(exc))
        context.user_data.clear()
        return ConversationHandler.END

    users = context.user_data.get("users") or []
    body = "Теперь выбери **ответственного** (люди из твоего workspace в Asana):"
    if len(users) > 8:
        body += "\n\n_Если список длинный — листай кнопками внизу._"
    text = f"{lead}\n\n{body}" if lead else body
    markup = _assignee_keyboard(users, 0)
    await _reply_or_edit(update, text, markup, parse_mode="Markdown")
    return Step.ASSIGNEE


async def _load_users_if_needed(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("users") is not None:
        return
    client: AsanaClient = context.bot_data["asana"]
    ws = str(context.user_data["draft"]["workspace_gid"])
    users = await client.list_users_in_workspace(ws)
    users_sorted = sorted(users, key=lambda u: str(u.get("name", "")).lower())
    context.user_data["users"] = users_sorted


async def on_assignee_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.ASSIGNEE
    await query.answer()
    m = re.match(r"^page:asg:(\d+)$", query.data)
    if not m:
        return Step.ASSIGNEE
    page = int(m.group(1))
    users = context.user_data.get("users") or []
    markup = _assignee_keyboard(users, page)
    await query.edit_message_text(
        "Теперь выбери **ответственного** (люди из твоего workspace в Asana):",
        reply_markup=markup,
        parse_mode="Markdown",
    )
    return Step.ASSIGNEE


async def on_assignee_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return Step.ASSIGNEE

    if query.data.startswith("page:asg:"):
        return await on_assignee_page(update, context)

    await query.answer()

    try:
        await _load_users_if_needed(context)
    except AsanaAPIError as exc:
        await _reply_error(update, str(exc))
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "asg:none":
        context.user_data["draft"]["assignee_gid"] = None
        context.user_data["draft"]["assignee_name"] = None
    else:
        m = re.match(r"^asg:(\d+)$", query.data)
        if not m:
            return Step.ASSIGNEE
        idx = int(m.group(1))
        users = context.user_data.get("users") or []
        if idx < 0 or idx >= len(users):
            await query.edit_message_text("Кнопка устарела. Нажми /new ещё раз.")
            return ConversationHandler.END
        context.user_data["draft"]["assignee_gid"] = str(users[idx]["gid"])
        context.user_data["draft"]["assignee_name"] = str(users[idx].get("name", ""))

    await _reply_or_edit(
        update,
        "Отлично. Теперь **одним сообщением** напиши название задачи (как она появится в Asana):",
        None,
        parse_mode="Markdown",
    )
    return Step.TITLE


async def on_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return Step.TITLE
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("Пустое название не подойдёт. Напиши текст задачи.")
        return Step.TITLE

    draft = context.user_data.get("draft") or {}
    project_gid = draft.get("project_gid")
    ws_gid = draft.get("workspace_gid")
    if not project_gid or not ws_gid:
        await update.message.reply_text("Сессия сброшена. Начни с /new.")
        context.user_data.clear()
        return ConversationHandler.END

    client: AsanaClient = context.bot_data["asana"]
    section_gid = draft.get("section_gid")
    assignee_gid = draft.get("assignee_gid")

    try:
        task = await client.create_task(
            name=title,
            project_gid=str(project_gid),
            assignee_gid=str(assignee_gid) if assignee_gid else None,
        )
        task_gid = str(task["gid"])
        if section_gid:
            await client.add_task_to_section(str(section_gid), task_gid)

        link = task.get("permalink_url") or ""
        extra = f"\nОткрыть: {link}" if link else ""
        await update.message.reply_text(
            "Готово — задача создана в Asana." + extra,
            disable_web_page_preview=True,
        )
    except AsanaAPIError as exc:
        await update.message.reply_text(f"Asana отказала: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("create task failed")
        await update.message.reply_text(f"Ошибка при создании: {exc}")

    context.user_data.clear()
    return ConversationHandler.END


async def _reply_or_edit(
    update: Update,
    text: str,
    markup: InlineKeyboardMarkup | None,
    *,
    parse_mode: str | None = None,
) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode=parse_mode
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)


async def _reply_error(update: Update, text: str) -> None:
    if update.callback_query:
        await update.callback_query.answer(text[:200], show_alert=True)
    elif update.message:
        await update.message.reply_text(text)


def main() -> None:
    settings = load_settings()
    asana = AsanaClient(settings.asana_pat)

    allowed = filters.User(user_id=list(settings.allowed_telegram_user_ids))

    conv = ConversationHandler(
        entry_points=[CommandHandler("new", new_entry, filters=allowed)],
        states={
            Step.WORKSPACE: [
                CallbackQueryHandler(on_workspace_pick, pattern=r"^ws:\d+$"),
                CallbackQueryHandler(on_workspace_page, pattern=r"^page:ws:\d+$"),
            ],
            Step.PROJECT: [
                CallbackQueryHandler(on_project_pick, pattern=r"^(proj:\d+|page:proj:\d+)$"),
            ],
            Step.SECTION: [
                CallbackQueryHandler(on_section_pick, pattern=r"^(sec:\d+|page:sec:\d+)$"),
            ],
            Step.ASSIGNEE: [
                CallbackQueryHandler(
                    on_assignee_pick,
                    pattern=r"^(asg:none|asg:\d+|page:asg:\d+)$",
                ),
            ],
            Step.TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_title),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel, filters=allowed),
        ],
        allow_reentry=True,
    )

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .defaults(Defaults(parse_mode=None))
        .build()
    )
    app.bot_data["settings"] = settings
    app.bot_data["asana"] = asana

    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("start", cmd_start, filters=allowed))
    app.add_handler(conv)

    denied = filters.ChatType.PRIVATE & ~filters.User(
        user_id=list(settings.allowed_telegram_user_ids)
    )

    async def deny(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Нет доступа к этому боту. Команда /myid покажет твой id для настройки."
            )

    app.add_handler(MessageHandler(denied, deny))

    logger.info("Бот запущен. Ctrl+C — остановить.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
