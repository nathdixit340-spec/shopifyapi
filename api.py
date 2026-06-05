import asyncio
import uuid
from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from autoshopify import AutoShopifyChecker

app = FastAPI(title="Shopify Checker API")
checker = AutoShopifyChecker()

# Concurrency limit – adjust based on your needs
SEMAPHORE = asyncio.Semaphore(10)

# In‑memory storage for user sites/proxies (replace with DB later)
user_sites: Dict[int, List[str]] = {}
user_proxies: Dict[int, List[str]] = {}

# In‑memory storage for mass check tasks
mass_tasks: Dict[str, dict] = {}

# ---------- Request Models ----------
class SingleCheckRequest(BaseModel):
    site: str
    cc: str          # "cc|mm|yy|cvv"
    proxy: Optional[str] = None

class MassCheckRequest(BaseModel):
    sites: List[str]
    cards: List[str]   # each "cc|mm|yy|cvv"
    proxies: Optional[List[str]] = None

# ---------- Single Check ----------
@app.post("/check/single")
async def single_check(req: SingleCheckRequest):
    parts = req.cc.split('|')
    if len(parts) != 4:
        raise HTTPException(400, "Invalid format. Use cc|mm|yy|cvv")
    cc, mm, yy, cvv = parts
    success, response, info = await checker.check_card(req.site, cc, mm, yy, cvv, req.proxy)
    return {
        "success": success,
        "response": response,
        "amount": info.get("amount"),
        "currency": info.get("currency"),
        "gateway": info.get("gateway")
    }

# ---------- Mass Check (background) ----------
@app.post("/check/mass")
async def mass_check(req: MassCheckRequest, background: BackgroundTasks):
    task_id = str(uuid.uuid4())
    mass_tasks[task_id] = {
        "status": "running",
        "total": len(req.cards),
        "processed": 0,
        "results": [],
        "sites": req.sites,
        "proxies": req.proxies or []
    }
    background.add_task(run_mass_check, task_id, req.cards, req.sites, req.proxies)
    return {"task_id": task_id}

@app.get("/check/mass/{task_id}")
async def get_mass_status(task_id: str):
    if task_id not in mass_tasks:
        raise HTTPException(404, "Task not found")
    return mass_tasks[task_id]

async def run_mass_check(task_id: str, cards: List[str], sites: List[str], proxies: List[str]):
    results = []
    site_index = 0
    proxy_index = 0

    async def check_one(card_line: str):
        nonlocal site_index, proxy_index
        async with SEMAPHORE:
            parts = card_line.split('|')
            if len(parts) != 4:
                return {"card": card_line, "error": "Invalid format"}
            cc, mm, yy, cvv = parts
            site = sites[site_index % len(sites)]
            site_index += 1
            proxy = None
            if proxies:
                proxy = proxies[proxy_index % len(proxies)]
                proxy_index += 1
            success, response, info = await checker.check_card(site, cc, mm, yy, cvv, proxy)
            return {
                "card": card_line,
                "success": success,
                "response": response,
                "amount": info.get("amount"),
                "site": site
            }

    # Run all checks concurrently
    pending = [check_one(card) for card in cards]
    for coro in asyncio.as_completed(pending):
        res = await coro
        results.append(res)
        mass_tasks[task_id]["processed"] += 1
        mass_tasks[task_id]["results"] = results

    mass_tasks[task_id]["status"] = "completed"

# ---------- User Site Management ----------
@app.post("/user/{user_id}/sites")
async def add_user_site(user_id: int, site: str):
    if user_id not in user_sites:
        user_sites[user_id] = []
    if site not in user_sites[user_id]:
        user_sites[user_id].append(site)
    return {"status": "ok", "sites": user_sites[user_id]}

@app.delete("/user/{user_id}/sites/{site}")
async def remove_user_site(user_id: int, site: str):
    if user_id in user_sites and site in user_sites[user_id]:
        user_sites[user_id].remove(site)
    return {"status": "ok"}

@app.get("/user/{user_id}/sites")
async def get_user_sites(user_id: int):
    return user_sites.get(user_id, [])

@app.post("/user/{user_id}/proxies")
async def add_user_proxy(user_id: int, proxy: str):
    if user_id not in user_proxies:
        user_proxies[user_id] = []
    if proxy not in user_proxies[user_id]:
        user_proxies[user_id].append(proxy)
    return {"status": "ok", "proxies": user_proxies[user_id]}

@app.delete("/user/{user_id}/proxies/{proxy}")
async def remove_user_proxy(user_id: int, proxy: str):
    if user_id in user_proxies and proxy in user_proxies[user_id]:
        user_proxies[user_id].remove(proxy)
    return {"status": "ok"}

@app.get("/user/{user_id}/proxies")
async def get_user_proxies(user_id: int):
    return user_proxies.get(user_id, [])