import aiosqlite

DB_NAME = "users.db"


async def init_db():
    """Создаёт таблицы при первом запуске"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                free_generation_used BOOLEAN DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await db.execute('''
            CREATE TABLE IF NOT EXISTS deposit_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                status TEXT DEFAULT 'pending',
                screenshot_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        await db.commit()


async def register_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user_id, username or "None", first_name or "None"))
        await db.commit()


async def get_user_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_free_generation_status(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT free_generation_used FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] == 1 if row else False


async def use_free_generation(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET free_generation_used = 1 WHERE user_id = ?', (user_id,))
        await db.commit()


async def deduct_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
        await db.commit()


async def add_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
        await db.commit()


async def create_deposit_request(user_id: int, amount: int, screenshot_id: str) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            INSERT INTO deposit_requests (user_id, amount, screenshot_id)
            VALUES (?, ?, ?)
        ''', (user_id, amount, screenshot_id))
        await db.commit()
        return cursor.lastrowid


async def get_pending_requests():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''
            SELECT request_id, user_id, amount, screenshot_id, created_at 
            FROM deposit_requests 
            WHERE status = 'pending'
            ORDER BY created_at
        ''') as cursor:
            return await cursor.fetchall()


async def confirm_request(request_id: int, user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE deposit_requests SET status = "approved" WHERE request_id = ?', (request_id,))
        await db.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
        await db.commit()


async def reject_request(request_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE deposit_requests SET status = "rejected" WHERE request_id = ?', (request_id,))
        await db.commit()


async def get_user_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            total_users = (await cursor.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM users WHERE free_generation_used = 1') as cursor:
            free_used = (await cursor.fetchone())[0]
        return {"total_users": total_users, "free_used": free_used, "free_left": total_users - free_used}