import os
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import html
from datetime import datetime

# Configuration des sources couvrant toute la journée
SOURCES = [
    {
        "nom": "La Libre",
        "url": "https://www.lalibre.be/arc/outboundfeeds/rss/?outputType=xml",
        "type": "rss_direct"
    },
    {
        "nom": "Le Soir",
        "url": "https://www.lesoir.be/arc/outboundfeeds/rss/?outputType=xml",
        "url_secours": "https://news.google.com/rss/search?q=site:lesoir.be+when:1d&hl=fr&gl=BE&ceid=BE:fr",
        "type": "hybride"
    },
    {
        "nom": "RTL Info",
        "url": "https://www.rtl.be/rss.xml",
        "url_secours": "https://news.google.com/rss/search?q=site:rtl.be+when:1d&hl=fr&gl=BE&ceid=BE:fr",
        "type": "hybride"
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

def nettoyer_texte(texte, max_len=350):
    if not texte:
        return ""
    texte = html.unescape(texte)
    texte = re.sub(r'<[^>]+>', ' ', texte)
    texte = " ".join(texte.split())
    if len(texte) > max_len:
        texte = texte[:max_len - 3].rsplit(' ', 1)[0] + "..."
    return texte

def extraire_meta_description(url):
    """Récupère le chapeau/résumé directement sur la page de l'article si le flux RSS est vide."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            page_html = resp.read().decode('utf-8', errors='ignore')
        
        patterns = [
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']'
        ]
        for pat in patterns:
            m = re.search(pat, page_html, re.IGNORECASE)
            if m:
                desc = nettoyer_texte(m.group(1))
                if len(desc) > 40:
                    return desc
    except Exception:
        pass
    return ""

def extraire_synthese_google_news(description_html, titre_principal):
    """Transforme la liste des couvertures Google News en une vraie synthèse informative."""
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
    
    # Si un seul article dans le bloc Google News, on nettoie l'ensemble
    texte_global = nettoyer_texte(description_html)
    if texte_global and not texte_global.lower().startswith(titre_base):
        return texte_global
    return ""

def lire_flux_xml(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=12) as response:
        return ET.fromstring(response.read())

def extraire_articles(source):
    articles = []
    root = None
    utilise_secours = False
    
    try:
        root = lire_flux_xml(source["url"])
    except Exception:
        if "url_secours" in source:
            try:
                root = lire_flux_xml(source["url_secours"])
                utilise_secours = True
            except Exception as e:
                print(f"Erreur sur {source['nom']} : {e}")
                return []
        else:
            return []

    items = root.findall('.//item')
    for item in items[:4]:
        titre_brut = item.find('title').text if item.find('title') is not None else "Sans titre"
        lien = item.find('link').text if item.find('link') is not None else ""
        desc_brute = item.find('description').text if item.find('description') is not None else ""
        
        titre_propre = nettoyer_texte(titre_brut, max_len=150)
        # Nettoyer le suffixe " - RTL Info" ou " - Le Soir" dans le titre si redondant
        titre_propre = re.sub(r'\s+-\s+(RTL info|RTL\.be|Le Soir|La Libre)$', '', titre_propre, flags=re.IGNORECASE)
        
        resume = ""
        if source["type"] == "google_news" or utilise_secours:
            resume = extraire_synthese_google_news(desc_brute, titre_propre)
        else:
            resume = nettoyer_texte(desc_brute)
            
        # Vérifier si le résumé est vide ou répète simplement le titre
        if not resume or len(resume) < 35 or resume.lower().startswith(titre_propre[:25].lower()):
            resume_page = extraire_meta_description(lien)
            if resume_page and not resume_page.lower().startswith(titre_propre[:25].lower()):
                resume = resume_page

        # Si vraiment aucun texte supplémentaire n'est fourni par le média (ex: lien Google News verrouillé)
        if not resume or resume.lower().startswith(titre_propre[:25].lower()):
            # On cherche un autre article de la même source qui possède un résumé complet
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
