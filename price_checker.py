"""
price_checker.py — Runs every hour to check if any prices have dropped.

IMPROVEMENTS OVER V1:
- asyncio.sleep(1) between each product check to avoid hammering servers
- Ignores price changes smaller than MIN_DROP_PERCENT (default 1%)
  so users aren't spammed by tiny rounding fluctuations
- Now reads and saves currency alongside the price
- Alert message shows correct currency symbol instead of always "$"
- Handles the new (price, currency) tuple returned by get_price()
"""

import asyncio
import logging
from aiogram import Bot

from config import MIN_DROP_PERCENT
from database import get_all_products, update_price
from scraper import get_price
from handlers import format_price  # Reuse the same formatting helper

logger = logging.getLogger(__name__)

# Seconds to wait between checking each product.
# This prevents us from sending too many requests too fast.
DELAY_BETWEEN_CHECKS = 1.0


async def check_all_prices(bot: Bot):
    """
    Go through every tracked product, fetch its current price,
    and notify the user if the price dropped by more than MIN_DROP_PERCENT.

    Called automatically every hour by the scheduler in bot.py.
    """
    products = await get_all_products()
    logger.info(f"[Price Checker] Starting check for {len(products)} product(s)...")

    for product in products:
        product_id = product["id"]
        user_id    = product["user_id"]
        url        = product["url"]
        old_price  = product["last_price"]       # float or None
        old_currency = product.get("currency", "?")

        # Fetch current price — returns (float, str) or (None, None)
        new_price, new_currency = await get_price(url)

        if new_price is None:
            logger.warning(f"[Price Checker] No price found for product #{product_id} — skipping")
            # Don't update the DB; keep the last known price intact
            await asyncio.sleep(DELAY_BETWEEN_CHECKS)
            continue

        # Use the new currency if we got one; fall back to the old one
        currency = new_currency if new_currency and new_currency != "?" else old_currency

        logger.info(
            f"[Price Checker] Product #{product_id}: "
            f"old={old_price} → new={new_price} {currency}"
        )

        if old_price is None:
            # First successful price fetch for this product — just save it, no alert
            await update_price(product_id, new_price, currency)

        elif new_price < old_price:
            drop_amount  = old_price - new_price
            drop_percent = (drop_amount / old_price) * 100

            if drop_percent < MIN_DROP_PERCENT:
                # Change is too small (probably a rounding difference) — ignore it
                logger.info(
                    f"[Price Checker] Product #{product_id}: drop of {drop_percent:.2f}% "
                    f"is below threshold ({MIN_DROP_PERCENT}%) — not alerting"
                )
                await update_price(product_id, new_price, currency)

            else:
                # Real price drop — notify the user!
                old_str = format_price(old_price, currency)
                new_str = format_price(new_price, currency)
                save_str = format_price(drop_amount, currency)

                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"🔔 <b>Price Drop Alert!</b>\n\n"
                            f"💸 <b>Was:</b> {old_str}\n"
                            f"✅ <b>Now:</b> {new_str}\n"
                            f"📉 <b>You save:</b> {save_str} ({drop_percent:.1f}% off)\n\n"
                            f"🔗 {url}"
                        ),
                        parse_mode="HTML",
                    )
                    logger.info(
                        f"[Price Checker] ✅ Alert sent to user {user_id} "
                        f"for product #{product_id} ({drop_percent:.1f}% drop)"
                    )
                except Exception as e:
                    # Don't crash the whole loop if one message fails to send
                    logger.error(f"[Price Checker] Failed to send alert to {user_id}: {e}")

                await update_price(product_id, new_price, currency)

        else:
            # Price is the same or went up — update silently
            await update_price(product_id, new_price, currency)

        # Small pause between each product to be a polite scraper
        await asyncio.sleep(DELAY_BETWEEN_CHECKS)

    logger.info("[Price Checker] ✅ Done.")
