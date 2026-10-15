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
# SETTINGS
# ==========================================
MIN_DISCOUNT = 50.0
MAX_ALERTS_PER_RUN = 5

REPORT_HOUR = 22
REPORT_MINUTE = 0

BASE_URL = "https://outlet.mediamarkt.be/"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")
MEMORY_FILE = "gevonden_deals.txt"
TZ = ZoneInfo("Europe/Brussels")

# Badge rouge = texte du type "-15%" ou "-15 %"
BADGE_REGEX = re.compile(r"^-\s*(\d{1,2})\s*%$")
BADGE_IN_TEXT = re.compile(r"(?<![\w.])-\s*(\d{1,2})\s*%")
# Code produit type (CP.MA.00000779.01) : on l'enlève du texte
PRODUCT_CODE = re.compile(r"\(?\bCP\.[A-Z]+\.\d+\.\d+\)?")


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


def extract_prices_for_display(text: str):
    """Prix uniquement pour l'AFFICHAGE (jamais utilisés pour calculer la promo)."""
    clean = PRODUCT_CODE.sub(" ", text)
    found = re.findall(r"€\s*(\d+(?:[.,]\d{3})*(?:[.,]\d{2}|[.,]-)?)", clean)
    prices = [parse_eu_number(p) for p in found]
    prices = [p for p in prices if 1.0 <= p <= 15000.0]
    if not prices:
        return 0.0, 0.0
    if len(prices) == 1:
        return 0.0, prices[0]
    return max(prices), min(prices)


async def read_badge(card):
    """Lit UNIQUEMENT le pourcentage affiché (badge rouge). Retourne None si pas fiable."""
    badges = []

    # 1) Éléments dont le texte entier est "-15%"
    try:
        els = await card.locator("span, div, p, b, strong, small").all()
        for el in els:
            try:
                t = (await el.inner_text(timeout=500)).strip()
            except Exception:
                continue
            m = BADGE_REGEX.match(t)
            if m:
                badges.append(float(m.group(1)))
    except Exception:
        pass

    # 2) Secours : regex sur le texte, sans les codes produit
    if not badges:
        text = PRODUCT_CODE.sub(" ", await card.inner_text())
        badges = [float(x) for x in BADGE_IN_TEXT.findall(text)]

    if not badges:
        return None

    # Si plusieurs badges différents, la "carte" contient plusieurs produits -> on ignore
    if len(set(badges)) > 1:
        return None

    return badges[0]


def send_discord_deal(title, old_price, new_price, discount, url, now_str):
    if not DISCORD_WEBHOOK_URL:
        return

    def fmt(v):
        if v <= 0:
            return "Voir le site"
        return f"€ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    if discount >= 70.0:
        header = f"🚨 INSANE DEAL: -{discount:.0f}% OFF!"
        ping = f"@everyone 🔥 **MediaMarkt Outlet just dropped a -{discount:.0f}% deal!**"
        color = 16711680
    else:
        header = f"🔥 MASSIVE DEAL FOUND: -{discount:.0f}% OFF!"
        ping = f"@everyone ⚡ **New -{discount:.0f}% discount found on MediaMarkt Outlet!**"
        color = 16753920

    embed = {
        "title": header,
        "description": f"**[{title}]({url})**\n\n👉 **[Click here to open on MediaMarkt Outlet]({url})**",
        "url": url,
        "color": color,
        "fields": [
            {"name": "🏷️ Old Price", "value": f"~~{fmt(old_price)}~~", "inline": True},
            {"name": "💶 Outlet Price", "value": f"**{fmt(new_price)}**", "inline": True},
            {"name": "📉 Discount (badge)", "value": f"**-{discount:.0f}%**", "inline": True},
        ],
        "footer": {"text": f"MediaMarkt Outlet Monitor • {now_str}"},
    }
    requests.post(
        DISCORD_WEBHOOK_URL,
        json={"username": "MediaMarkt Outlet Bot", "content": ping, "embeds": [embed]},
        timeout=10,
    )


def send_daily_report(total_found_today, max_seen_discount, max_seen_title, date_str, time_str):
    if not DISCORD_WEBHOOK_URL:
        return

    min_disc_int = int(MIN_DISCOUNT)
    if total_found_today > 0:
        status_line = f"✅ **Deals found today (>={min_disc_int}%):** `{total_found_today} deal(s) found today!`"
        embed_color = 3066993
    else:
        status_line = f"❌ **Deals found today (>={min_disc_int}%):** `0 deals found today`"
        embed_color = 3447003

    embed = {
        "title": f"🌙 Daily Status Report ({REPORT_HOUR:02d}:{REPORT_MINUTE:02d}) — {date_str}",
        "description": (
            f"The bot is **100% active** and scanning smoothly.\n\n"
            f"{status_line}\n"
            f"📊 **Highest discount currently on the Outlet:** `-{max_seen_discount:.0f}%` *(e.g. {max_seen_title[:50]}...)*\n\n"
            f"All systems are fully operational!"
        ),
        "color": embed_color,
        "footer": {"text": f"MediaMarkt 24/7 Cloud Monitor • Report sent at {time_str}"},
    }
    requests.post(
        DISCORD_WEBHOOK_URL,
        json={"username": "MediaMarkt Outlet Bot", "embeds": [embed]},
        timeout=10,
    )
    print("   [DISCORD] ✅ Daily status report sent!")


async def scan_page_products(page, target_url, seen_deals, alerts_sent, highest_tracker, now_str, today_str):
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)
        await page.evaluate("window.scrollBy(0, 700)")
        await page.wait_for_timeout(1500)
    except Exception as e:
        print(f"   [!] Could not open {target_url}: {e}")
        return alerts_sent

    cards = await page.locator("div.product-layout, div.product-thumb, div.product").all()

    for card in cards:
        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break
        try:
            text = await card.inner_text()
            if not text or len(text.strip()) < 10:
                continue

            link_el = card.locator("a[href*='product'], a:has-text('Meer info'), a[href]").first
            if await link_el.count() == 0:
                continue
            href = await link_el.get_attribute("href")
            product_url = urljoin(BASE_URL, href) if href else BASE_URL

            lines = [l for l in re.split(r"\n", text.strip()) if l.strip()]
            title = lines[0] if lines else "MediaMarkt Product"
            if "Als nieuw" in title or "Korting" in title or "€" in title or title.strip().startswith("-"):
                title = lines[1] if len(lines) > 1 else title
            title = re.sub(r"\s+", " ", title)[:120]

            # On LIT simplement le pourcentage affiché, sans aucun calcul
            discount_pct = await read_badge(card)
            if discount_pct is None:
                continue

            if discount_pct > highest_tracker["pct"]:
                highest_tracker["pct"] = discount_pct
                highest_tracker["title"] = title

            if product_url in seen_deals:
                continue

            if discount_pct >= MIN_DISCOUNT:
                old_price, new_price = extract_prices_for_display(text)
                print(f"   🔥 MATCH FOUND: {title} | -{discount_pct:.0f}%")
                send_discord_deal(title, old_price, new_price, discount_pct, product_url, now_str)

                save_seen_deal(product_url)
                seen_deals.add(product_url)

                daily_deal_key = f"DEAL_{today_str}_{product_url}"
                save_seen_deal(daily_deal_key)
                seen_deals.add(daily_deal_key)

                alerts_sent += 1
        except Exception:
            continue

    return alerts_sent


async def main():
    now_be = datetime.now(TZ)
    now_str = now_be.strftime("%H:%M:%S")
    today_str = now_be.strftime("%Y-%m-%d")

    print(f"=== START SMART SCAN ({today_str} {now_str}) | Filter: >= {MIN_DISCOUNT}% ===")
    seen_deals = load_seen_deals()
    alerts_sent = 0
    highest_tracker = {"pct": 0.0, "title": "Unknown"}

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="nl-BE",
            timezone_id="Europe/Brussels",
        )
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
        links = await page.locator("a[href*='route=product/category'], nav a[href], .menu a[href]").all()

        main_categories = []
        for link in links:
            href = await link.get_attribute("href")
            if href and "route=product/category&path=" in href:
                full_url = urljoin(BASE_URL, href)
                if "_" not in full_url.split("path=")[-1] and full_url not in main_categories:
                    main_categories.append(full_url)

        if len(main_categories) < 4:
            main_categories.extend([
                f"{BASE_URL}index.php?route=product/category&path=7060",
                f"{BASE_URL}index.php?route=product/category&path=6966",
                f"{BASE_URL}index.php?route=product/category&path=6991",
                f"{BASE_URL}index.php?route=product/category&path=7095",
            ])

        random.shuffle(main_categories)
        selected_cats = list(dict.fromkeys(main_categories))[:4]

        for cat_url in selected_cats:
            if alerts_sent >= MAX_ALERTS_PER_RUN:
                break
            sorted_url = f"{cat_url}&sort=p.date_added&order=ASC&limit=50"
            alerts_sent = await scan_page_products(
                page, sorted_url, seen_deals, alerts_sent, highest_tracker, now_str, today_str
            )

        await browser.close()

    total_found_today = sum(1 for item in seen_deals if item.startswith(f"DEAL_{today_str}_"))

    report_flag = f"DAILY_REPORT_{today_str}_{REPORT_HOUR:02d}"
    if now_be.hour == REPORT_HOUR and now_be.minute >= REPORT_MINUTE and report_flag not in seen_deals:
        send_daily_report(total_found_today, highest_tracker["pct"], highest_tracker["title"], today_str, now_str)
        save_seen_deal(report_flag)

    print(f"=== DONE! Highest discount seen: -{highest_tracker['pct']:.0f}% | Found today: {total_found_today} ===")


if __name__ == "__main__":
    asyncio.run(main())
    
