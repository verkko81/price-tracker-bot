"""
config.py — All settings live here. Edit this file to configure your bot.

IMPROVEMENTS OVER V1:
- Added MAX_PRODUCTS_PER_USER to enforce per-user tracking limit
- Added MIN_DROP_PERCENT to ignore tiny rounding-error price changes
"""

import os

# ------------------------------------------------------------------
# Required: your Telegram bot token from @BotFather
# Best practice: set it as an environment variable so it's not
# saved in your code:
#   export BOT_TOKEN="123456:your-token-here"   (Mac/Linux)
#   set BOT_TOKEN=123456:your-token-here        (Windows)
# ------------------------------------------------------------------
BOT_TOKEN ="8734537730:AAG0EUTj9EkBHTBhRVrvBHsad1KvX9fdg2g"


# SQLite database file (created automatically on first run)
DATABASE_FILE = "price_tracker.db"

# Maximum number of products a single user can track at once
MAX_PRODUCTS_PER_USER = 20

# Minimum price drop (in percent) needed to trigger a notification.
# Example: 1.0 means we ignore drops smaller than 1%
# This prevents spam from tiny rounding fluctuations (e.g. $9.99 → $9.98)
MIN_DROP_PERCENT = 1.0
