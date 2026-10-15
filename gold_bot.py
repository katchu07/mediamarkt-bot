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
    """Scrape spécifiquement le 'Cours moyen' ou le prix affiché sur Numagold."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(NUMAGOLD_URL, headers=headers, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            
            # Recherche ciblée sur le tableau "Cours moyen" visible sur la capture
            for row in soup.find_all(["tr", "div", "li", "p", "span"]):
                text = row.get_text()
                if "Cours moyen" in text:
                    # Extrait le prix en euro qui se trouve dans la même zone
                    match = re.search(r"€\s*[\d\.\,]+", text)
                    if match:
                        return match.group(0).strip()
            
            # Secours global : cherche n'importe quel prix en euro formaté type 6.007,00 €
            matches = re.findall(r"€\s*[\d\.\,]+\b", res.text)
            for m in matches:
                if "6." in m or "5." in m or "7." in m: # Filtre de sécurité pour viser la tranche de prix des lingotins 50g
                    return m.strip()
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
