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
MIN_DISCOUNT = 15.0  # Nu op 15% om te testen! Zet na een succesvolle test op 50.0 of 60.0

MAX_ALERTS_PER_RUN = 3
BASE_URL = "https://outlet.mediamarkt.be/"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
MEMORY_FILE = "gevonden_deals.txt"

def load_seen_deals() -> set:
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_deal(url: str):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{url}\n")

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
    """Haalt slim kortingspercentages én oude/nieuwe prijzen uit de ruwe tekst van een productblok."""
    discount_pct = 0.0
    old_price = 0.0
    new_price = 0.0

    # 1. Zoek naar expliciete percentages in de tekst (bijv. "-20%" of "25 %")
    pct_matches = re.findall(r"-\s*(\d+(?:[.,]\d+)?)\s*%|(\d+(?:[.,]\d+)?)\s*%", text)
    for m in pct_matches:
        val_str = m[0] or m[1]
        val = parse_eu_number(val_str)
        if 5.0 <= val <= 95.0:
            discount_pct = max(discount_pct, val)

    # 2. Zoek naar alle bedragen in de tekst (bijv. € 499,00 of 499,99 € of 499.-)
    price_matches = re.findall(r"(?:€\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}|[.,]-))(?:\s*€)?|€\s*(\d+(?:[.,]\d+)?)", text)
    prices = []
    for pm in price_matches:
        raw_p = (pm[0] or pm[1]).replace(",-", "").replace(".-", "")
        p_val = parse_eu_number(raw_p)
        if 5.0 <= p_val <= 15000.0:
            prices.append(p_val)

    # Als we minstens 2 bedragen in één productblok zien, is de hoogste de oude prijs en de laagste de outletprijs
    unique_prices = sorted(list(set(prices)))
    if len(unique_prices) >= 2:
        new_price = unique_prices[0]
        old_price = unique_prices[-1]
        if old_price > new_price:
            calc_pct = ((old_price - new_price) / old_price) * 100.0
            if calc_pct <= 95.0:
                discount_pct = max(discount_pct, calc_pct)
    elif len(unique_prices) == 1:
        new_price = unique_prices[0]

    return old_price, new_price, discount_pct

def send_discord_message(payload: dict):
    if not DISCORD_WEBHOOK_URL:
        print("❌ FOUT: DISCORD_WEBHOOK secret is leeg! Controleer Settings -> Secrets and variables -> Actions.")
        return
    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"   [DISCORD] Verzonden! HTTP Status: {res.status_code}")
    except Exception as e:
        print(f"   [DISCORD FOUT] {e}")

def send_discord_deal(title: str, old_price: float, new_price: float, discount: float, url: str):
    old_str = f"€ {old_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if old_price > 0 else "Zie site"
    new_str = f"€ {new_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if new_price > 0 else "Zie site"

    if discount >= 50.0:
        header = f"🚨 KORTINGSLIMIET VERHOOGD: -{discount:.1f}% DEAL! 🚨"
        ping = f"@everyone 🔥 **MediaMarkt Outlet zit nu op -{discount:.1f}% korting!**"
        color = 16711680
    else:
        header = f"✅ DEAL GEVONDEN: -{discount:.1f}% Korting!"
        ping = f"⚡ **Deal gevonden met -{discount:.1f}% korting (Filter: >={MIN_DISCOUNT:.0f}%)**"
        color = 16753920

    embed = {
        "title": header,
        "description": f"**[{title}]({url})**\n\n👉 **[Open direct op MediaMarkt Outlet]({url})**",
        "url": url,
        "color": color,
        "fields": [
            {"name": "🏷️ Oude Prijs", "value": f"~~{old_str}~~", "inline": True},
            {"name": "💶 Outlet Prijs", "value": f"**{new_str}**", "inline": True},
            {"name": "📉 Korting", "value": f"**-{discount:.1f}%**", "inline": True}
        ],
        "footer": {"text": f"MediaMarkt Outlet Monitor • {datetime.now().strftime('%H:%M:%S')}"}
    }
    send_discord_message({"username": "MediaMarkt Outlet Bot", "content": ping, "embeds": [embed]})

async def scan_page_products(page, seen_deals: set, alerts_sent: int) -> int:
    # Zoek alle mogelijke productcontainers op de pagina
    cards = await page.locator(".product-layout, .product-thumb, .product-item, .product-card, article, .product").all()
    print(f"-> {len(cards)} productblokken gevonden op pagina: {page.url}")

    for card in cards:
        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break
        try:
            text = await card.inner_text()
            if not text or len(text.strip()) < 10:
                continue

            # Zoek productlink
            link_el = card.locator("a[href*='product'], h4 a, .name a, a[href]").first
            if await link_el.count() == 0:
                continue
            href = await link_el.get_attribute("href")
            if not href:
                continue
            product_url = urljoin(BASE_URL, href)

            # Alleen bij echte deals (>35%) blokkeren we duplicaten, tijdens testmodus (<35%) laten we ze altijd door!
            if MIN_DISCOUNT >= 35.0 and product_url in seen_deals:
                continue

            # Zoek titel
            title_el = card.locator("h4 a, .name a, h2, h3, .caption a, a[href*='product']").first
            title = (await title_el.inner_text()).strip() if await title_el.count() > 0 else text.split("\n")[0].strip()
            title = re.sub(r"\s+", " ", title)[:120]

            old_price, new_price, discount_pct = analyze_card_text(text)

            if discount_pct > 0:
                print(f"   [CHECK] {title[:40]}... -> Oude prijs: {old_price} | Nieuw: {new_price} | Korting: -{discount_pct:.1f}%")

            if discount_pct >= MIN_DISCOUNT:
                send_discord_deal(title, old_price, new_price, discount_pct, product_url)
                if MIN_DISCOUNT >= 35.0:
                    save_seen_deal(product_url)
                    seen_deals.add(product_url)
                alerts_sent += 1
        except Exception:
            continue

    return alerts_sent

async def main():
    print(f"=== START SCAN (Filter: >= {MIN_DISCOUNT}%) ===")
    seen_deals = load_seen_deals()
    alerts_sent = 0

    # Als je aan het testen bent (MIN_DISCOUNT < 35%), stuur altijd een test-signaal naar Discord
    if MIN_DISCOUNT < 35.0:
        send_discord_message({
            "username": "MediaMarkt Outlet Bot",
            "content": f"🛠️ **Test-modus actief (Filter: >={MIN_DISCOUNT:.0f}%)!** De GitHub-server is nu de MediaMarkt Outlet aan het scannen..."
        })

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="nl-BE", timezone_id="Europe/Brussels"
        )
        page = await context.new_page()

        print("1. Voorpagina openen...")
        await page.goto(BASE_URL, wait_until="networkidle", timeout=45000)
        await asyncio.sleep(2.0)

        print("Pagina titel:", await page.title())
        alerts_sent = await scan_page_products(page, seen_deals, alerts_sent)

        # Check maximaal 4 categorieën als we nog geen 3 deals op het voorblad hadden
        if alerts_sent < MAX_ALERTS_PER_RUN:
            links = await page.locator("a[href*='route=product/category'], nav a[href], .menu a[href]").all()
            categories = []
            for link in links:
                href = await link.get_attribute("href")
                if href:
                    full_url = urljoin(BASE_URL, href)
                    if full_url.startswith(BASE_URL) and full_url != BASE_URL and full_url not in categories:
                        categories.append(full_url)

            print(f"Gevonden categorie-links: {len(categories)}")
            for cat_url in categories[:4]:
                if alerts_sent >= MAX_ALERTS_PER_RUN:
                    break
                try:
                    await page.goto(cat_url, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(1.5)
                    alerts_sent = await scan_page_products(page, seen_deals, alerts_sent)
                except Exception:
                    continue

        await browser.close()
        print(f"=== KLAAR! Totaal {alerts_sent} deal-melding(en) verstuurd. ===")

if __name__ == "__main__":
    asyncio.run(main())
