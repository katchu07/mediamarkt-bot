import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

# ==========================================
# SETTINGS
# ==========================================
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_GOLD")
NUMAGOLD_URL = "https://numagold.be/fr/product/lingotin-dor-50-gr/"
TZ = ZoneInfo("Europe/Brussels")

def parse_price(text: str) -> float:
    """Nettoie une chaîne de prix pour en faire un float."""
    clean = re.sub(r"[^\d,\.]", "", text)
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

def fetch_gold_spot_price() -> float:
    """Récupère le prix spot mondial de l'or en EUR par gramme."""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=1d"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            quote = data["chart"]["result"][0]["meta"]
            price_per_ounce_usd = quote.get("regularMarketPrice", 0.0)
            
            fx_url = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X?interval=1d&range=1d"
            fx_res = requests.get(fx_url, headers=headers, timeout=10)
            eur_usd = 1.05
            if fx_res.status_code == 200:
                eur_usd = fx_res.json()["chart"]["result"][0]["meta"].get("regularMarketPrice", 1.05)
            
            price_per_ounce_eur = price_per_ounce_usd / eur_usd
            price_per_gram_eur = price_per_ounce_eur / 31.1035
            return price_per_gram_eur
    except Exception as e:
        print(f"[!] Error fetching spot gold: {e}")
    return 0.0

def fetch_numagold_lingotin() -> str:
    """Scrape le prix brut affiché sur Numagold."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(NUMAGOLD_URL, headers=headers, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            
            # Recherche du prix WooCommerce
            price_el = soup.find("span", class_="woocommerce-Price-amount amount")
            if price_el:
                return price_el.get_text().strip()
            
            # Secours via regex
            match = re.search(r"(\d{1,3}(?:[ \.\,]\d{3})*[,\.]\d{2})\s*€", res.text)
            if match:
                return match.group(0).strip()
    except Exception as e:
        print(f"[!] Error scraping Numagold: {e}")
    return "N/A"

def send_discord_message(payload: dict):
    if not DISCORD_WEBHOOK_URL:
        print("❌ ERROR: DISCORD_WEBHOOK_GOLD secret is missing!")
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"   [DISCORD ERROR] {e}")

def main():
    now_be = datetime.now(TZ)
    date_str = now_be.strftime("%d %B %Y")
    print(f"=== START GOLD TRACKER ({date_str}) ===")

    spot_per_gram = fetch_gold_spot_price()
    numagold_display_price = fetch_numagold_lingotin()

    spot_str = f"€ {spot_per_gram:,.2f}".replace(",", "X").replace(".", ",").replace("X", " sowohl") if spot_per_gram > 0 else "N/A"
    # Nettoyage propre du format affiché
    spot_str = f"€ {spot_per_gram:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if spot_per_gram > 0 else "N/A"

    embed = {
        "title": f"🪙 Gold Market Daily Report — {date_str}",
        "description": (
            f"Here is your daily update on gold prices in Belgium.\n\n"
            f"⚖️ **Gold Spot Price (per gram):** `{spot_str} / gram`\n\n"
            f"🥇 **Numagold 50g Lingotin:**\n"
            f"• **Price on site:** **{numagold_display_price}**\n\n"
            f"🔗 **[View Numagold Product Page]({NUMAGOLD_URL})**"
        ),
        "color": 16766720,  # Or
        "footer": {"text": f"Gold Tracker • {now_be.strftime('%H:%M')}"}
    }

    send_discord_message({
        "username": "Gold Tracker Bot",
        "content": "🪙 **Daily Gold & Physical Bar Update:**",
        "embeds": [embed]
    })

    print("=== GOLD CHECK COMPLETE ===")

if __name__ == "__main__":
    main()
