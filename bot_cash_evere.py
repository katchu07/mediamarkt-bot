import os
import urllib.request
import json
import re
import html
from datetime import datetime

# URLs des deux magasins Cash Converters (avec encodage propre)
URLS_MAGASINS = [
    {
        "nom": "Magasin Evere",
        "url": "https://www.cashconverters.be/nl/2-home?q=Winkel-EVERE&order=product.date_add.desc"
    },
    {
        "nom": "Magasin Saint-Josse",
        "url": "https://www.cashconverters.be/nl/2-home?q=Winkel-SAINT-JOSSE&order=product.date_add.desc"
    }
]

HEADERS_JSON = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'X-Requested-With': 'XMLHttpRequest'
}

HEADERS_HTML = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'nl-BE,fr-BE;q=0.9'
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

def extraire_depuis_html(contenu_html):
    articles = []
    blocs = re.findall(r'<article[^>]*class=["\'][^"\']*product-miniature[^"\']*["\'][^>]*>(.*?)</article>', contenu_html, re.DOTALL | re.IGNORECASE)
    
    for bloc in blocs:
        m_titre = re.search(r'<h[23][^>]*class=["\'][^"\']*product-title[^"\']*["\'][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', bloc, re.DOTALL | re.IGNORECASE)
        if not m_titre:
            m_titre = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*product-title[^"\']*["\'][^>]*>(.*?)</a>', bloc, re.DOTALL | re.IGNORECASE)
            
        m_prix = re.search(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>(.*?)</span>', bloc, re.DOTALL | re.IGNORECASE)
        
        if m_titre:
            lien = m_titre.group(1).strip()
            nom = nettoyer_texte(m_titre.group(2))
            prix = nettoyer_texte(m_prix.group(1)) if m_prix else "Prix non affiché"
            
            articles.append({
                "nom": nom,
                "prix": prix,
                "lien": lien
            })
            if len(articles) == 20:
                break
                
    return articles

def recuperer_derniers_articles(url_magasin):
    articles = []
    
    # 1. Tentative via l'API JSON PrestaShop
    try:
        req = urllib.request.Request(url_magasin, headers=HEADERS_JSON)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8', errors='ignore'))
            
        if "products" in data and isinstance(data["products"], list):
            for p in data["products"][:20]:
                nom = nettoyer_texte(p.get("name", "Article sans nom"))
                prix = nettoyer_texte(p.get("price", p.get("regular_price", "Prix inconnu")))
                lien = p.get("url", p.get("canonical_url", url_magasin))
                articles.append({
                    "nom": nom,
                    "prix": prix,
                    "lien": lien
                })
            if articles:
                return articles
                
        if "rendered_products" in data:
            articles = extraire_depuis_html(data["rendered_products"])
            if articles:
                return articles
    except Exception as e:
        print(f"Passage au mode HTML standard pour {url_magasin}")

    # 2. Tentative via le HTML complet si le JSON échoue
    try:
        req = urllib.request.Request(url_magasin, headers=HEADERS_HTML)
        with urllib.request.urlopen(req, timeout=20) as resp:
            page_html = resp.read().decode('utf-8', errors='ignore')
        articles = extraire_depuis_html(page_html)
    except Exception as e:
        print(f"Erreur de récupération HTML pour {url_magasin} : {e}")

    return articles[:20]

def envoyer_discord_magasin(nom_magasin, articles):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_CASH_EVERE")
    if not webhook_url:
        raise ValueError("Le secret DISCORD_WEBHOOK_CASH_EVERE est introuvable ou vide.")
        
    if not articles:
        print(f"Aucun article à envoyer pour {nom_magasin}.")
        return

    date_str = datetime.now().strftime("%d/%m/%Y")
    
    # Séparer en deux blocs de 10 pour que chaque message reste parfaitement dans les limites Discord
    partie_1 = articles[:10]
    partie_2 = articles[10:]
    
    lignes_1 = [f"`{idx:2d}.` [{art['nom']}]({art['lien']}) — **{art['prix']}**" for idx, art in enumerate(partie_1, 1)]
    embeds = [{
        "title": f"📍 {nom_magasin} — Articles 1 à 10",
        "description": "\n".join(lignes_1),
        "color": 15844367
    }]
    
    if partie_2:
        lignes_2 = [f"`{idx:2d}.` [{art['nom']}]({art['lien']}) — **{art['prix']}**" for idx, art in enumerate(partie_2, 11)]
        embeds.append({
            "title": f"📍 {nom_magasin} — Articles 11 à 20",
            "description": "\n".join(lignes_2),
            "color": 15844367
        })

    payload = {
        "content": f"🛒 **Cash Converters — Nouveautés du {date_str} ({nom_magasin})**",
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
        print(f"Bilan envoyé avec succès pour {nom_magasin} ! Code HTTP : {response.status}")

if __name__ == "__main__":
    for magasin in URLS_MAGASINS:
        print(f"Récupération pour {magasin['nom']}...")
        items = recuperer_derniers_articles(magasin["url"])
        if items:
            envoyer_discord_magasin(magasin["nom"], items)
        else:
            print(fort := f"Attention : Impossible de récupérer les articles pour {magasin['nom']}.")
