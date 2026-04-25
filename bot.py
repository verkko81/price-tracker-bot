"""
bot.py — Main entry point. Starts the bot and the background price checker.

No structural changes from V1. Minor improvement:
- Logging format now includes timestamps so terminal output is easier to read.
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN
from database import init_db
from handlers import router
from price_checker import check_all_prices

# Set up logging with timestamps so you can see when events happened
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)


async def main():
    # Create the database tables (safe to call on every start — won't overwrite data)
    await init_db()

    # Create the bot and dispatcher
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    # Register all command handlers from handlers.py
    dp.include_router(router)

    # Schedule the hourly price check
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_all_prices,   # Function to call
        "interval",         # Run repeatedly on an interval
        hours=1,            # Every 1 hour
        args=[bot],         # Pass the bot instance so it can send messages
    )
    scheduler.start()

    print("✅ Bot is running! Press Ctrl+C to stop.")

    # Start listening for messages from Telegram
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
