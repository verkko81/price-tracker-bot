"""
handlers.py — Defines how the bot responds to user messages and commands.

IMPROVEMENTS OVER V1:
- Duplicate check: tells user if they're already tracking a URL
- User limit: blocks tracking more than MAX_PRODUCTS_PER_USER products
- Currency is now shown correctly instead of always hardcoding "$"
- /list output is cleaner and shows currency per product
- /remove gives better feedback when an ID doesn't exist
- All imports updated to use new database and scraper signatures

Commands:
  /start   — Welcome message
  /track   — Start tracking a product URL
  /list    — Show all tracked products
  /remove  — Stop tracking a product
"""

from aiogram import Router
from aiogram.types import Message
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


def format_price(price: float | None, currency: str) -> str:
    """
    Format a price + currency into a readable string.

    Examples:
      format_price(19.99, "USD")  → "$19.99"
      format_price(1299.0, "EUR") → "€1,299.00"
      format_price(None, "?")     → "Price unknown"
    """
    if price is None:
        return "Price unknown"

    # Map ISO codes back to display symbols
    symbols = {
        "USD": "$", "EUR": "€", "GBP": "£",
        "TRY": "₺", "INR": "₹", "JPY": "¥",
        "KRW": "₩", "AUD": "A$", "CAD": "C$",
    }

    symbol = symbols.get(currency, "")

    # Currencies with no cents (whole numbers only)
    no_cents = {"JPY", "KRW"}
    if currency in no_cents:
        return f"{symbol}{price:,.0f}"

    # Standard display: symbol + value with 2 decimal places
    if symbol:
        return f"{symbol}{price:,.2f}"
    elif currency and currency != "?":
        # Show ISO code if we have no symbol for it
        return f"{price:,.2f} {currency}"
    else:
        return f"{price:,.2f}"


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Greet the user when they send /start."""
    await message.answer(
        "👋 Welcome to the <b>Price Tracker Bot</b>!\n\n"
        "I'll watch product prices for you and notify you when they drop.\n\n"
        "📌 <b>Commands:</b>\n"
        "/track &lt;url&gt; — Start tracking a product\n"
        "/list — Show your tracked products\n"
        f"/remove &lt;id&gt; — Stop tracking a product\n\n"
        f"ℹ️ You can track up to <b>{MAX_PRODUCTS_PER_USER} products</b>.",
        parse_mode="HTML",
    )


@router.message(Command("track"))
async def cmd_track(message: Message):
    """
    Handle /track <url>.
    Validates the URL, checks limits, fetches the price, and saves to DB.
    """
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "❗ Please provide a URL after the command.\n"
            "Example: /track https://example.com/product"
        )
        return

    url = parts[1].strip()

    # Basic URL validation
    if not url.startswith("http"):
        await message.answer(
            "❗ That doesn't look like a valid URL.\n"
            "Make sure it starts with <code>https://</code>",
            parse_mode="HTML",
        )
        return

    user_id = message.from_user.id

    # --- Guard 1: Duplicate check ---
    if await is_already_tracking(user_id, url):
        await message.answer(
            "⚠️ You're already tracking this product!\n"
            "Use /list to see all your tracked products."
        )
        return

    # --- Guard 2: Per-user product limit ---
    count = await count_user_products(user_id)
    if count >= MAX_PRODUCTS_PER_USER:
        await message.answer(
            f"⛔ You've reached the limit of <b>{MAX_PRODUCTS_PER_USER} tracked products</b>.\n"
            "Please remove one with /remove &lt;id&gt; before adding a new one.",
            parse_mode="HTML",
        )
        return

    await message.answer("🔍 Fetching the current price, please wait...")

    # get_price() now returns (price, currency) — both can be None
    price, currency = await get_price(url)

    if price is None:
        await message.answer(
            "⚠️ <b>Couldn't find a price on that page.</b>\n"
            "This can happen when:\n"
            "• The site loads prices via JavaScript\n"
            "• The page blocked our request\n\n"
            "I'll still save the link and try again every hour.",
            parse_mode="HTML",
        )
    else:
        price_str = format_price(price, currency)
        await message.answer(
            f"✅ Found price: <b>{price_str}</b>",
            parse_mode="HTML",
        )

    # Save to database (even if price is None — we'll retry later)
    await add_product(user_id=user_id, url=url, price=price, currency=currency or "?")

    await message.answer("📦 Product saved! I'll notify you when the price drops.")


@router.message(Command("list"))
async def cmd_list(message: Message):
    """Show all products the user is currently tracking."""
    products = await get_user_products(message.from_user.id)

    if not products:
        await message.answer(
            "You're not tracking any products yet.\n"
            "Use /track &lt;url&gt; to add one!",
            parse_mode="HTML",
        )
        return

    lines = [f"📋 <b>Your tracked products ({len(products)}/{MAX_PRODUCTS_PER_USER}):</b>\n"]

    for p in products:
        price_str = format_price(p["last_price"], p.get("currency", "?"))
        # Truncate long URLs so the list stays readable
        url_display = p["url"] if len(p["url"]) <= 50 else p["url"][:47] + "..."
        lines.append(
            f"🆔 <b>ID {p['id']}</b> — {price_str}\n"
            f"🔗 <a href=\"{p['url']}\">{url_display}</a>\n"
        )

    lines.append("To stop tracking: /remove &lt;id&gt;")
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("remove"))
async def cmd_remove(message: Message):
    """Handle /remove <id> — stop tracking a product."""
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer(
            "❗ Please provide a product ID.\n"
            "Example: /remove 3\n\n"
            "Use /list to see your product IDs."
        )
        return

    product_id = int(parts[1].strip())

    # Verify the product belongs to this user before deleting
    user_products = await get_user_products(message.from_user.id)
    owned_ids = {p["id"] for p in user_products}

    if product_id not in owned_ids:
        await message.answer(
            f"❌ No product with ID <b>{product_id}</b> found in your list.\n"
            "Use /list to check your product IDs.",
            parse_mode="HTML",
        )
        return

    await delete_product(product_id=product_id, user_id=message.from_user.id)
    await message.answer(f"🗑️ Product <b>#{product_id}</b> removed from tracking.", parse_mode="HTML")
