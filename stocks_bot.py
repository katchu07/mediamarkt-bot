import os
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

# ==========================================
# SETTINGS (Focus sur les actions à fort potentiel / volatiles)
# ==========================================
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_STOCKS")
MY_DISCORD_ID = "644995150774272020"  # Ton ID Discord pour les pings d'urgence
TZ = ZoneInfo("Europe/Brussels")

# Liste élargie : actions de croissance, tech innovante, et valeurs à fort potentiel de rebond
EXPANDED_WATCHLIST = [
    # Tech & Croissance / Potentiel fort
    "PLTR", "COIN", "HOOD", "RBLX", "SOFI", "U", "SHOP", "SQ", "RIVN", "LCID",
    "SNOW", "CRWD", "DDOG", "NET", "PATH", "ARM", "SMCI", "AMD", "NVDA",
    # Valeurs européennes / technologiques / vertes en vue
    "ASML.AS", "ADYEN.AS", "NIO", "BABA", "PDD", "SE", "MELI",
    # Énergies / Biotech / Volatiles
    "ENPH", "SEDG", "IONQ", "QUBT"
]

def fetch_stock_data(ticker: str) -> dict:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            result = data["chart"]["result"][0]
            meta = result["meta"]
            
            current_price = meta.get("regularMarketPrice", 0.0)
            prev_close = meta.get("chartPreviousClose", meta.get("previousClose", current_price))
            
            change_pct = ((current_price - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0

            return {
                "ticker": ticker,
                "price": current_price,
                "change_pct": change_pct,
                "currency": meta.get("currency", "USD")
            }
    except Exception:
        pass
    return None

def fetch_analyst_opinion(ticker: str) -> tuple:
    try:
        url = f"https://query2.finance.yahoo.com/v11/finance/quoteSummary/{ticker}?modules=financialData"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            fin = data["quoteSummary"]["result"][0].get("financialData", {})
            rec_key = fin.get("recommendationKey", "neutral").upper()
            target_price = fin.get("targetMeanPrice", {}).get("raw", 0.0)
            return rec_key, target_price
    except Exception:
        pass
    return "HOLD", 0.0

def send_discord_message(payload: dict):
    if not DISCORD_WEBHOOK_URL:
        print("❌ ERROR: DISCORD_WEBHOOK_STOCKS secret is missing!")
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"   [DISCORD ERROR] {e}")

def main():
    now_be = datetime.now(TZ)
    date_str = now_be.strftime("%Y-%m-%d %H:%M")
    print(f"=== START GROWTH & DIP CHECK ({date_str}) ===")

    market_overview = []

    for ticker in EXPANDED_WATCHLIST:
        data = fetch_stock_data(ticker)
        if not data:
            continue
        
        rec, target = fetch_analyst_opinion(ticker)
        data["recommendation"] = rec
        data["target_price"] = target
        
        market_overview.append(data)

    # Tri pour afficher en premier les plus grosses baisses de la journée (les "Losers")
    market_overview.sort(key=lambda x: x["change_pct"])

    # On sélectionne les 5 actions qui ont le plus chuté aujourd'hui
    top_drops = market_overview[:5]

    top_losers_text = ""
    for item in top_drops:
        emoji = "🔴" if item["change_pct"] < 0 else "🟢"
        top_losers_text += f"{emoji} **{item['ticker']}**: `{item['price']:.2f} {item['currency']}` (*{item['change_pct']:.2f}%*) | Expert view: **{item['recommendation']}**\n"

    # Détection d'une opportunité en or : Grosse chute (<-4%) + Avis positif d'experts (BUY ou STRONG_BUY)
    golden_opportunities = [
        d for d in top_drops 
        if d["change_pct"] <= -4.0 and d["recommendation"] in ["BUY", "STRONG_BUY"]
    ]

    # 1. Alerte VIP avec ping direct si une forte baisse a un avis "Buy" d'expert
    if golden_opportunities:
        for opp in golden_opportunities:
            ping_text = f"<@{MY_DISCORD_ID}> 🚨 **HIGH-POTENTIAL DIP DETECTED!**"
            embed = {
                "title": f"📉 Growth Stock Dip: {opp['ticker']} dropped {opp['change_pct']:.2f}%!",
                "description": (
                    f"**{opp['ticker']}** has experienced a sharp pullback to **{opp['price']:.2f} {opp['currency']}**.\n\n"
                    f"👔 **Analyst Consensus:** `{opp['recommendation']}`\n"
                    f"🎯 **Expert Target Price:** `{opp['target_price']:.2f} {opp['currency']}`\n\n"
                    f"👉 Significant drop on a growth asset while experts maintain a 'Buy' rating. Potential rebound opportunity!"
                ),
                "color": 16711680,
                "footer": {"text": f"Growth Stock Monitor • {date_str}"}
            }
            send_discord_message({"username": "Growth Stock Bot", "content": ping_text, "embeds": [embed]})

    # 2. Rapport quotidien avec les plus grosses chutes du segment croissance
    daily_embed = {
        "title": f"🚀 Growth & Volatile Stocks Report — {now_be.strftime('%d %B %Y')}",
        "description": (
            f"Here are the **biggest market drops** today among high-potential growth and tech stocks.\n\n"
            f"📉 **Top Dips / Biggest Losers Today:**\n{top_losers_text}\n"
            f"💡 *Tracking potential recovery plays backed by analyst ratings.*"
        ),
        "color": 3447003,
        "footer": {"text": "Growth Stock Monitor • Daily Pulse"}
    }
    
    send_discord_message({
        "username": "Growth Stock Bot", 
        "content": "🚀 **Daily growth stock market update:**" if not golden_opportunities else "🚀 **Daily Report + VIP Opportunity Alert sent above!**", 
        "embeds": [daily_embed]
    })

    print("=== GROWTH CHECK COMPLETE ===")

if __name__ == "__main__":
    main()
