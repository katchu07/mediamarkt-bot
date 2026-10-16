import os
import urllib.request
import json
import re
import html
from datetime import datetime

URL_EVERE = "https://www.cashconverters.be/nl/2-home?q=Winkel-EVERE&order=product.date_add.desc"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
    'Connection': 'keep-alive'
}

def nettoyer_texte(texte):
    if not texte:
        return ""
    texte = html.unescape(texte)
    texte = re.sub(r'<[^>]+>', '', texte)
    texte = " ".join(texte.split())
    if texte and texte == texte.lower():
        texte = texte.capitalize()
    return texte

def recuperer_20_derniers_articles():
    articles = []
    try:
        req = urllib.request.Request(URL_EVERE, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=25) as resp:
            page_html = resp.read().decode('utf-8', errors='ignore')
            
        # Extraction robuste des blocs de produits PrestaShop
        blocs = re.findall(r'<article[^>]*class=["\'][^"\']*product-miniature[^"\']*["\'][^>]*>(.*?)</article>', page_html, re.DOTALL | re.IGNORECASE)
        
        for bloc in blocs:
            m_titre = re.search(r'<h[23][^>]*class=["\'][^"\']*product-title[^"\']*["\'][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', bloc, re.DOTALL | re.IGNORECASE)
            if not m_titre:
                m_titre = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*product-title[^"\']*["\'][^>]*>(.*?)</a>', bloc, re.DOTALL | re.IGNORECASE)
                
            m_prix = re.search(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>(.*?)</span>', bloc, re.DOTALL | re.IGNORECASE)
            
            if m_titre:
                lien = m_titre.group(1).strip()
                nom = nettoyer_texte(m_titre.group(2))
                prix = nettoyer_texte(m_prix.group(1)) if m_prix else "Prix non affiché"
                
                # Éviter les doublons
                if not any(a['nom'] == nom for a in articles):
                    articles.append({
                        "nom": nom,
                        "prix": prix,
                        "lien": lien
                    })
                if len(articles) == 20:
                    break
    except Exception as e:
        print(f"Erreur lors de la récupération : {e}")

    return articles[:20]

def envoyer_discord(articles):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_CASH_EVERE")
    if not webhook_url:
        raise ValueError("Le secret DISCORD_WEBHOOK_CASH_EVERE est introuvable ou vide.")
        
    date_str = datetime.now().strftime("%d/%m/%Y")
    
    partie_1 = articles[:10]
    partie_2 = articles[10:]
    
    lignes_1 = [f"`{idx:2d}.` [{art['nom']}]({art['lien']}) — **{art['prix']}**" for idx, art in enumerate(partie_1, 1)]
    lignes_2 = [f"`{idx:2d}.` [{art['nom']}]({art['lien']}) — **{art['prix']}**" for idx, art in enumerate(partie_2, 11)]
    
    embeds = [
        {
            "title": "📦 Articles 1 à 10",
            "description": "\n".join(lignes_1),
            "color": 15844367
        }
    ]
    
    if lignes_2:
        embeds.append({
            "title": "📦 Articles 11 à 20",
            "description": "\n".join(lignes_2),
            "color": 15844367,
            "url": "https://www.cashconverters.be/nl/2-home?q=Winkel-EVERE"
        })
    
    payload = {
        "content": f"🛒 **Cash Converters Evere — Nouveautés du {date_str}**",
        "embeds": embeds
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'DiscordBot (https://github.com, 1.0)'
        }
    )
    
    with urllib.request.urlopen(req) as response:
        print(f"Liste Evere envoyée avec succès ! Code HTTP : {response.status}")

if __name__ == "__main__":
    liste_20 = recuperer_20_derniers_articles()
    if liste_20:
        envoyer_discord(liste_20)
    else:
        print("Aucun article n'a pu être récupéré sur Cash Converters Evere.")
