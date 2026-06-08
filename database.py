import sqlite3
import json
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

DB_PATH = "razor_bot.db"

def get_db():
    """Return a new database connection (not async)."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

async def init_db():
    """Create tables if not exist (must be called at startup)."""
    conn = get_db()
    cursor = conn.cursor()
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            plan TEXT DEFAULT 'Bronze',
            expiry TEXT,
            banned INTEGER DEFAULT 0,
            joined INTEGER DEFAULT 0
        )
    ''')
    # Sites table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sites (
            user_id INTEGER,
            site TEXT,
            PRIMARY KEY (user_id, site)
        )
    ''')
    # Proxies table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS proxies (
            user_id INTEGER,
            ip TEXT,
            port TEXT,
            username TEXT,
            password TEXT,
            proxy_url TEXT,
            type TEXT,
            PRIMARY KEY (user_id, proxy_url)
        )
    ''')
    # Cards table (hits)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card TEXT,
            status TEXT,
            response TEXT,
            gateway TEXT,
            price TEXT,
            date TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Global sites (for fallback)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS global_sites (
            site TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

# ---------- User helpers ----------
async def ensure_user(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, plan, expiry) VALUES (?, ?, ?)", (user_id, "Bronze", None))
    conn.commit()
    conn.close()

async def get_user_plan(user_id: int) -> str:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row["plan"] if row else "Bronze"

async def set_user_plan(user_id: int, plan: str, duration_days: int):
    expiry = (datetime.now() + timedelta(days=duration_days)).isoformat() if duration_days > 0 else None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET plan = ?, expiry = ? WHERE user_id = ?", (plan, expiry, user_id))
    conn.commit()
    conn.close()

async def is_premium_user(user_id: int) -> bool:
    plan = await get_user_plan(user_id)
    return plan.lower() not in ("bronze", "free")

async def is_banned_user(user_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row["banned"])

async def mark_user_joined(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET joined = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

async def is_user_marked_joined(user_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT joined FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row and row["joined"])

async def remove_joined_mark(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET joined = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ---------- Site helpers ----------
async def add_site_db(user_id: int, site: str) -> bool:
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO sites (user_id, site) VALUES (?, ?)", (user_id, site))
        conn.commit()
        inserted = cursor.rowcount > 0
        conn.close()
        return inserted
    except:
        return False

async def get_user_sites(user_id: int) -> List[str]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT site FROM sites WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row["site"] for row in rows]

async def remove_site_db(user_id: int, site: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sites WHERE user_id = ? AND site = ?", (user_id, site))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

async def get_total_sites_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM sites")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

# ---------- Proxy helpers ----------
async def add_proxy_db(user_id: int, proxy_data: dict) -> bool:
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO proxies (user_id, ip, port, username, password, proxy_url, type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, proxy_data['ip'], proxy_data['port'], proxy_data.get('username'), proxy_data.get('password'), proxy_data['proxy_url'], proxy_data.get('type', 'http')))
        conn.commit()
        conn.close()
        return True
    except:
        return False

async def get_all_user_proxies(user_id: int) -> List[dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ip, port, username, password, proxy_url, type FROM proxies WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    proxies = []
    for r in rows:
        proxies.append({
            'ip': r['ip'],
            'port': r['port'],
            'username': r['username'],
            'password': r['password'],
            'proxy_url': r['proxy_url'],
            'type': r['type']
        })
    return proxies

async def get_proxy_count(user_id: int) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM proxies WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

async def remove_proxy_by_index(user_id: int, index: int) -> dict:
    proxies = await get_all_user_proxies(user_id)
    if 0 <= index < len(proxies):
        removed = proxies[index]
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM proxies WHERE user_id = ? AND proxy_url = ?", (user_id, removed['proxy_url']))
        conn.commit()
        conn.close()
        return removed
    return {}

async def remove_proxy_by_url(user_id: int, proxy_url: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies WHERE user_id = ? AND proxy_url = ?", (user_id, proxy_url))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

async def clear_all_proxies(user_id: int) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies WHERE user_id = ?", (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

async def get_random_proxy(user_id: int) -> Optional[dict]:
    proxies = await get_all_user_proxies(user_id)
    if not proxies:
        return None
    import random
    return random.choice(proxies)

# ---------- Card hits ----------
async def save_card_to_db(card: str, status: str, response: str, gateway: str, price: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO cards (card, status, response, gateway, price)
        VALUES (?, ?, ?, ?, ?)
    ''', (card, status, response, gateway, price))
    conn.commit()
    conn.close()

async def get_total_cards_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM cards")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

async def get_charged_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM cards WHERE status = 'CHARGED'")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

async def get_approved_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM cards WHERE status = 'APPROVED'")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

# ---------- Stats helpers ----------
async def get_total_users() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

async def get_premium_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE plan NOT IN ('Bronze', 'Free')")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

async def get_all_premium_users() -> List[int]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE plan NOT IN ('Bronze', 'Free')")
    rows = cursor.fetchall()
    conn.close()
    return [row["user_id"] for row in rows]

async def get_users_with_sites() -> List[int]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT user_id FROM sites")
    rows = cursor.fetchall()
    conn.close()
    return [row["user_id"] for row in rows]

async def get_sites_per_user() -> Dict[int, int]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, COUNT(*) as cnt FROM sites GROUP BY user_id")
    rows = cursor.fetchall()
    conn.close()
    return {row["user_id"]: row["cnt"] for row in rows}

async def get_all_sites_detail() -> List[dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, site FROM sites ORDER BY user_id")
    rows = cursor.fetchall()
    conn.close()
    return [{"user_id": row["user_id"], "site": row["site"]} for row in rows]

async def get_global_sites() -> List[str]:
    """Return global fallback sites (if any)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT site FROM global_sites")
    rows = cursor.fetchall()
    conn.close()
    return [row["site"] for row in rows]

# Export all functions in a `db` object for compatibility
class DatabaseWrapper:
    def __init__(self):
        self.db = None  # Not used in SQLite version
db = DatabaseWrapper()
