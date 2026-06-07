import asyncio
import uuid
import threading
import time
import logging
from flask import Flask, request, jsonify
from autoshopify import AutoShopifyChecker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
checker = AutoShopifyChecker()

MAX_CONCURRENT = 5
MAX_RETRIES = 3          # Retry failed cards up to 3 times
RETRY_DELAY = 2          # Seconds between retries

mass_tasks = {}
user_sites = {}
user_proxies = {}

# Error patterns that trigger a retry on a different site
RETRYABLE_ERRORS = [
    "Error parsing shipping response",
    "Site Error - Cannot access products",
    "Error processing card",
    "Site not supported",
    "totalTaxAmount",
    "429",
    "CAPTCHA"
]

def normalize_site(site: str) -> str:
    site = site.lower().strip()
    for prefix in ['https://', 'http://']:
        if site.startswith(prefix):
            site = site[len(prefix):]
    return site.rstrip('/')

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# ---------- Single Check ----------
@app.route('/check/single', methods=['POST'])
def single_check():
    data = request.json
    site = data.get('site')
    cc = data.get('cc')
    proxy = data.get('proxy')
    parts = cc.split('|')
    if len(parts) != 4:
        return jsonify({'error': 'Invalid format. Use cc|mm|yy|cvv'}), 400
    cc_num, mm, yy, cvv = parts
    success, response, info = run_async(checker.check_card(site, cc_num, mm, yy, cvv, proxy))
    return jsonify({
        'success': success,
        'response': response,
        'amount': info.get('amount'),
        'currency': info.get('currency'),
        'gateway': info.get('gateway')
    })

# ---------- Mass Check with Retry & Site Rotation ----------
@app.route('/check/mass', methods=['POST'])
def mass_check():
    data = request.json
    sites = data.get('sites')
    cards = data.get('cards')
    proxies = data.get('proxies', [])
    if not sites or not cards:
        return jsonify({'error': 'Missing sites or cards'}), 400

    task_id = str(uuid.uuid4())
    mass_tasks[task_id] = {
        'status': 'running',
        'total': len(cards),
        'processed': 0,
        'results': []
    }

    def run_concurrent_checks():
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        results = [None] * len(cards)
        site_idx = 0
        proxy_idx = 0

        async def check_with_retry(card_line, initial_site, proxy):
            cc_parts = card_line.split('|')
            if len(cc_parts) != 4:
                return {'card': card_line, 'error': 'Invalid format'}

            cc, mm, yy, cvv = cc_parts
            # Try with different sites from the list
            for attempt in range(MAX_RETRIES):
                # Rotate site on each attempt (if more than one site available)
                site = sites[(site_idx + attempt) % len(sites)] if attempt > 0 else initial_site
                try:
                    success, response, info = await checker.check_card(site, cc, mm, yy, cvv, proxy)
                    # Check if response contains retryable error
                    if any(err in response for err in RETRYABLE_ERRORS):
                        logger.info(f"Retryable error on {site}: {response[:50]}. Attempt {attempt+1}/{MAX_RETRIES}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY)
                            continue
                    # If success or non‑retryable, return result
                    return {
                        'card': card_line,
                        'success': success,
                        'response': response,
                        'amount': info.get('amount'),
                        'site': site,
                        'attempts': attempt + 1
                    }
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                    else:
                        return {
                            'card': card_line,
                            'success': False,
                            'response': f"Error: {str(e)}",
                            'amount': None,
                            'site': site,
                            'attempts': attempt + 1
                        }
            # Fallback (should not reach here)
            return {'card': card_line, 'error': 'Max retries exceeded'}

        async def check_one(index, card_line):
            nonlocal site_idx, proxy_idx
            async with semaphore:
                # Initial site from rotation
                site = sites[site_idx % len(sites)]
                site_idx += 1
                proxy = None
                if proxies:
                    proxy = proxies[proxy_idx % len(proxies)]
                    proxy_idx += 1

                result = await check_with_retry(card_line, site, proxy)
                results[index] = result
                mass_tasks[task_id]['processed'] += 1
                mass_tasks[task_id]['results'] = [r for r in results if r is not None]

        async def run_all():
            tasks = [check_one(i, card) for i, card in enumerate(cards)]
            await asyncio.gather(*tasks)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_all())
        mass_tasks[task_id]['status'] = 'completed'
        loop.close()

    threading.Thread(target=run_concurrent_checks, daemon=True).start()
    return jsonify({'task_id': task_id})

@app.route('/check/mass/<task_id>', methods=['GET'])
def get_mass_status(task_id):
    if task_id not in mass_tasks:
        return jsonify({'error': 'Task not found'}), 404
    return jsonify(mass_tasks[task_id])

# ---------- User Site Management (unchanged) ----------
@app.route('/user/<int:user_id>/sites', methods=['POST'])
def add_user_site(user_id):
    site = request.args.get('site') or request.json.get('site')
    if not site:
        return jsonify({'error': 'Missing site'}), 400
    site = normalize_site(site)
    if user_id not in user_sites:
        user_sites[user_id] = []
    if site not in user_sites[user_id]:
        user_sites[user_id].append(site)
    return jsonify({'status': 'ok', 'sites': user_sites[user_id]})

@app.route('/user/<int:user_id>/sites', methods=['GET'])
def get_user_sites(user_id):
    return jsonify(user_sites.get(user_id, []))

@app.route('/user/<int:user_id>/sites/<path:site>', methods=['DELETE'])
def remove_user_site(user_id, site):
    site = normalize_site(site)
    if user_id in user_sites:
        user_sites[user_id] = [s for s in user_sites[user_id] if s != site]
    return jsonify({'status': 'ok'})

@app.route('/user/<int:user_id>/sites/clear', methods=['DELETE'])
def clear_user_sites(user_id):
    if user_id in user_sites:
        user_sites[user_id] = []
    return jsonify({'status': 'ok'})

@app.route('/user/<int:user_id>/proxies', methods=['POST'])
def add_user_proxy(user_id):
    proxy = request.args.get('proxy') or request.json.get('proxy')
    if not proxy:
        return jsonify({'error': 'Missing proxy'}), 400
    proxy = proxy.strip()
    if user_id not in user_proxies:
        user_proxies[user_id] = []
    if proxy not in user_proxies[user_id]:
        user_proxies[user_id].append(proxy)
    return jsonify({'status': 'ok', 'proxies': user_proxies[user_id]})

@app.route('/user/<int:user_id>/proxies', methods=['GET'])
def get_user_proxies(user_id):
    return jsonify(user_proxies.get(user_id, []))

@app.route('/user/<int:user_id>/proxies/<path:proxy>', methods=['DELETE'])
def remove_user_proxy(user_id, proxy):
    proxy = proxy.strip()
    if user_id in user_proxies and proxy in user_proxies[user_id]:
        user_proxies[user_id].remove(proxy)
    return jsonify({'status': 'ok'})

@app.route('/user/<int:user_id>/proxies/clear', methods=['DELETE'])
def clear_user_proxies(user_id):
    if user_id in user_proxies:
        user_proxies[user_id] = []
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
