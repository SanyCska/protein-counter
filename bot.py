from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from openai import OpenAI
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from ai_protein import estimate_protein
from storage import ProteinEntry, ProteinStore

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

(ADD_NAME, ADD_ROUTE, ASK_MANUAL, ASK_MANUAL_CALORIES, ASK_AI, ASK_AI_CONFIRM, ASK_AI_CORRECT) = range(7)

CALLBACK_MANUAL = "prot_manual"
CALLBACK_AI = "prot_ai"
CALLBACK_AI_SAVE = "ai_save"
CALLBACK_AI_CORRECT = "ai_correct"

DELETE_PREFIX = "del_"


def _today() -> date:
    tz_name = (os.environ.get("TZ") or "").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (ValueError, ZoneInfoNotFoundError):
        logger.warning("Invalid TZ=%r, falling back to UTC", tz_name)
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _store(context: ContextTypes.DEFAULT_TYPE) -> ProteinStore:
    path = os.environ.get("PROTEIN_DB_PATH", "data/protein.sqlite3")
    return ProteinStore(path)


def _openai_client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _openai_model() -> str:
    # OPENAI_MODEL may be present but empty in env/.env; guard with fallback.
    return (os.environ.get("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"


def _entry_source_label(source: str) -> str:
    if source == "manual":
        return "вручную"
    if source == "ai_corrected":
        return "ИИ, исправлено"
    return "ИИ"


def _today_message_text(day: date, entries: list[ProteinEntry]) -> str:
    lines = [day.isoformat(), ""]
    total = 0.0
    total_kcal = 0.0
    any_kcal = False
    for e in entries:
        total += e.protein_g
        src = _entry_source_label(e.source)
        if e.calories_kcal is not None:
            any_kcal = True
            total_kcal += e.calories_kcal
            lines.append(
                f"• {e.food_name} — {e.protein_g:g} г белка, {e.calories_kcal:g} ккал ({src})"
            )
        else:
            lines.append(f"• {e.food_name} — {e.protein_g:g} г ({src})")
    lines.append("")
    lines.append(f"Итого: {total:g} г белка")
    if any_kcal:
        lines.append(f"Калории: {total_kcal:g} ккал")
    return "\n".join(lines)


def _today_delete_keyboard(entries: list[ProteinEntry]) -> InlineKeyboardMarkup:
    rows = []
    for e in entries:
        label = e.food_name.strip()
        if len(label) > 40:
            label = label[:39] + "…"
        rows.append(
            [InlineKeyboardButton(f"🗑 {label}", callback_data=f"{DELETE_PREFIX}{e.id}")]
        )
    return InlineKeyboardMarkup(rows)


def _commands_text() -> str:
    return (
        "Доступные команды:\n\n"
        "/start — приветствие\n"
        "/help — этот список команд\n"
        "/add — записать приём пищи (белок и калории: вручную или оценка ИИ по ингредиентам)\n"
        "/today — записи за сегодня, сумма белка и калорий (🗑 — удалить запись)\n"
        "/cancel — отменить текущий шаг в /add"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я помогаю считать дневной белок и калории.\n\n"
        "Полный список команд: /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_commands_text())


def _clear_add_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in (
        "food_name",
        "manual_protein_g",
        "ai_ingredients",
        "ai_estimated_g",
        "ai_estimated_kcal",
        "ai_reason",
    ):
        context.user_data.pop(k, None)


async def add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_add_user_data(context)
    await update.message.reply_text("Что ты ел(а)? Напиши название блюда.")
    return ADD_NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Пришли непустое название.")
        return ADD_NAME
    if text.startswith("/"):
        await update.message.reply_text("Напиши название блюда обычным текстом, не командой.")
        return ADD_NAME
    context.user_data["food_name"] = text
    keyboard = [
        [InlineKeyboardButton("Указать белок самому", callback_data=CALLBACK_MANUAL)],
        [InlineKeyboardButton("Посчитать с ИИ", callback_data=CALLBACK_AI)],
    ]
    await update.message.reply_text(
        "Как определим белок и калории?\n"
        "• Вручную — сначала белок в граммах, потом калории в ккал.\n"
        "• С ИИ — по ингредиентам оценю и белок, и калории.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADD_ROUTE


async def route_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == CALLBACK_MANUAL:
        await query.edit_message_text(
            "Введи количество белка в граммах (например, 25 или 25,5). "
            "После этого спрошу калории."
        )
        return ASK_MANUAL
    if query.data == CALLBACK_AI:
        await query.edit_message_text(
            "Перечисли ингредиенты (количество и единицы помогают точнее оценить)."
        )
        return ASK_AI
    return ConversationHandler.END


def _parse_protein_grams(text: str) -> float | None:
    t = text.strip().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    return float(m.group(1))


async def manual_protein(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text or ""
    grams = _parse_protein_grams(raw)
    if grams is None or grams < 0:
        await update.message.reply_text("Пришли число — белок в граммах (например, 30).")
        return ASK_MANUAL
    food_name = context.user_data.get("food_name")
    if not food_name:
        await update.message.reply_text("Что-то пошло не так. Начни снова с /add.")
        return ConversationHandler.END
    context.user_data["manual_protein_g"] = float(grams)
    await update.message.reply_text(
        "Сколько калорий в этом приёме (ккал)? Например, 450 или 320,5. "
        "Если не хочешь вести калории — напиши 0."
    )
    return ASK_MANUAL_CALORIES


async def manual_calories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text or ""
    kcal = _parse_protein_grams(raw)
    if kcal is None or kcal < 0:
        await update.message.reply_text(
            "Пришли число — калории в ккал (например, 400). Напиши 0, если калории не считаешь."
        )
        return ASK_MANUAL_CALORIES
    food_name = context.user_data.get("food_name")
    grams = context.user_data.get("manual_protein_g")
    if not food_name or grams is None:
        await update.message.reply_text("Что-то пошло не так. Начни снова с /add.")
        return ConversationHandler.END
    store = _store(context)
    day = _today()
    calories_saved: float | None = float(kcal) if kcal > 0 else None
    store.add_entry(
        user_id=update.effective_user.id,
        day=day,
        food_name=food_name,
        protein_g=float(grams),
        source="manual",
        ingredients=None,
        calories_kcal=calories_saved,
    )
    kcal_part = f", {kcal:g} ккал" if kcal > 0 else ""
    await update.message.reply_text(
        f"Сохранено: {food_name} — {float(grams):g} г белка{kcal_part} ({day.isoformat()})."
    )
    _clear_add_user_data(context)
    return ConversationHandler.END


async def ai_ingredients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        await update.message.reply_text(
            "OpenAI не настроен (нет OPENAI_API_KEY). "
            "Укажи ключ в .env или выбери «Указать белок самому»."
        )
        return ConversationHandler.END
    ingredients = (update.message.text or "").strip()
    if len(ingredients) < 3:
        await update.message.reply_text("Опиши ингредиенты чуть подробнее.")
        return ASK_AI
    food_name = context.user_data.get("food_name")
    if not food_name:
        await update.message.reply_text("Что-то пошло не так. Начни снова с /add.")
        return ConversationHandler.END
    await update.message.reply_text("Оцениваю количество белка…")
    try:
        client = _openai_client()
        grams, kcal_est, reason = estimate_protein(
            client,
            food_name=food_name,
            ingredients_text=ingredients,
            model=_openai_model(),
        )
    except Exception as e:
        logger.exception("OpenAI failed")
        await update.message.reply_text(
            f"Не удалось получить оценку ИИ: {e}\nПопробуй снова /add или введи белок вручную."
        )
        return ConversationHandler.END
    day = _today()
    context.user_data["ai_ingredients"] = ingredients
    context.user_data["ai_estimated_g"] = float(grams)
    context.user_data["ai_estimated_kcal"] = kcal_est
    context.user_data["ai_reason"] = reason
    keyboard = [
        [InlineKeyboardButton("Сохранить", callback_data=CALLBACK_AI_SAVE)],
        [InlineKeyboardButton("Исправить", callback_data=CALLBACK_AI_CORRECT)],
    ]
    kcal_line = (
        f"~{kcal_est:g} ккал.\n"
        if kcal_est is not None
        else "Калории: не удалось оценить.\n"
    )
    await update.message.reply_text(
        f"Оценка ИИ: ~{grams:g} г белка, {kcal_line}{reason}\n\n"
        f"Блюдо: {food_name} ({day.isoformat()}).\n"
        "Сохранить эту оценку или ввести своё количество белка?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_AI_CONFIRM


async def ai_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    food_name = context.user_data.get("food_name")
    grams = context.user_data.get("ai_estimated_g")
    ingredients = context.user_data.get("ai_ingredients")
    kcal_est = context.user_data.get("ai_estimated_kcal")
    if not food_name or grams is None or ingredients is None:
        await query.edit_message_text("Что-то пошло не так. Начни снова с /add.")
        _clear_add_user_data(context)
        return ConversationHandler.END
    day = _today()
    if query.data == CALLBACK_AI_SAVE:
        store = _store(context)
        store.add_entry(
            user_id=update.effective_user.id,
            day=day,
            food_name=food_name,
            protein_g=float(grams),
            source="ai",
            ingredients=ingredients,
            calories_kcal=kcal_est if isinstance(kcal_est, (int, float)) else None,
        )
        kcal_saved = kcal_est if isinstance(kcal_est, (int, float)) else None
        kcal_suffix = f", {float(kcal_saved):g} ккал" if kcal_saved is not None else ""
        await query.edit_message_text(
            f"Сохранено: {food_name} — {float(grams):g} г белка{kcal_suffix} ({day.isoformat()}), оценка ИИ."
        )
        _clear_add_user_data(context)
        return ConversationHandler.END
    if query.data == CALLBACK_AI_CORRECT:
        await query.edit_message_text(
            "Введи верное количество белка в граммах (например, 25 или 25,5)."
        )
        return ASK_AI_CORRECT
    return ConversationHandler.END


async def ai_correct_protein(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text or ""
    grams = _parse_protein_grams(raw)
    if grams is None or grams < 0:
        await update.message.reply_text("Пришли число — белок в граммах (например, 30).")
        return ASK_AI_CORRECT
    food_name = context.user_data.get("food_name")
    ingredients = context.user_data.get("ai_ingredients")
    if not food_name or ingredients is None:
        await update.message.reply_text("Что-то пошло не так. Начни снова с /add.")
        _clear_add_user_data(context)
        return ConversationHandler.END
    kcal_est = context.user_data.get("ai_estimated_kcal")
    kcal_save = kcal_est if isinstance(kcal_est, (int, float)) else None
    store = _store(context)
    day = _today()
    store.add_entry(
        user_id=update.effective_user.id,
        day=day,
        food_name=food_name,
        protein_g=grams,
        source="ai_corrected",
        ingredients=ingredients,
        calories_kcal=kcal_save,
    )
    kcal_suffix = f", {float(kcal_save):g} ккал" if kcal_save is not None else ""
    await update.message.reply_text(
        f"Сохранено: {food_name} — {grams:g} г белка{kcal_suffix} ({day.isoformat()}), белок вручную после ИИ."
    )
    _clear_add_user_data(context)
    return ConversationHandler.END


async def ai_confirm_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Нажми «Сохранить» или «Исправить» на сообщении с оценкой ИИ.")
    return ASK_AI_CONFIRM


async def add_route_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Нажми одну из кнопок выше (вручную или ИИ).")
    return ADD_ROUTE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_add_user_data(context)
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store = _store(context)
    day = _today()
    uid = update.effective_user.id
    entries = store.entries_for_day(uid, day)
    if not entries:
        await update.message.reply_text(f"За {day.isoformat()} пока нет записей.")
        return
    text = _today_message_text(day, entries)
    await update.message.reply_text(text, reply_markup=_today_delete_keyboard(entries))


async def delete_today_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith(DELETE_PREFIX):
        return
    try:
        entry_id = int(query.data[len(DELETE_PREFIX) :])
    except ValueError:
        await query.answer("Некорректная кнопка.", show_alert=True)
        return
    store = _store(context)
    uid = update.effective_user.id
    day = _today()
    deleted = store.delete_entry(uid, entry_id)
    if not deleted:
        await query.answer("Запись не найдена или уже удалена.", show_alert=True)
        return
    await query.answer("Удалено")
    entries = store.entries_for_day(uid, day)
    if not entries:
        await query.edit_message_text(f"За {day.isoformat()} пока нет записей.")
        return
    text = _today_message_text(day, entries)
    await query.edit_message_text(text, reply_markup=_today_delete_keyboard(entries))


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Приветствие"),
            BotCommand("help", "Список команд"),
            BotCommand("add", "Добавить приём пищи"),
            BotCommand("today", "Записи за сегодня"),
            BotCommand("cancel", "Отменить шаг в /add"),
        ]
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Укажите TELEGRAM_BOT_TOKEN в окружении или в .env")
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning(
            "OPENAI_API_KEY не задан — «Посчитать с ИИ» не будет работать, пока не добавите ключ."
        )

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_entry)],
        states={
            ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_name),
            ],
            ADD_ROUTE: [
                CallbackQueryHandler(route_choice, pattern=f"^({CALLBACK_MANUAL}|{CALLBACK_AI})$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_route_reminder),
            ],
            ASK_MANUAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_protein),
            ],
            ASK_MANUAL_CALORIES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, manual_calories),
            ],
            ASK_AI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ai_ingredients),
            ],
            ASK_AI_CONFIRM: [
                CallbackQueryHandler(
                    ai_confirm,
                    pattern=f"^({CALLBACK_AI_SAVE}|{CALLBACK_AI_CORRECT})$",
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ai_confirm_reminder),
            ],
            ASK_AI_CORRECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ai_correct_protein),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="add_conversation",
    )

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(
        CallbackQueryHandler(delete_today_entry, pattern=f"^{re.escape(DELETE_PREFIX)}\\d+$")
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(conv)

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
