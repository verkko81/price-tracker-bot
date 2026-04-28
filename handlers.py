from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, CommandStart

from config import MAX_PRODUCTS_PER_USER
from database import (
    add_product,
    get_user_products,
    delete_product,
    is_already_tracking,
    count_user_products,
)
from scraper import get_price

router = Router()

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📦 Мои товары"),
            KeyboardButton(text="🛒 Добавить товар"),
        ],
        [
            KeyboardButton(text="❓ О боте / Помощь"),
            KeyboardButton(text="🌍 Язык"),
        ],
        [
            KeyboardButton(text="💰 Лента скидок"),
        ],
    ],
    resize_keyboard=True
)


def format_price(price: float | None, currency: str) -> str:
    if price is None:
        return "Price unknown"

    symbols = {
        "USD": "$", "EUR": "€", "GBP": "£",
        "TRY": "₺", "INR": "₹", "JPY": "¥",
        "KRW": "₩", "AUD": "A$", "CAD": "C$",
    }

    symbol = symbols.get(currency, "")

    if currency in {"JPY", "KRW"}:
        return f"{symbol}{price:,.0f}"

    if symbol:
        return f"{symbol}{price:,.2f}"

    if currency and currency != "?":
        return f"{price:,.2f} {currency}"

    return f"{price:,.2f}"


async def add_product_by_url(message: Message, url: str):
    if not url.startswith("http"):
        await message.answer("❗ Ссылка должна начинаться с https://")
        return

    user_id = message.from_user.id

    if await is_already_tracking(user_id, url):
        await message.answer("⚠️ Этот товар уже отслеживается.\nНажми 📦 Мои товары.")
        return

    count = await count_user_products(user_id)

    if count >= MAX_PRODUCTS_PER_USER:
        await message.answer(
            f"⛔ Лимит: {MAX_PRODUCTS_PER_USER} товаров.\n"
            "Сначала удали один товар через /remove ID"
        )
        return

    await message.answer("🔍 Проверяю текущую цену, подожди...")

    price, currency = await get_price(url)

    if price is None:
        await message.answer(
            "⚠️ Не смог найти цену на странице.\n\n"
            "Но я всё равно сохраню ссылку и попробую проверить позже."
        )
    else:
        price_str = format_price(price, currency)
        await message.answer(f"✅ Найдена цена: <b>{price_str}</b>", parse_mode="HTML")

    await add_product(user_id=user_id, url=url, price=price, currency=currency or "?")

    await message.answer(
        "📦 Товар сохранён! Я сообщу, если цена упадёт.",
        reply_markup=main_menu
    )


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Добро пожаловать в <b>Price Tracker Bot</b>!\n\n"
        "Я отслеживаю цены товаров и сообщаю, когда цена падает.\n\n"
        "Пришли ссылку на товар или выбери действие в меню 👇",
        parse_mode="HTML",
        reply_markup=main_menu,
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer(
        "Выбери действие 👇",
        reply_markup=main_menu
    )


@router.message(Command("track"))
async def cmd_track(message: Message):
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer("❗ Пришли ссылку после команды.\nПример: /track https://example.com/product")
        return

    await add_product_by_url(message, parts[1].strip())


@router.message(Command("list"))
async def cmd_list(message: Message):
    products = await get_user_products(message.from_user.id)

    if not products:
        await message.answer(
            "📭 У тебя пока нет товаров.\n\n"
            "Нажми 🛒 Добавить товар или просто пришли ссылку.",
            reply_markup=main_menu
        )
        return

    lines = [f"📋 <b>Твои товары ({len(products)}/{MAX_PRODUCTS_PER_USER}):</b>\n"]

    for p in products:
        price_str = format_price(p["last_price"], p.get("currency", "?"))
        url_display = p["url"] if len(p["url"]) <= 50 else p["url"][:47] + "..."

        lines.append(
            f"🆔 <b>ID {p['id']}</b> — {price_str}\n"
            f"🔗 <a href=\"{p['url']}\">{url_display}</a>\n"
        )

    lines.append("Удалить товар: /remove ID")

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=main_menu
    )


@router.message(Command("remove"))
async def cmd_remove(message: Message):
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("❗ Укажи ID товара.\nПример: /remove 3\n\nID можно посмотреть в 📦 Мои товары.")
        return

    product_id = int(parts[1].strip())

    user_products = await get_user_products(message.from_user.id)
    owned_ids = {p["id"] for p in user_products}

    if product_id not in owned_ids:
        await message.answer(f"❌ Товар с ID {product_id} не найден.")
        return

    await delete_product(product_id=product_id, user_id=message.from_user.id)
    await message.answer(f"🗑️ Товар #{product_id} удалён.", reply_markup=main_menu)


@router.message(F.text == "🛒 Добавить товар")
async def button_add_product(message: Message):
    await message.answer("Пришли ссылку на товар 👇")


@router.message(F.text == "📦 Мои товары")
async def button_my_products(message: Message):
    await cmd_list(message)


@router.message(F.text == "❓ О боте / Помощь")
async def button_help(message: Message):
    await message.answer(
        "❓ <b>Как пользоваться ботом</b>\n\n"
        "1. Нажми 🛒 Добавить товар\n"
        "2. Пришли ссылку на товар\n"
        "3. Я сохраню цену и буду проверять её\n\n"
        "Команды:\n"
        "/track ссылка — добавить товар\n"
        "/list — мои товары\n"
        "/remove ID — удалить товар",
        parse_mode="HTML",
        reply_markup=main_menu
    )


@router.message(F.text == "🌍 Язык")
async def button_language(message: Message):
    await message.answer("Пока доступен русский язык 🇷🇺")


@router.message(F.text == "💰 Лента скидок")
async def button_deals(message: Message):
    await message.answer("💰 Лента скидок пока в разработке 🚀")


@router.message(F.text.startswith("http"))
async def handle_plain_url(message: Message):
    await add_product_by_url(message, message.text.strip())