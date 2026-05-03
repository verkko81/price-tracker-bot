"""
database.py — SQLite база для price tracker bot.
Хранит товары, пользователей и статистику.
"""

import aiosqlite
from config import DATABASE_FILE


async def init_db():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_products (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                url         TEXT NOT NULL,
                last_price  REAL,
                currency    TEXT DEFAULT '?',
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        try:
            await db.execute("ALTER TABLE tracked_products ADD COLUMN currency TEXT DEFAULT '?'")
        except Exception:
            pass

        await db.commit()


async def add_product(user_id: int, url: str, price: float | None, currency: str = "?"):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "INSERT INTO tracked_products (user_id, url, last_price, currency) VALUES (?, ?, ?, ?)",
            (user_id, url, price, currency),
        )
        await db.commit()


async def is_already_tracking(user_id: int, url: str) -> bool:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute(
            "SELECT 1 FROM tracked_products WHERE user_id = ? AND url = ? LIMIT 1",
            (user_id, url),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def count_user_products(user_id: int) -> int:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM tracked_products WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_user_products(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tracked_products WHERE user_id = ? ORDER BY added_at DESC",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_all_products() -> list[dict]:
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tracked_products") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def update_price(product_id: int, new_price: float, currency: str = "?"):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "UPDATE tracked_products SET last_price = ?, currency = ? WHERE id = ?",
            (new_price, currency, product_id),
        )
        await db.commit()


async def delete_product(product_id: int, user_id: int):
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(
            "DELETE FROM tracked_products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        )
        await db.commit()


async def get_admin_stats() -> dict:
    """
    Статистика для админки:
    - всего пользователей
    - всего товаров
    - среднее количество товаров на пользователя
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        cursor = await db.execute("SELECT COUNT(DISTINCT user_id) FROM tracked_products")
        users = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM tracked_products")
        products = (await cursor.fetchone())[0]

        avg_products = round(products / users, 2) if users > 0 else 0

        return {
            "users": users,
            "products": products,
            "avg_products": avg_products,
        }