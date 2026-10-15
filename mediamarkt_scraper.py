import asyncio
import os
import re
import random
from datetime import datetime
import requests
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ==========================================
# INSTELLINGEN
# ==========================================
MIN_DISCOUNT = 50.0  # Pakt alles vanaf 50% en hoger!
MAX_ALERTS_PER_RUN = 5

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

def send_discord_deal(title: str, old_price: float, new_price: float, discount: float, url: str):
    if not DISCORD_WEBHOOK_URL:
        return
    
    old_str = f"€ {old_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if old_price > 0 else "Zie site"
    new_str = f"€ {new_price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if new_price > 0 else "Zie site"

    # Nu 50%+ de standaard is, maken we de 70%+ deals extra opvallend
    if discount >= 70.0:
        header = f"🚨 BIZARRE DEAL: -{discount:.1f}% KORTING!"
        ping = f"@everyone 🔥 **MediaMarkt Outlet dumpt dit voor -{discount:.1f}%!**"
        color = 16711680
    else:
        header = f"✅ DEAL GEVONDEN: -{discount:.1f}% Korting!"
        ping = f"⚡ **Dikke deal gevonden (-{discount:.1f}%)!**"
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
    requests.post(DISCORD_WEBHOOK_URL, json={"username": "MediaMarkt Bot", "content": ping, "embeds": [embed]}, timeout=10)

async def scan_page_products(page, target_url: str, seen_deals: set, alerts_sent: int) -> int:
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)
        await page.evaluate("window.scrollBy(0, 800)")
        await page.wait_for_timeout(1500)
    except Exception as e:
        print(f"   [!] Fout bij openen {target_url}: {e}")
        return alerts_sent

    cards = await page.locator("div.product-layout, div.product-thumb, div.product, div:has(> div > a:has-text('Meer info')), div:has(> a:has-text('Meer info'))").all()
    print(f"   -> {len(cards)} producten ontdekt op {target_url.split('&path=')[-1] if '&path=' in target_url else 'Voorpagina'}")

    for card in cards:
        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break
        try:
            text = await card.inner_text()
            if not text or len(text.strip()) < 10:
                continue

            link_el = card.locator("a[href*='product'], a:has-text('Meer info'), a:has-text('Winkelwagen'), a[href]").first
            if await link_el.count() == 0:
                continue
            href = await link_el.get_attribute("href")
            product_url = urljoin(BASE_URL, href) if href else BASE_URL

            # Bij de echte 50% scan blokkeren we duplicaten strikt!
            if product_url in seen_deals:
                continue

            title_match = re.split(r'\n', text.strip())
            title = title_match[0] if title_match else "MediaMarkt Product"
            if "Als nieuw" in title or "Korting" in title or "€" in title or "-" in title[:3]:
                title = title_match[1] if len(title_match) > 1 else title
            title = re.sub(r"\s+", " ", title)[:120]

            old_price, new_price, discount_pct = analyze_card_text(text)

            if discount_pct >= MIN_DISCOUNT:
                print(f"   🔥 {discount_pct:.1f}% KORTING GEVONDEN: {title}")
                send_discord_deal(title, old_price, new_price, discount_pct, product_url)
                save_seen_deal(product_url)
                seen_deals.add(product_url)
                alerts_sent += 1
        except Exception:
            continue

    return alerts_sent

async def main():
    print(f"=== START RANDOM DIEPTE-SCAN (Filter: >= {MIN_DISCOUNT}%) ===")
    seen_deals = load_seen_deals()
    alerts_sent = 0

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="nl-BE", timezone_id="Europe/Brussels"
        )
        page = await context.new_page()

        print("1. Categorieën ophalen vanaf voorpagina...")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
        
        links = await page.locator("a[href*='route=product/category'], nav a[href], .menu a[href]").all()
        categories = []
        for link in links:
            href = await link.get_attribute("href")
            if href:
                full_url = urljoin(BASE_URL, href)
                if full_url.startswith(BASE_URL) and full_url != BASE_URL and full_url not in categories:
                    categories.append(full_url)

        # We pakken 3 willekeurige categorieën uit de hele lijst
        random.shuffle(categories)
        target_cats = categories[:3]
        
        # We voegen ook bewust de voorpagina toe om recente flitsdeals niet te missen
        target_cats.append(BASE_URL)

        for cat_url in target_cats:
            if alerts_sent >= MAX_ALERTS_PER_RUN:
                break
            
            # Bepaal willekeurige paginanummers om diep in het assortiment te prikken (ouder voorraad)
            # Voorblad krijgt uiteraard geen paginanummer, categorieën krijgen 2 willekeurige pagina's
            if cat_url == BASE_URL:
                random_pages = [1]
            else:
                random_pages = [random.randint(1, 3), random.randint(4, 9)]

            for page_num in set(random_pages):
                if alerts_sent >= MAX_ALERTS_PER_RUN:
                    break
                
                scan_url = f"{cat_url}&page={page_num}" if "?" in cat_url and page_num > 1 else cat_url
                print(f"\n[SCANNEN] Categorie URL: {scan_url}")
                alerts_sent = await scan_page_products(page, scan_url, seen_deals, alerts_sent)

        await browser.close()
        print(f"\n=== KLAAR! Totaal {alerts_sent} deal-melding(en) >50% verstuurd. ===")

if __name__ == "__main__":
    asyncio.run(main())
