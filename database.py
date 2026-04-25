"""
database.py — Everything related to saving and loading data from SQLite.

IMPROVEMENTS OVER V1:
- Added "currency" column to store the detected currency per product
- Added duplicate check: is_already_tracking(user_id, url)
- Added count_user_products(user_id) for enforcing the 20-product limit
- Schema migration: safely adds the "currency" column to existing databases
  so users who already have a price_tracker.db don't lose their data
"""

import aiosqlite
from config import DATABASE_FILE


async def init_db():
    """
    Create the database table if it doesn't exist.
    Also runs a safe migration to add the 'currency' column
    in case the user has an older version of the database.
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        # Create the main table (only runs if it doesn't exist yet)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                url         TEXT NOT NULL,
                last_price  REAL,
                currency    TEXT DEFAULT '?',   -- NEW: e.g. "USD", "EUR", "?"
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Safe migration: add the currency column to existing databases.
        # "ALTER TABLE ... ADD COLUMN" fails if the column already exists,
        # so we catch that error and silently ignore it.
        try:
            await db.execute("ALTER TABLE tracked_products ADD COLUMN currency TEXT DEFAULT '?'")
        except Exception:
            pass  # Column already exists — that's fine

        await db.commit()


async def add_product(user_id: int, url: str, price: float | None, currency: str = "?"):
    """Save a new product for a user."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "INSERT INTO tracked_products (user_id, url, last_price, currency) VALUES (?, ?, ?, ?)",
            (user_id, url, price, currency),
        )
        await db.commit()


async def is_already_tracking(user_id: int, url: str) -> bool:
    """
    Return True if this user is already tracking this exact URL.
    Used to prevent duplicate entries.
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute(
            "SELECT 1 FROM tracked_products WHERE user_id = ? AND url = ? LIMIT 1",
            (user_id, url),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None  # True = already exists


async def count_user_products(user_id: int) -> int:
    """Return the number of products a user is currently tracking."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM tracked_products WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_user_products(user_id: int) -> list[dict]:
    """Return all products being tracked for a specific user."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row  # Makes rows accessible like dicts
        async with db.execute(
            "SELECT * FROM tracked_products WHERE user_id = ? ORDER BY added_at DESC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_products() -> list[dict]:
    """Return every tracked product across all users (used by price_checker)."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tracked_products") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_price(product_id: int, new_price: float, currency: str = "?"):
    """Update the saved price and currency after a successful check."""
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "UPDATE tracked_products SET last_price = ?, currency = ? WHERE id = ?",
            (new_price, currency, product_id),
        )
        await db.commit()


async def delete_product(product_id: int, user_id: int):
    """
    Remove a product from tracking.
    The user_id check ensures users can only delete their own products.
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "DELETE FROM tracked_products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        )
        await db.commit()
