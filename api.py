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

MAX_CONCURRENT = 2
MAX_RETRIES = 2
RETRY_DELAY = 2

mass_tasks = {}
user_sites = {}
user_proxies = {}

# Error strings that indicate a site is NOT valid
SITE_ERROR_INDICATORS = [
    "Site Error - Cannot access products",
    "Connection error",
    "Error parsing shipping response",
    "Error processing card",
    "Failed to get session token",
    "Timeout",
    "CAPTCHA",
    "Not a Shopify site",
    "No products found"
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
        return jsonify({'error': 'Invalid format'}), 400
    cc_num, mm, yy, cvv = parts
    success, response, info = run_async(checker.check_card(site, cc_num, mm, yy, cvv, proxy))
    return jsonify({
        'success': success,
        'response': response,
        'amount': info.get('amount'),
        'currency': info.get('currency'),
        'gateway': info.get('gateway')
    })

# ---------- Mass Check ----------
@app.route('/check/mass', methods=['POST'])
def mass_check():
    data = request.json
    sites = data.get('sites')
    cards = data.get('cards')
    proxies = data.get('proxies', [])
    if not sites or not cards:
        return jsonify({'error': 'Missing sites or cards'}), 400

    task_id = str(uuid.uuid4())
    stop_event = threading.Event()
    mass_tasks[task_id] = {
        'status': 'running',
        'total': len(cards),
        'processed': 0,
        'results': [],
        'stop_event': stop_event
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
            for attempt in range(MAX_RETRIES):
                if stop_event.is_set():
                    return {'card': card_line, 'error': 'Stopped'}
                site = sites[(site_idx + attempt) % len(sites)] if attempt > 0 else initial_site
                try:
                    success, response, info = await checker.check_card(site, cc, mm, yy, cvv, proxy)
                    # Only retry on site errors (not on genuine declines)
                    is_site_error = any(err in response for err in SITE_ERROR_INDICATORS)
                    if is_site_error and attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                    return {
                        'card': card_line,
                        'success': success,
                        'response': response,
                        'amount': info.get('amount'),
                        'site': site
                    }
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                    return {
                        'card': card_line,
                        'success': False,
                        'response': f"Error: {str(e)}",
                        'site': site
                    }
            return {'card': card_line, 'error': 'Max retries'}

        async def check_one(index, card_line):
            nonlocal site_idx, proxy_idx
            async with semaphore:
                if stop_event.is_set():
                    return
                site = sites[site_idx % len(sites)]
                site_idx += 1
                proxy = proxies[proxy_idx % len(proxies)] if proxies else None
                if proxies:
                    proxy_idx += 1
                result = await check_with_retry(card_line, site, proxy)
                if stop_event.is_set():
                    return
                results[index] = result
                mass_tasks[task_id]['processed'] += 1
                mass_tasks[task_id]['results'] = [r for r in results if r is not None]

        async def run_all():
            tasks = [check_one(i, card) for i, card in enumerate(cards)]
            await asyncio.gather(*tasks)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_all())
        mass_tasks[task_id]['status'] = 'completed' if not stop_event.is_set() else 'stopped'
        loop.close()

    threading.Thread(target=run_concurrent_checks, daemon=True).start()
    return jsonify({'task_id': task_id})

@app.route('/check/mass/<task_id>', methods=['GET'])
def get_mass_status(task_id):
    if task_id not in mass_tasks:
        return jsonify({'error': 'Task not found'}), 404
    task = {k: v for k, v in mass_tasks[task_id].items() if k not in ['stop_event', 'thread']}
    return jsonify(task)

@app.route('/check/mass/<task_id>', methods=['DELETE'])
def stop_mass_check(task_id):
    if task_id not in mass_tasks:
        return jsonify({'error': 'Task not found'}), 404
    task = mass_tasks[task_id]
    if task['status'] != 'running':
        return jsonify({'error': 'Task already completed/stopped'}), 400
    stop_event = task.get('stop_event')
    if stop_event:
        stop_event.set()
    time.sleep(1)
    return jsonify({
        'status': 'stopped',
        'total': task['total'],
        'processed': task['processed'],
        'results': task['results']
    })

# ---------- Site Testing (batch, auto-adds valid) ----------
@app.route('/user/<int:user_id>/sites/test', methods=['POST'])
def test_and_add_sites():
    data = request.json
    user_id = data.get('user_id')
    sites = data.get('sites')
    test_cc = data.get('test_cc', "4197475861867116|05|2034|500")
    if not sites:
        return jsonify({'error': 'No sites'}), 400
    # Limit batch size to avoid memory/timeout
    if len(sites) > 20:
        return jsonify({'error': 'Too many sites (max 20 per request). Please split into multiple files.'}), 400

    parts = test_cc.split('|')
    if len(parts) != 4:
        return jsonify({'error': 'Invalid test CC format'}), 400
    tc, tm, ty, tcvv = parts

    async def test_one(site):
        site = normalize_site(site)
        try:
            success, response, info = await checker.check_card(site, tc, tm, ty, tcvv, None)
            # Valid if no site error indicators
            is_valid = not any(err in response for err in SITE_ERROR_INDICATORS)
            return (site, is_valid, response[:100])
        except Exception as e:
            return (site, False, str(e)[:100])

    async def test_all():
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        async def limited_test(site):
            async with sem:
                return await test_one(site)
        tasks = [limited_test(s) for s in sites]
        results = await asyncio.gather(*tasks)
        valid = [site for site, ok, _ in results if ok]
        invalid = [{'site': site, 'reason': reason} for site, ok, reason in results if not ok]
        # Auto-add valid sites
        if user_id not in user_sites:
            user_sites[user_id] = []
        for site in valid:
            if site not in user_sites[user_id]:
                user_sites[user_id].append(site)
        return valid, invalid

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        valid, invalid = loop.run_until_complete(test_all())
    except Exception as e:
        loop.close()
        return jsonify({'error': str(e)}), 500
    loop.close()
    return jsonify({'valid_sites': valid, 'invalid_sites': invalid})

# ---------- User Site Management ----------
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
    if user_id in user_sites and site in user_sites[user_id]:
        user_sites[user_id].remove(site)
    return jsonify({'status': 'ok'})

@app.route('/user/<int:user_id>/sites/clear', methods=['DELETE'])
def clear_user_sites(user_id):
    if user_id in user_sites:
        user_sites[user_id] = []
    return jsonify({'status': 'ok'})

# ---------- User Proxy Management ----------
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
