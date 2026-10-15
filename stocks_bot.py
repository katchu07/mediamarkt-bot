import os
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

# ==========================================
# SETTINGS
# ==========================================
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_STOCKS")
MY_DISCORD_ID = "644995150774272020"  # Ton ID utilisateur pour les pings d'urgence
TZ = ZoneInfo("Europe/Brussels")

WATCHLIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", 
    "NFLX", "AMD", "INTL", "DIS", "PYPL", "BA", "JPM", "V",
    "ASML.AS", "ADYEN.AS", "ABI.BR", "SOLB.BR"
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
    except Exception as e:
        print(f"   [!] Error fetching {ticker}: {e}")
    return None

def fetch_analyst_opinion(ticker: str) -> tuple:
    try:
        url = f"https://query2.finance.yahoo.com/v11/finance/quoteSummary/{ticker}?modules=financialData,recommendationTrend"
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
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"   [DISCORD] Message sent -> Status {res.status_code}")
    except Exception as e:
        print(f"   [DISCORD ERROR] {e}")

def main():
    now_be = datetime.now(TZ)
    date_str = now_be.strftime("%Y-%m-%d %H:%M")
    print(f"=== START STOCK & EXPERT CHECK ({date_str}) ===")

    significant_dips = []
    market_overview = []

    for ticker in WATCHLIST:
        data = fetch_stock_data(ticker)
        if not data:
            continue
        
        rec, target = fetch_analyst_opinion(ticker)
        data["recommendation"] = rec
        data["target_price"] = target
        
        market_overview.append(data)
        
        if data["change_pct"] <= -3.5:
            significant_dips.append(data)

    market_overview.sort(key=lambda x: x["change_pct"])
    significant_dips.sort(key=lambda x: x["change_pct"])

    top_losers_text = ""
    for item in market_overview[:5]:
        emoji = "🔴" if item["change_pct"] < 0 else "🟢"
        top_losers_text += f"{emoji} **{item['ticker']}**: `{item['price']:.2f} {item['currency']}` (*{item['change_pct']:.2f}%*) | Expert view: **{item['recommendation']}**\n"

    golden_opportunities = [
        d for d in significant_dips 
        if d["change_pct"] <= -5.0 and d["recommendation"] in ["BUY", "STRONG_BUY"]
    ]

    if golden_opportunities:
        for opp in golden_opportunities:
            ping_text = f"<@{MY_DISCORD_ID}> 🚨 **MAJOR DIP OPPORTUNITY DETECTED!**"
            embed = {
                "title": f"📉 Expert-Backed Dip: {opp['ticker']} is down {opp['change_pct']:.2f}%!",
                "description": (
                    f"**{opp['ticker']}** has dropped sharply to **{opp['price']:.2f} {opp['currency']}**.\n\n"
                    f"👔 **Expert Consensus:** `{opp['recommendation']}`\n"
                    f"🎯 **Analyst Target Price:** `{opp['target_price']:.2f} {opp['currency']}`\n\n"
                    f"👉 This stock is heavily discounted while experts still rate it as a buy."
                ),
                "color": 16711680,
                "footer": {"text": f"Stock Market Monitor • {date_str}"}
            }
            send_discord_message({"username": "Stock Expert Bot", "content": ping_text, "embeds": [embed]})

    daily_embed = {
        "title": f"📊 Daily Stock Market & Expert Report — {now_be.strftime('%d %B %Y')}",
        "description": (
            f"Here is your daily market pulse on monitored assets.\n\n"
            f"📉 **Top Movers / Biggest Drops Today:**\n{top_losers_text}\n"
            f"💡 *Scanned daily for sudden drops backed by analyst 'BUY' ratings.*"
        ),
        "color": 3447003,
        "footer": {"text": "Stock Market Monitor • Automated Daily Update"}
    }
    
    send_discord_message({
        "username": "Stock Expert Bot", 
        "content": "📊 **Daily stock market update:**" if not golden_opportunities else "📊 **Daily Market Report + VIP Alert sent above!**", 
        "embeds": [daily_embed]
    })

    print("=== STOCK CHECK COMPLETE ===")

if __name__ == "__main__":
    main()
