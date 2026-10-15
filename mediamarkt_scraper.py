import asyncio
import os
import re
from datetime import datetime
import requests
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ==========================================
# PAS HIER JE KORTINGSPERCENTAGE AAN:
# ==========================================
MIN_DISCOUNT = 15.0  # Nu op 15% om Discord te testen! Zet straks op 50.0 of 60.0

# Maximaal aantal meldingen per scan-ronde (voorkomt Discord-spam tijdens het testen!)
MAX_ALERTS_PER_RUN = 3

BASE_URL = "https://outlet.mediamarkt.be/"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
MEMORY_FILE = "gevonden_deals.txt"

SELECTORS = {
    "category_links": "a[href*='route=product/category']",
    "product_card": ".product-layout, .product-thumb, .product-item, .product-card, article",
    "title": "h4 a, .caption h4, .name a, h2, h3, .product-title",
    "link": "h4 a, .name a, .image a, a[href*='route=product/product'], a[href]",
    "old_price": ".price-old, .old-price, del, s, .strike-price",
    "new_price": ".price-new, .new-price, .special-price, .current-price, .price",
    "discount_badge": ".sale, .discount, .badge-discount, .percentage, .save-badge, .label-danger"
}

def load_seen_deals() -> set:
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_deal(url: str):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{url}\n")

def parse_price(price_str: str) -> float:
    if not price_str:
        return 0.0
    match = re.search(r"(\d[\d.,]*)", price_str)
    if not match:
        return 0.0
    clean = match.group(1).strip(",.")
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

def parse_percentage(badge_str: str) -> float:
    if not badge_str:
        return 0.0
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", badge_str)
    if match:
        return float(match.group(1).replace(",", "."))
    return 0.0

def send_discord_notification(title: str, old_price: float, new_price: float, discount: float, url: str):
    if not DISCORD_WEBHOOK_URL:
        print("[FOUT] Geen DISCORD_WEBHOOK gevonden in GitHub Secrets!")
        return

    old_price_str = f"€ {old_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if old_price > 0 else "Onbekend"
    new_price_str = f"€ {new_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if new_price > 0 else "Zie website"

    if discount >= 50.0:
        header_title = f"🚨 KORTINGSLIMIET VERHOOGD: -{discount:.1f}% DEAL! 🚨"
        ping_text = f"@everyone 🔥 **MediaMarkt Outlet zit nu op -{discount:.1f}% korting!**"
        embed_color = 16711680
    else:
        header_title = f"✅ TEST / DEAL: -{discount:.1f}% Korting!"
        ping_text = f"⚡ **Deal gevonden met -{discount:.1f}% korting (Filter staat op >={MIN_DISCOUNT:.0f}%)**"
        embed_color = 16753920

    embed = {
        "title": header_title,
        "description": f"**[{title}]({url})**\n\n👉 **[Klik hier om de Outlet te openen]({url})**",
        "url": url,
        "color": embed_color,
        "fields": [
            {"name": "🏷️ Oude Prijs", "value": f"~~{old_price_str}~~", "inline": True},
            {"name": "💶 Nieuwe Prijs", "value": f"**{new_price_str}**", "inline": True},
            {"name": "📉 Korting", "value": f"**-{discount:.1f}%**", "inline": True}
        ],
        "footer": {"text": f"Snelle MediaMarkt Monitor • {datetime.now().strftime('%H:%M:%S')}"}
    }

    payload = {"username": "MediaMarkt Outlet Bot", "content": ping_text, "embeds": [embed]}
    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"[DISCORD] Melding verstuurd voor {title} (-{discount:.1f}%) -> Status: {res.status_code}")
    except Exception as e:
        print(f"[DISCORD FOUT] {e}")

async def extract_products_from_page(page, seen_deals: set, alerts_sent: int) -> int:
    cards = await page.locator(SELECTORS["product_card"]).all()
    print(f"-> {len(cards)} producten zichtbaar op deze pagina.")

    for card in cards:
        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break
        try:
            link_el = card.locator(SELECTORS["link"]).first
            if await link_el.count() == 0:
                continue
            href = await link_el.get_attribute("href")
            if not href:
                continue
            product_url = urljoin(BASE_URL, href)
            if product_url in seen_deals:
                continue

            title_el = card.locator(SELECTORS["title"]).first
            title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else "MediaMarkt Product"
            title = re.sub(r"\s+", " ", title)

            old_price, new_price, discount_pct = 0.0, 0.0, 0.0

            old_el = card.locator(SELECTORS["old_price"]).first
            if await old_el.count() > 0:
                old_price = parse_price(await old_el.inner_text())

            new_el = card.locator(SELECTORS["new_price"]).first
            if await new_el.count() > 0:
                new_price = parse_price(await new_el.inner_text())

            badge_el = card.locator(SELECTORS["discount_badge"]).first
            if await badge_el.count() > 0:
                discount_pct = parse_percentage(await badge_el.inner_text())

            if old_price > 0 and new_price > 0 and old_price > new_price:
                calculated_discount = ((old_price - new_price) / old_price) * 100.0
                discount_pct = max(discount_pct, calculated_discount)

            if discount_pct >= MIN_DISCOUNT:
                send_discord_notification(title, old_price, new_price, discount_pct, product_url)
                save_seen_deal(product_url)
                seen_deals.add(product_url)
                alerts_sent += 1
        except Exception:
            continue

    return alerts_sent

async def main():
    seen_deals = load_seen_deals()
    alerts_sent = 0

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="nl-BE", timezone_id="Europe/Brussels"
        )
        page = await context.new_page()

        print("1. Voorpagina scannen...")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.5)
        alerts_sent = await extract_products_from_page(page, seen_deals, alerts_sent)

        # Als we nog geen 3 deals op de voorpagina hebben gevonden, checken we maximaal 4 hoofdcategorieën (alleen pagina 1!)
        if alerts_sent < MAX_ALERTS_PER_RUN:
            links = await page.locator(SELECTORS["category_links"]).all()
            categories = []
            for link in links:
                href = await link.get_attribute("href")
                if href:
                    full_url = urljoin(BASE_URL, href)
                    if full_url.startswith(BASE_URL) and full_url not in categories:
                        categories.append(full_url)

            for cat_url in categories[:4]:
                if alerts_sent >= MAX_ALERTS_PER_RUN:
                    break
                print(f"2. Snelle check van categorie: {cat_url}")
                try:
                    await page.goto(cat_url, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(1.0)
                    alerts_sent = await extract_products_from_page(page, seen_deals, alerts_sent)
                except Exception:
                    continue

        await browser.close()
        print(f"Klaar! Totaal {alerts_sent} melding(en) naar Discord gestuurd.")

if __name__ == "__main__":
    asyncio.run(main())
