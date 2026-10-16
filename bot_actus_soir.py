import os
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import json
import re
import html
from datetime import datetime

# Configuration des sources avec contournement anti-bot pour Le Soir et RTL Info
SOURCES = [
    {
        "nom": "La Libre",
        "url": "https://www.lalibre.be/arc/outboundfeeds/rss/?outputType=xml",
        "type": "rss_direct"
    },
    {
        "nom": "Le Soir",
        "url": "https://www.bing.com/news/search?q=site:lesoir.be&format=rss&mkt=fr-BE",
        "type": "bing_rss"
    },
    {
        "nom": "RTL Info",
        "url": "https://www.bing.com/news/search?q=site:rtl.be&format=rss&mkt=fr-BE",
        "type": "bing_rss"
    },
    {
        "nom": "Google News (Belgique & Monde)",
        "url": "https://news.google.com/rss?hl=fr&gl=BE&ceid=BE:fr",
        "type": "google_news"
    }
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'fr-BE,fr;q=0.9'
}

PHRASES_BLOQUEES = [
    "informations complètes et à jour",
    "compilées par google actualités",
    "google news",
    "activez javascript"
]

def est_resume_valide(resume, titre):
    if not resume or len(resume) < 40:
        return False
    resume_low = resume.lower()
    for b in PHRASES_BLOQUEES:
        if b in resume_low:
            return False
    if resume_low.startswith(titre[:25].lower()):
        return False
    return True

def nettoyer_texte(texte, max_len=360):
    if not texte:
        return ""
    texte = html.unescape(texte)
    texte = re.sub(r'<[^>]+>', ' ', texte)
    texte = " ".join(texte.split())
    if len(texte) > max_len:
        texte = texte[:max_len - 3].rsplit(' ', 1)[0] + "..."
    return texte

def extraire_vrai_lien_bing(lien):
    """Extrait l'URL directe de l'article (lesoir.be ou rtl.be) depuis un lien de redirection Bing."""
    try:
        parsed = urllib.parse.urlparse(lien)
        params = urllib.parse.parse_qs(parsed.query)
        if 'url' in params and params['url']:
            return params['url'][0]
    except Exception:
        pass
    return lien

def extraire_synthese_google_news(description_html, titre_principal):
    """Extrait les différents points de couverture d'un événement majeur sur Google News."""
    if not description_html:
        return ""
    textes_li = re.findall(r'<li[^>]*>(.*?)</li>', description_html, re.DOTALL | re.IGNORECASE)
    points = []
    titre_base = titre_principal.split(' - ')[0][:30].lower()
    
    for li in textes_li:
        propre = nettoyer_texte(li, max_len=200)
        if propre and not propre.lower().startswith(titre_base):
            points.append(f"• {propre}")
            
    if points:
        return "Synthèse des différents médias sur ce sujet :\n" + "\n".join(points[:3])
    return ""

def chercher_resume_via_bing(titre):
    """Si un article Google News n'a qu'un seul lien sans synthèse, récupère son extrait via Bing."""
    try:
        titre_propre = titre.split(' - ')[0]
        q = urllib.parse.quote(titre_propre)
        url = f"https://www.bing.com/news/search?q={q}&format=rss&mkt=fr-BE"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read())
        for item in root.findall('.//item')[:2]:
            desc = item.find('description')
            if desc is not None and desc.text:
                texte = nettoyer_texte(desc.text)
                if est_resume_valide(texte, titre_propre):
                    return texte
    except Exception:
        pass
    return ""

def extraire_articles(source):
    articles = []
    try:
        req = urllib.request.Request(source["url"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as response:
            root = ET.fromstring(response.read())
    except Exception as e:
        print(f"Erreur sur {source['nom']} : {e}")
        return []

    items = root.findall('.//item')
    for item in items[:6]:
        titre_brut = item.find('title').text if item.find('title') is not None else "Sans titre"
        lien = item.find('link').text if item.find('link') is not None else ""
        desc_brute = item.find('description').text if item.find('description') is not None else ""
        
        titre_propre = nettoyer_texte(titre_brut, max_len=150)
        titre_propre = re.sub(r'\s+-\s+(RTL info|RTL\.be|Le Soir|La Libre)$', '', titre_propre, flags=re.IGNORECASE)
        
        if source["type"] == "bing_rss":
            lien = extraire_vrai_lien_bing(lien)
            resume = nettoyer_texte(desc_brute)
        elif source["type"] == "google_news":
            resume = extraire_synthese_google_news(desc_brute, titre_propre)
            if not est_resume_valide(resume, titre_propre):
                resume = chercher_resume_via_bing(titre_propre)
        else:
            resume = nettoyer_texte(desc_brute)
            
        if not est_resume_valide(resume, titre_propre):
            continue

        articles.append({
            "source": source["nom"],
            "titre": titre_propre,
            "lien": lien,
            "resume": resume
        })
        if len(articles) >= 3:
            break
            
    return articles

def selectionner_5_infos():
    pool_par_source = [extraire_articles(s) for s in SOURCES]
    selection = []
    titres_vus = set()
    
    for rang in range(3):
        for liste_articles in pool_par_source:
            if rang < len(liste_articles):
                art = liste_articles[rang]
                cle_titre = art["titre"][:30].lower()
                if cle_titre not in titres_vus:
                    titres_vus.add(cle_titre)
                    selection.append(art)
                if len(selection) == 5:
                    return selection
    return selection

def envoyer_discord(articles):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_ACTUS")
    if not webhook_url:
        raise ValueError("Le secret DISCORD_WEBHOOK_ACTUS est introuvable.")
        
    date_str = datetime.now().strftime("%d/%m/%Y")
    
    embeds = []
    for idx, art in enumerate(articles, 1):
        embeds.append({
            "title": f"{idx}. [{art['source']}] {art['titre']}",
            "url": art["lien"],
            "description": art["resume"],
            "color": 3447003
        })
        
    payload = {
        "content": f"📰 **Le Récap' Actus de la Journée ({date_str}) — Belgique & Monde**\nVoici les 5 informations les plus importantes du jour avec leur synthèse :",
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
        print(f"Recap envoyé avec succès ! Code HTTP : {response.status}")

if __name__ == "__main__":
    top_5 = selectionner_5_infos()
    if top_5:
        envoyer_discord(top_5)
    else:
        print("Aucune actualité n'a pu être récupérée.")
