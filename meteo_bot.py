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
METEO_BE_URL = "https://www.meteo.be/fr/woluwe-saint-lambert"
# Coordonnées exactes pour Sint-Stevens-Woluwe / Woluwe-Saint-Lambert
LAT = 50.858
LON = 4.444

# Récupère UNIQUEMENT le secret du salon Météo !
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_METEO")
TZ = ZoneInfo("Europe/Brussels")


def wmo_to_english(code: int) -> str:
    mapping = {
        0: "☀️ Clear sky",
        1: "🌤️ Mainly clear",
        2: "⛅ Partly cloudy",
        3: "☁️ Overcast",
        45: "🌫️ Foggy",
        48: "🌫️ Rime fog",
        51: "🌦️ Light drizzle",
        53: "🌦️ Moderate drizzle",
        55: "🌧️ Dense drizzle",
        61: "🌧️ Slight rain",
        63: "🌧️ Moderate rain",
        65: "🌧️ Heavy rain",
        71: "🌨️ Slight snow",
        73: "🌨️ Moderate snow",
        75: "❄️ Heavy snow",
        80: "🌦️ Rain showers",
        81: "🌧️ Moderate showers",
        82: "⛈️ Violent showers",
        95: "⛈️ Thunderstorm",
    }
    return mapping.get(code, "⛅ Variable")


async def scrape_irm_page() -> dict:
    """Visite le site officiel meteo.be pour lire les infos de Woluwe."""
    info = {"irm_temp": None, "irm_uv": None}
    try:
        async with Stealth().use_async(async_playwright()) as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="fr-BE",
                timezone_id="Europe/Brussels",
            )
            page = await context.new_page()
            await page.goto(METEO_BE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)

            body_text = await page.locator("body").inner_text()

            # Cherche un indice UV éventuel sur la page IRM
            uv_match = re.search(r"UV\s*[:\-]?\s*(\d+(?:[.,]\d+)?)", body_text, re.IGNORECASE)
            if uv_match:
                info["irm_uv"] = float(uv_match.group(1).replace(",", "."))

            await browser.close()
    except Exception as e:
        print(f"[INFO] Lecture directe IRM ignorée, utilisation des données satellites : {e}")
    return info


def fetch_detailed_slices() -> dict:
    """Récupère les tranches horaires exactes (Matin 8h, Midi 13h, Soir 19h) et l'UV max."""
    url = (
        f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
        f"&hourly=temperature_2m,apparent_temperature,precipitation_probability,precipitation,weathercode,windspeed_10m,uv_index"
        f"&daily=uv_index_max,temperature_2m_max,temperature_2m_min"
        f"&timezone=Europe%2FBrussels&forecast_days=1"
    )
    res = requests.get(url, timeout=15)
    res.raise_for_status()
    return res.json()


def build_clothing_advice(min_temp, max_temp, max_rain_prob, max_wind) -> str:
    items = []

    # Conseils selon la température
    if max_temp < 5:
        items.append("🧥 **Heavy winter coat**, thermal layers, gloves, and a warm beanie.")
    elif max_temp < 12:
        items.append("🧥 **Warm jacket or coat** with a sweater/hoodie.")
    elif max_temp < 18:
        items.append("🧶 **Light jacket, trench coat, or hoodie** (layer up, chilly morning!).")
    elif max_temp < 23:
        items.append("👕 **T-shirt with a light overshirt/sweater** for the morning.")
    else:
        items.append("🩳 **Light summer clothes** (T-shirt, shorts/light pants).")

    if min_temp < 8 and max_temp >= 15:
        items.append("🧣 **Dress in layers (oignon style):** cold at 8 AM, mild this afternoon.")

    # Conseils pluie
    if max_rain_prob >= 60:
        items.append("☔ **Bring an umbrella and a waterproof jacket!** (High chance of rain).")
    elif max_rain_prob >= 30:
        items.append("🌂 **Pack a small foldable umbrella** just in case (possible showers).")
    else:
        items.append("👟 **No umbrella needed** (dry day expected).")

    # Conseils vent
    if max_wind >= 35:
        items.append("💨 **Windbreaker recommended** (breezy/windy today).")

    return "\n".join(items)


def build_sunscreen_advice(uv_max: float) -> str:
    if uv_max >= 6:
        return f"🧴 **YES — HIGH PRIORITY (UV Index: {uv_max:.1f})**\nApply **SPF 30+ or 50+** sunscreen and wear sunglasses!"
    elif uv_max >= 3:
        return f"🧴 **YES — RECOMMENDED (UV Index: {uv_max:.1f})**\nPut on **SPF 30+** if you're outside around midday."
    else:
        return f"✅ **NO SUNSCREEN NEEDED (UV Index: {uv_max:.1f})**\nUV levels are too low today."


def format_period(hourly: dict, idx: int) -> str:
    temp = hourly["temperature_2m"][idx]
    feels = hourly["apparent_temperature"][idx]
    rain_p = hourly["precipitation_probability"][idx]
    wind = hourly["windspeed_10m"][idx]
    cond = wmo_to_english(hourly["weathercode"][idx])
    return (
        f"**{cond}**\n"
        f"🌡️ Temp: **{temp:.1f}°C** *(Feels: {feels:.1f}°C)*\n"
        f"🌧️ Rain: **{rain_p}%** | 💨 Wind: **{wind:.0f} km/h**"
    )


async def main():
    if not DISCORD_WEBHOOK_URL:
        print("❌ ERROR: DISCORD_WEBHOOK_METEO secret is missing!")
        return

    now_be = datetime.now(TZ)
    date_str = now_be.strftime("%A, %d %B %Y")

    print("1. Vérification de la page IRM (meteo.be)...")
    irm_data = await scrape_irm_page()

    print("2. Récupération des prévisions Matin / Midi / Soir pour Sint-Stevens-Woluwe...")
    data = fetch_detailed_slices()
    hourly = data["hourly"]
    daily = data["daily"]

    # Index horaires : 8 = 08:00 (Matin), 13 = 13:00 (Midi), 19 = 19:00 (Soir)
    morning_str = format_period(hourly, 8)
    midday_str = format_period(hourly, 13)
    evening_str = format_period(hourly, 19)

    day_temps = [hourly["temperature_2m"][i] for i in range(8, 21)]
    day_rain = [hourly["precipitation_probability"][i] for i in range(8, 21)]
    day_wind = [hourly["windspeed_10m"][i] for i in range(8, 21)]

    min_t, max_t = min(day_temps), max(day_temps)
    max_rain_prob = max(day_rain)
    max_wind = max(day_wind)

    uv_max = irm_data["irm_uv"] if irm_data["irm_uv"] is not None else daily["uv_index_max"][0]

    clothing_text = build_clothing_advice(min_t, max_t, max_rain_prob, max_wind)
    sunscreen_text = build_sunscreen_advice(uv_max)

    embed = {
        "title": f"🌤️ Sint-Stevens-Woluwe Weather — {date_str}",
        "description": f"Good morning! Here is your daily forecast based on **[IRM / Meteo.be]({METEO_BE_URL})**.",
        "url": METEO_BE_URL,
        "color": 3447003 if max_rain_prob >= 50 else 16763904,
        "fields": [
            {"name": "🌅 Morning (08:00)", "value": morning_str, "inline": True},
            {"name": "☀️ Midday (13:00)", "value": midday_str, "inline": True},
            {"name": "🌙 Evening (19:00)", "value": evening_str, "inline": True},
            {"name": "👔 What to Wear Today", "value": clothing_text, "inline": False},
            {"name": "🧴 Sunscreen Check", "value": sunscreen_text, "inline": False},
        ],
        "footer": {"text": f"IRM Woluwe Weather Bot • Sent at {now_be.strftime('%H:%M')}"},
    }

    payload = {
        "username": "Woluwe Weather Bot",
        "content": "☕ **Good morning! Here is your weather & outfit briefing for today:**",
        "embeds": [embed],
    }

    res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    print(f"✅ Weather report sent to Discord! (Status: {res.status_code})")


if __name__ == "__main__":
    asyncio.run(main())
