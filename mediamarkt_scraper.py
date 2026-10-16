import asyncio
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
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

# Badge rouge : "-15%" ou "-50 %"
BADGE_REGEX = re.compile(r"^-\s*(\d{1,2})\s*%$")
BADGE_IN_TEXT = re.compile(r"(?<![\w.])-\s*(\d{1,2})\s*%")
PRODUCT_CODE = re.compile(r"\(?\bCP\.[A-Z]+\.\d+\.\d+\)?")
SKIP_LINES = {"als nieuw", "licht beschadigd", "zichtbaar beschadigd krassen", "meer info"}

# Trouve chaque produit via ses liens "product_id=..." (ne dépend d'aucune classe CSS)
EXTRACT_JS = """
() => {
  const sel = "a[href*='product_id=']";
  const idOf = a => { const m = a.href.match(/product_id=(\\d+)/); return m ? m[1] : null; };
  const idsIn = el => new Set(Array.from(el.querySelectorAll(sel)).map(idOf).filter(Boolean));
  const seen = new Set();
  const out = [];
  for (const a of Array.from(document.querySelectorAll(sel))) {
    const id = idOf(a);
    if (!id || seen.has(id)) continue;
    let el = a;
    for (let i = 0; i < 8; i++) {
      const p = el.parentElement;
      if (!p || p === document.body) break;
      if (idsIn(p).size > 1) break;
      el = p;
    }
    seen.add(id);
    out.push({ id: id, text: el.innerText || "" });
  }
  return out;
}
"""


def load_seen_deals() -> set:
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen_deal(key: str):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"{key}\n")


def load_today_max(seen: set, today_str: str):
    """Plus forte réduction déjà vue aujourd'hui (sur toutes les exécutions)."""
    best_pct, best_title = 0.0, "Unknown"
    prefix = f"MAX_{today_str}_"
    for item in seen:
        if item.startswith(prefix):
            parts = item.split("_", 3)  # MAX, date, pct, titre
            try:
                pct = float(parts[2])
            except (IndexError, ValueError):
                continue
            if pct > best_pct:
                best_pct = pct
                best_title = parts[3] if len(parts) > 3 else "Unknown"
    return best_pct, best_title


def parse_eu_number(val_str: str) -> float:
    clean = val_str.strip().strip(",.-–")
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
    """Prix uniquement pour l'AFFICHAGE dans Discord (jamais pour calculer la promo)."""
    clean = PRODUCT_CODE.sub(" ", text)
    found = re.findall(r"€\s*(\d+(?:[.,]\d{3})*(?:[.,]\d{2})?)", clean)
    prices = [parse_eu_number(p) for p in found]
    prices = [p for p in prices if 1.0 <= p <= 15000.0]
    if not prices:
        return 0.0, 0.0
    if len(prices) == 1:
        return 0.0, prices[0]
    return max(prices), min(prices)


def read_badge_from_text(text: str):
    """Lit UNIQUEMENT le pourcentage affiché (ex: '-25%'). Retourne None si pas fiable."""
    clean = PRODUCT_CODE.sub(" ", text)
    lines = [l.strip() for l in clean.splitlines() if l.strip()]

    badges = []
    for l in lines:
        m = BADGE_REGEX.match(l)
        if m:
            badges.append(float(m.group(1)))

    # Secours : "-25%" noyé dans une ligne
    if not badges:
        badges = [float(x) for x in BADGE_IN_TEXT.findall(clean)]

    if not badges or len(set(badges)) > 1:
        return None
    return badges[0]


def extract_title(text: str) -> str:
    for line in text.splitlines():
        l = line.strip()
        if len(l) < 4 or l.lower() in SKIP_LINES:
            continue
        if l.startswith("€") or l.startswith("-"):
            continue
        return re.sub(r"\s+", " ", l)[:100]
    return "MediaMarkt Product"


def send_discord_deal(title, old_price, new_price, discount, url, now_str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        print("   ❌ DISCORD_WEBHOOK secret is missing!")
        return False

    def fmt(v):
        if v <= 0:
            return "See website"
        return f"€ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    embed = {
        "title": f"🔥 MASSIVE DEAL FOUND: -{discount:.0f}% OFF!",
        "description": f"**[{title}]({url})**\n\n👉 **[Click here to open on MediaMarkt Outlet]({url})**",
        "url": url,
        "color": 16753920,
        "fields": [
            {"name": "🏷️ Old Price", "value": f"~~{fmt(old_price)}~~", "inline": True},
            {"name": "💶 Outlet Price", "value": f"**{fmt(new_price)}**", "inline": True},
            {"name": "📉 Discount", "value": f"**-{discount:.0f}%**", "inline": True},
        ],
        "footer": {"text": f"MediaMarkt Outlet Monitor • {now_str}"},
    }
    try:
        res = requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "username": "MediaMarkt Outlet Bot",
                "content": f"@everyone ⚡ **New -{discount:.0f}% discount found!**",
                "embeds": [embed],
            },
            timeout=10,
        )
        return res.status_code in (200, 204)
    except Exception as e:
        print(f"   [DISCORD ERROR] {e}")
        return False


def send_daily_report(total_found_today, max_seen_discount, max_seen_title, date_str, time_str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False

    min_disc_int = int(MIN_DISCOUNT)
    if total_found_today > 0:
        status_line = f"✅ **Deals found today (>={min_disc_int}%):** `{total_found_today} deal(s) found today!`"
    else:
        status_line = f"❌ **Today no deals found with more than {min_disc_int}% discount.**"

    embed = {
        "title": f"🌙 Daily Status Report ({REPORT_HOUR:02d}:{REPORT_MINUTE:02d}) — {date_str}",
        "description": (
            f"The bot is **100% active**.\n\n{status_line}\n"
            f"📊 **Highest discount seen today:** `-{max_seen_discount:.0f}%` *(e.g. {max_seen_title[:40]}...)*"
        ),
        "color": 3447003,
        "footer": {"text": f"MediaMarkt 24/7 Cloud Monitor • {time_str}"},
    }
    try:
        res = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"username": "MediaMarkt Outlet Bot", "embeds": [embed], "allowed_mentions": {"parse": []}},
            timeout=10,
        )
        return res.status_code in (200, 204)
    except Exception as e:
        print(f"   [DISCORD ERROR] {e}")
        return False


async def scan_page_products(page, target_url, seen_deals, alerts_sent, highest_tracker, now_str, today_str) -> int:
    label = target_url.split("path=")[-1].split("&")[0]
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3000)
    except Exception as e:
        print(f"   [!] Timeout/Skipped {target_url}: {e}")
        return alerts_sent

    products = await page.evaluate(EXTRACT_JS)
    print(f"   -> {len(products)} products found in category {label}")

    # Diagnostic : si rien n'est trouvé, on affiche ce que le bot voit vraiment
    if not products:
        try:
            title = await page.title()
            body = (await page.inner_text("body"))[:200].replace("\n", " ")
            print(f"   [DEBUG] page title: {title!r} | body starts with: {body!r}")
        except Exception as e:
            print(f"   [DEBUG] could not read page: {e}")
        return alerts_sent

    with_badge = 0
    for prod in products:
        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break
        try:
            text = prod["text"]
            discount_pct = read_badge_from_text(text)
            if discount_pct is None:
                continue
            with_badge += 1

            title = extract_title(text)
            product_url = f"{BASE_URL}index.php?route=product/product&product_id={prod['id']}"

            if discount_pct > highest_tracker["pct"]:
                highest_tracker["pct"] = discount_pct
                highest_tracker["title"] = title

            if product_url in seen_deals:
                continue

            if discount_pct >= MIN_DISCOUNT:
                old_price, new_price = extract_prices_for_display(text)
                print(f"   🔥 MATCH FOUND: {title} | -{discount_pct:.0f}%")

                # On ne mémorise le deal que si l'alerte est vraiment partie sur Discord
                if send_discord_deal(title, old_price, new_price, discount_pct, product_url, now_str):
                    save_seen_deal(product_url)
                    seen_deals.add(product_url)

                    daily_deal_key = f"DEAL_{today_str}_{product_url}"
                    save_seen_deal(daily_deal_key)
                    seen_deals.add(daily_deal_key)

                    alerts_sent += 1
        except Exception as e:
            print(f"   [!] Error on a product: {e}")
            continue

    print(f"   -> {with_badge} of them have a discount badge")
    return alerts_sent


async def main():
    now_be = datetime.now(TZ)
    now_str = now_be.strftime("%H:%M:%S")
    today_str = now_be.strftime("%Y-%m-%d")

    print(f"=== START SMART SCAN ({today_str} {now_str}) | Filter: >= {MIN_DISCOUNT}% ===")
    seen_deals = load_seen_deals()
    alerts_sent = 0

    # On repart de la plus forte réduction déjà vue aujourd'hui
    stored_pct, stored_title = load_today_max(seen_deals, today_str)
    highest_tracker = {"pct": stored_pct, "title": stored_title}

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

        fast_categories = [
            f"{BASE_URL}index.php?route=product/category&path=7060&sort=p.date_added&order=ASC&limit=50",
            f"{BASE_URL}index.php?route=product/category&path=6966&sort=p.date_added&order=ASC&limit=50",
        ]

        for cat_url in fast_categories:
            if alerts_sent >= MAX_ALERTS_PER_RUN:
                break
            alerts_sent = await scan_page_products(
                page, cat_url, seen_deals, alerts_sent, highest_tracker, now_str, today_str
            )

        await browser.close()

    # Mémorise la nouvelle plus forte réduction du jour
    if highest_tracker["pct"] > stored_pct:
        save_seen_deal(f"MAX_{today_str}_{int(highest_tracker['pct'])}_{highest_tracker['title']}")

    total_found_today = sum(1 for item in seen_deals if item.startswith(f"DEAL_{today_str}_"))

    # Rapport envoyé dès 22:00 passé, une seule fois par jour
    report_flag = f"DAILY_REPORT_{today_str}"
    report_due = (now_be.hour, now_be.minute) >= (REPORT_HOUR, REPORT_MINUTE)
    if report_due and report_flag not in seen_deals:
        if send_daily_report(total_found_today, highest_tracker["pct"], highest_tracker["title"], today_str, now_str):
            save_seen_deal(report_flag)

    print(f"=== DONE! Highest discount today: -{highest_tracker['pct']:.0f}% | Found today: {total_found_today} ===")


if __name__ == "__main__":
    asyncio.run(main())
