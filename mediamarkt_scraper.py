import asyncio
import os
import re
import random
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ==========================================
# SETTINGS (PAS HIER JE TEST-TIJD EN % AAN)
# ==========================================
MIN_DISCOUNT = 50.0   # Melding bij 50% korting of hoger (50%, 55%, 60%, 70%+)
MAX_ALERTS_PER_RUN = 5

REPORT_HOUR = 18      # <--- TEST-UUR: Staat nu op 18 (zet na het testen op 22)
REPORT_MINUTE = 10    # <--- TEST-MINUUT: Staat nu op 10 voor 18u10 (zet na het testen op 0)

BASE_URL = "https://outlet.mediamarkt.be/"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
MEMORY_FILE = "gevonden_deals.txt"

def load_seen_deals() -> set:
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_deal(key: str):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{key}\n")

def parse_eu_number(val_str: str) -> float:
    clean = val_str.strip().strip(",.-")
    if not clean:
        return 0.0
    if "," in clean and "." in clean:
        if clean.rfind(",") > clean.rfind("."):
            clean = clean.replace(".", "").replace(",", ".")
        else:
            clean = clean.replace(",", "")
    elif "," in clean:
        clean = clean.replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return 0.0

def analyze_card_text(text: str):
    discount_pct = 0.0
    old_price = 0.0
    new_price = 0.0
    
    pct_matches = re.findall(r"-\s*(\d+(?:[.,]\d+)?)\s*%|(\d+(?:[.,]\d+)?)\s*%", text)
    for m in pct_matches:
        val = parse_eu_number(m[0] or m[1])
        if 5.0 <= val <= 95.0:
            discount_pct = max(discount_pct, val)

    price_matches = re.findall(r"(?:€\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}|[.,]-))(?:\s*€)?|€\s*(\d+(?:[.,]\d+)?)", text)
    prices = []
    for pm in price_matches:
        p_val = parse_eu_number((pm[0] or pm[1]).replace(",-", "").replace(".-", ""))
        if 5.0 <= p_val <= 15000.0:
            prices.append(p_val)

    unique_prices = sorted(list(set(prices)))
    if len(unique_prices) >= 2:
        new_price = unique_prices[0]
        old_price = unique_prices[-1]
        if old_price > new_price:
            calc = ((old_price - new_price) / old_price) * 100.0
            if calc <= 95.0:
                discount_pct = max(discount_pct, calc)
    elif len(unique_prices) == 1:
        new_price = unique_prices[0]

    return old_price, new_price, discount_pct

def send_discord_deal(title: str, old_price: float, new_price: float, discount: float, url: str, now_str: str):
    if not DISCORD_WEBHOOK_URL:
        return
    
    old_str = f"€ {old_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if old_price > 0 else "See website"
    new_str = f"€ {new_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if new_price > 0 else "See website"

    if discount >= 70.0:
        header = f"🚨 INSANE DEAL: -{discount:.1f}% OFF!"
        ping = f"@everyone 🔥 **MediaMarkt Outlet just dropped a -{discount:.1f}% deal!**"
        color = 16711680
    else:
        header = f"🔥 MASSIVE DEAL FOUND: -{discount:.1f}% OFF!"
        ping = f"@everyone ⚡ **New -{discount:.1f}% discount found on MediaMarkt Outlet!**"
        color = 16753920

    embed = {
        "title": header,
        "description": f"**[{title}]({url})**\n\n👉 **[Click here to open on MediaMarkt Outlet]({url})**",
        "url": url,
        "color": color,
        "fields": [
            {"name": "🏷️ Old Price", "value": f"~~{old_str}~~", "inline": True},
            {"name": "💶 Outlet Price", "value": f"**{new_str}**", "inline": True},
            {"name": "📉 Discount", "value": f"**-{discount:.1f}%**", "inline": True}
        ],
        "footer": {"text": f"MediaMarkt Outlet Monitor • {now_str}"}
    }
    requests.post(DISCORD_WEBHOOK_URL, json={"username": "MediaMarkt Outlet Bot", "content": ping, "embeds": [embed]}, timeout=10)

def send_daily_report(total_found_today: int, max_seen_discount: float, max_seen_title: str, date_str: str, time_str: str):
    """Stuurt ALTIJD het dagelijkse overzicht op het ingestelde tijdstip (ook als er wel deals waren!)."""
    if not DISCORD_WEBHOOK_URL:
        return

    if total_found_today > 0:
        status_line = f"✅ **Deals found today (>={MIN_DISCOUNT:.0f}%):** `{total_found_today} deal(s) found today!`"
        embed_color = 3066993  # Groen
    else:
        status_line = f"❌ **Deals found today (>={MIN_DISCOUNT:.0f
                                                  
