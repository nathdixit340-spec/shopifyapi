import os
import requests
import time
import threading
from typing import Dict
import telebot
from telebot import types

API_BASE = os.getenv("API_BASE", "http://localhost:8000")  # set to your Railway URL
BOT_TOKEN = os.getenv("BOT_TOKEN", "8471637595:AAFuQCudc79xugl59YNnYfsxaPp4ZYPTnv4")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Track active mass check tasks per user
user_tasks: Dict[int, str] = {}

# ---------- Helper functions ----------
def api_post(endpoint: str, json_data: dict = None, params: dict = None):
    try:
        if json_data:
            resp = requests.post(f"{API_BASE}{endpoint}", json=json_data, timeout=60)
        else:
            resp = requests.post(f"{API_BASE}{endpoint}", params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def api_get(endpoint: str):
    try:
        resp = requests.get(f"{API_BASE}{endpoint}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def api_delete(endpoint: str):
    try:
        resp = requests.delete(f"{API_BASE}{endpoint}", timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

# ---------- Commands ----------
@bot.message_handler(commands=['start', 'help'])
def send_help(message):
    help_text = """
<b>🛒 Shopify Checker Bot</b>

<b>Site Management:</b>
/seturl <code>site.com</code> – add a Shopify site
/urls – list your sites
/delurl <code>site.com</code> – remove a site

<b>Proxy Management:</b>
/setproxy <code>ip:port</code> or <code>user:pass@ip:port</code>
/proxies – list your proxies
/delproxy <code>proxy</code> – remove a proxy

<b>Checking:</b>
/check <code>cc|mm|yy|cvv</code> – single card check
/mass – reply to a .txt file with cards (one per line)
/stop – stop current mass check

<b>Example:</b>
<code>/check 4111111111111111|12|25|123</code>
    """
    bot.reply_to(message, help_text)

# Sites
@bot.message_handler(commands=['seturl'])
def set_url(message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /seturl example.com")
        return
    site = args[1].strip().lower()
    result = api_post(f"/user/{user_id}/sites", params={"site": site})
    if "error" in result:
        bot.reply_to(message, f"❌ Error: {result['error']}")
    else:
        bot.reply_to(message, f"✅ Site added: {site}")

@bot.message_handler(commands=['urls'])
def list_sites(message):
    user_id = message.from_user.id
    sites = api_get(f"/user/{user_id}/sites")
    if "error" in sites:
        bot.reply_to(message, f"❌ {sites['error']}")
    elif not sites:
        bot.reply_to(message, "📭 No sites configured. Use /seturl")
    else:
        text = "🌐 <b>Your Shopify sites:</b>\n" + "\n".join(f"• {s}" for s in sites)
        bot.reply_to(message, text)

@bot.message_handler(commands=['delurl'])
def del_url(message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /delurl example.com")
        return
    site = args[1].strip().lower()
    result = api_delete(f"/user/{user_id}/sites/{site}")
    if "error" in result:
        bot.reply_to(message, f"❌ {result['error']}")
    else:
        bot.reply_to(message, f"✅ Removed: {site}")

# Proxies
@bot.message_handler(commands=['setproxy'])
def set_proxy(message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /setproxy ip:port or user:pass@ip:port")
        return
    proxy = args[1].strip()
    result = api_post(f"/user/{user_id}/proxies", params={"proxy": proxy})
    if "error" in result:
        bot.reply_to(message, f"❌ {result['error']}")
    else:
        bot.reply_to(message, f"✅ Proxy added: {proxy}")

@bot.message_handler(commands=['proxies'])
def list_proxies(message):
    user_id = message.from_user.id
    proxies = api_get(f"/user/{user_id}/proxies")
    if "error" in proxies:
        bot.reply_to(message, f"❌ {proxies['error']}")
    elif not proxies:
        bot.reply_to(message, "📭 No proxies configured. Use /setproxy")
    else:
        text = "🔌 <b>Your proxies:</b>\n" + "\n".join(f"• {p}" for p in proxies)
        bot.reply_to(message, text)

@bot.message_handler(commands=['delproxy'])
def del_proxy(message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /delproxy ip:port")
        return
    proxy = args[1].strip()
    result = api_delete(f"/user/{user_id}/proxies/{proxy}")
    if "error" in result:
        bot.reply_to(message, f"❌ {result['error']}")
    else:
        bot.reply_to(message, f"✅ Removed: {proxy}")

# Single check
@bot.message_handler(commands=['check'])
def single_check(message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /check cc|mm|yy|cvv")
        return
    cc_data = args[1].strip()
    # Get first site
    sites = api_get(f"/user/{user_id}/sites")
    if "error" in sites or not sites:
        bot.reply_to(message, "❌ No sites configured. Use /seturl")
        return
    site = sites[0]
    # Optionally get a proxy if available
    proxies = api_get(f"/user/{user_id}/proxies")
    proxy = proxies[0] if (not isinstance(proxies, dict) and proxies) else None
    # Send request
    result = api_post("/check/single", json_data={"site": site, "cc": cc_data, "proxy": proxy})
    if "error" in result:
        bot.reply_to(message, f"❌ Error: {result['error']}")
    else:
        status = "✅ APPROVED" if result["success"] else "❌ DECLINED"
        msg = f"<b>Status:</b> {status}\n<b>Card:</b> <code>{cc_data}</code>\n<b>Response:</b> {result['response']}\n<b>Amount:</b> {result.get('amount', '0')} {result.get('currency', 'USD')}"
        bot.reply_to(message, msg)

# Mass check
@bot.message_handler(commands=['mass'])
def mass_check(message):
    user_id = message.from_user.id
    if user_id in user_tasks:
        bot.reply_to(message, "❌ A mass check is already running. Use /stop to cancel.")
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        bot.reply_to(message, "❌ Please reply to a .txt file with cards (one per line: cc|mm|yy|cvv)")
        return
    # Download the file
    try:
        file_info = bot.get_file(message.reply_to_message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        cards = [line.strip() for line in downloaded.decode().splitlines() if line.strip() and '|' in line]
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to read file: {e}")
        return
    if not cards:
        bot.reply_to(message, "❌ No valid cards found.")
        return
    # Get user's sites and proxies
    sites = api_get(f"/user/{user_id}/sites")
    proxies = api_get(f"/user/{user_id}/proxies")
    if "error" in sites or not sites:
        bot.reply_to(message, "❌ No sites configured. Use /seturl")
        return
    # Start mass check on API
    payload = {
        "sites": sites,
        "cards": cards,
        "proxies": proxies if not isinstance(proxies, dict) else []
    }
    result = api_post("/check/mass", json_data=payload)
    if "error" in result:
        bot.reply_to(message, f"❌ Error: {result['error']}")
        return
    task_id = result["task_id"]
    user_tasks[user_id] = task_id
    bot.reply_to(message, f"🔄 Mass check started (ID: {task_id[:8]}). Use /stop to cancel.\nResults will be sent when complete.")
    # Poll in background
    threading.Thread(target=poll_results, args=(message.chat.id, user_id, task_id), daemon=True).start()

def poll_results(chat_id, user_id, task_id):
    while True:
        status = api_get(f"/check/mass/{task_id}")
        if "error" in status:
            bot.send_message(chat_id, f"❌ Polling error: {status['error']}")
            break
        if status["status"] == "completed":
            # Send final results
            total = status["total"]
            results = status["results"]
            approved = sum(1 for r in results if r.get("success") and "Charged" not in r.get("response", ""))
            charged = sum(1 for r in results if "Charged" in r.get("response", ""))
            declined = sum(1 for r in results if not r.get("success"))
            msg = f"✅ <b>Mass Check Complete!</b>\nTotal: {total}\n✅ Approved: {approved}\n🔥 Charged: {charged}\n❌ Declined: {declined}\n\n"
            # Send first 15 results as preview
            for r in results[:15]:
                icon = "🔥" if "Charged" in r.get("response", "") else "✅" if r.get("success") else "❌"
                msg += f"{icon} {r['card'][:12]}… → {r.get('response', '')[:40]}\n"
            if len(results) > 15:
                msg += f"\n… and {len(results)-15} more. Full results in file below."
            bot.send_message(chat_id, msg)
            # Send full file
            full_text = "\n".join([f"{r['card']} → {r['response']}" for r in results])
            bot.send_document(chat_id, document=("mass_results.txt", full_text))
            if user_id in user_tasks:
                del user_tasks[user_id]
            break
        elif status["status"] == "running":
            # Update progress every 5 seconds (optional)
            time.sleep(5)
            continue
        else:
            break

@bot.message_handler(commands=['stop'])
def stop_mass(message):
    user_id = message.from_user.id
    if user_id in user_tasks:
        # API currently has no cancel endpoint – just stop polling locally
        del user_tasks[user_id]
        bot.reply_to(message, "⏹️ Mass check stop requested. (API cancel not implemented, but bot will stop tracking.)")
    else:
        bot.reply_to(message, "❌ No active mass check to stop.")

if __name__ == "__main__":
    bot.infinity_polling()