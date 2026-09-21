# -*- coding: utf-8 -*-
"""
Телеграм-бот для продажи книги за Telegram Stars.

Поток: /start -> выбор языка -> продающий текст -> бесплатная глава ->
оплата в звёздах -> отправка HTML-файла книги на выбранном языке.
"""
import asyncio
import logging
import os
import sqlite3
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from texts import CHOOSE_LANG, FREE_CHAPTER, T

# ----------------------------------------------------------------- настройки
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PRICE = int(os.getenv("PRICE_STARS", "199"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@your_username")
DB_PATH = os.getenv("DB_PATH", "bot.db")
# Ссылка, где можно купить звёзды дешевле / из России
STARS_URL = os.getenv("STARS_URL", "https://t.me/suastarsbot?start=user-6147195726")

BASE = Path(__file__).parent
BOOKS = {
    "ru": BASE / "books" / "kniga_ru.html",
    "tj": BASE / "books" / "kniga_tj.html",
}
LANGS = ("ru", "tj")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bookbot")
router = Router()


# ------------------------------------------------------------------ база данных
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                lang TEXT NOT NULL,
                stars INTEGER NOT NULL,
                charge_id TEXT UNIQUE NOT NULL,
                created_at INTEGER NOT NULL
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_seen INTEGER NOT NULL
            )"""
        )


def touch_user(user_id: int):
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
            (user_id, int(time.time())),
        )


def add_purchase(user_id, username, lang, stars, charge_id):
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO purchases (user_id, username, lang, stars, charge_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, lang, stars, charge_id, int(time.time())),
        )


def has_purchase(user_id: int, lang: str) -> bool:
    with db() as c:
        row = c.execute(
            "SELECT 1 FROM purchases WHERE user_id = ? AND lang = ?", (user_id, lang)
        ).fetchone()
    return row is not None


def find_by_charge(charge_id: str):
    with db() as c:
        return c.execute(
            "SELECT * FROM purchases WHERE charge_id = ?", (charge_id,)
        ).fetchone()


def delete_purchase(charge_id: str):
    with db() as c:
        c.execute("DELETE FROM purchases WHERE charge_id = ?", (charge_id,))


def get_stats():
    with db() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        n, stars = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(stars), 0) FROM purchases"
        ).fetchone()
        by_lang = c.execute(
            "SELECT lang, COUNT(*) AS n FROM purchases GROUP BY lang"
        ).fetchall()
    return users, n, stars, {r["lang"]: r["n"] for r in by_lang}


# ------------------------------------------------------------------- клавиатуры
def fmt(text: str) -> str:
    return text.replace("{price}", str(PRICE))


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="🇹🇯 Тоҷикӣ", callback_data="lang:tj"),
            ]
        ]
    )


def stars_button(lang: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=T[lang]["btn_stars"], url=STARS_URL)


def main_keyboard(lang: str, bought: bool) -> InlineKeyboardMarkup:
    t = T[lang]
    rows = []
    if bought:
        rows.append([InlineKeyboardButton(text=t["btn_get"], callback_data=f"get:{lang}")])
    else:
        rows.append([InlineKeyboardButton(text=t["btn_free"], callback_data=f"free:{lang}")])
        rows.append([InlineKeyboardButton(text=fmt(t["btn_buy"]), callback_data=f"buy:{lang}")])
        rows.append([stars_button(lang)])
    rows.append([InlineKeyboardButton(text=t["btn_lang"], callback_data="chooselang")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=fmt(T[lang]["btn_buy"]), callback_data=f"buy:{lang}")],
            [stars_button(lang)],
        ]
    )


# ------------------------------------------------------------------ отправка книги
async def send_book(bot: Bot, chat_id: int, lang: str):
    t = T[lang]
    await bot.send_message(chat_id, t["paid"])
    await bot.send_document(
        chat_id,
        FSInputFile(BOOKS[lang], filename=t["filename"]),
        caption=t["caption"],
        protect_content=True,  # запрет пересылки и сохранения средствами Telegram
    )


# ---------------------------------------------------------------------- хендлеры
@router.message(CommandStart())
async def cmd_start(message: Message):
    touch_user(message.from_user.id)
    await message.answer(CHOOSE_LANG, reply_markup=lang_keyboard())


@router.callback_query(F.data == "chooselang")
async def cb_choose_lang(cb: CallbackQuery):
    await cb.message.answer(CHOOSE_LANG, reply_markup=lang_keyboard())
    await cb.answer()


@router.callback_query(F.data.startswith("lang:"))
async def cb_lang(cb: CallbackQuery):
    lang = cb.data.split(":", 1)[1]
    if lang not in LANGS:
        return await cb.answer()
    bought = has_purchase(cb.from_user.id, lang)
    await cb.message.answer(fmt(T[lang]["sales"]), reply_markup=main_keyboard(lang, bought))
    await cb.answer()


@router.callback_query(F.data.startswith("free:"))
async def cb_free(cb: CallbackQuery):
    lang = cb.data.split(":", 1)[1]
    if lang not in LANGS:
        return await cb.answer()
    await cb.message.answer(FREE_CHAPTER[lang])
    await cb.message.answer(fmt(T[lang]["after_free"]), reply_markup=buy_keyboard(lang))
    await cb.answer()


@router.callback_query(F.data.startswith("get:"))
async def cb_get(cb: CallbackQuery, bot: Bot):
    lang = cb.data.split(":", 1)[1]
    if lang in LANGS and has_purchase(cb.from_user.id, lang):
        await send_book(bot, cb.message.chat.id, lang)
    await cb.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(cb: CallbackQuery, bot: Bot):
    lang = cb.data.split(":", 1)[1]
    if lang not in LANGS:
        return await cb.answer()

    # уже купил — просто отправляем файл снова
    if has_purchase(cb.from_user.id, lang):
        await send_book(bot, cb.message.chat.id, lang)
        return await cb.answer()

    t = T[lang]
    await bot.send_invoice(
        chat_id=cb.message.chat.id,
        title=t["inv_title"],
        description=t["inv_desc"],
        payload=f"book_{lang}",
        currency="XTR",  # Telegram Stars
        prices=[LabeledPrice(label=t["inv_label"], amount=PRICE)],
        # provider_token для звёзд не нужен
    )
    # подсказка: где купить звёзды дешевле / из России
    await bot.send_message(
        cb.message.chat.id,
        t["stars_hint"],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[stars_button(lang)]]),
    )
    await cb.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    lang = query.invoice_payload.replace("book_", "", 1)
    ok = (
        query.invoice_payload.startswith("book_")
        and lang in LANGS
        and query.currency == "XTR"
        and query.total_amount == PRICE
    )
    await query.answer(ok=ok, error_message=None if ok else T["ru"]["bad_payment"])


@router.message(F.successful_payment)
async def on_paid(message: Message, bot: Bot):
    pay = message.successful_payment
    lang = pay.invoice_payload.replace("book_", "", 1)
    if lang not in LANGS:
        lang = "ru"
    user = message.from_user

    add_purchase(user.id, user.username, lang, pay.total_amount, pay.telegram_payment_charge_id)
    log.info("Покупка: user=%s lang=%s stars=%s", user.id, lang, pay.total_amount)

    try:
        await send_book(bot, message.chat.id, lang)
    except Exception:
        log.exception("Не удалось отправить книгу user=%s", user.id)
        if ADMIN_ID:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ Оплата прошла, но книга не отправилась!\n"
                f"user_id: {user.id}, @{user.username}, lang: {lang}",
            )

    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"💰 Новая покупка: {pay.total_amount} ⭐\n"
                f"Пользователь: {user.full_name} (@{user.username}, id {user.id})\n"
                f"Язык: {lang}\n"
                f"charge_id: <code>{pay.telegram_payment_charge_id}</code>",
            )
        except Exception:
            log.exception("Не удалось уведомить админа")


# Telegram требует, чтобы бот с платежами отвечал на /paysupport
@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message):
    text = "\n\n".join(T[l]["support"].replace("{contact}", SUPPORT_CONTACT) for l in LANGS)
    await message.answer(text)


# ------------------------------------------------------------ команды администратора
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    users, n, stars, by_lang = get_stats()
    await message.answer(
        f"📊 <b>Статистика</b>\n"
        f"Пользователей: {users}\n"
        f"Покупок: {n}\n"
        f"Звёзд получено: {stars} ⭐\n"
        f"🇷🇺 RU: {by_lang.get('ru', 0)} | 🇹🇯 TJ: {by_lang.get('tj', 0)}"
    )


@router.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject, bot: Bot):
    """/refund <charge_id> — вернуть звёзды покупателю (только админ)."""
    if message.from_user.id != ADMIN_ID:
        return
    charge_id = (command.args or "").strip()
    row = find_by_charge(charge_id) if charge_id else None
    if not row:
        return await message.answer("Использование: /refund &lt;charge_id&gt;\nID есть в уведомлении о покупке.")
    try:
        await bot.refund_star_payment(
            user_id=row["user_id"], telegram_payment_charge_id=charge_id
        )
        delete_purchase(charge_id)
        await message.answer(f"✅ Возврат выполнен: {row['stars']} ⭐ → user {row['user_id']}")
    except Exception as e:
        await message.answer(f"❌ Ошибка возврата: {e}")


# ------------------------------------------------------------------------- запуск
async def main():
    if not BOT_TOKEN:
        raise SystemExit("Переменная BOT_TOKEN не задана. Добавь её в Railway → Variables.")
    for lang, path in BOOKS.items():
        if not path.exists():
            raise SystemExit(f"Не найден файл книги: {path}")

    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    log.info("Бот запущен: @%s, цена %s ⭐", me.username, PRICE)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
