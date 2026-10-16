import os
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import html
from datetime import datetime

# Configuration des sources (La Libre, Le Soir, RTL Info, Google News Belgique/Monde)
SOURCES = [
    {
        "nom": "La Libre",
        "url": "https://www.lalibre.be/arc/outboundfeeds/rss/?outputType=xml"
    },
    {
        "nom": "Le Soir",
        "url": "https://www.lesoir.be/rss2/2/cible_principale"
    },
    {
        "nom": "RTL Info",
        "url": "https://news.google.com/rss/search?q=site:rtl.be+when:1d&hl=fr&gl=BE&ceid=BE:fr"
    },
    {
        "nom": "Google News (Belgique & Monde)",
        "url": "https://news.google.com/rss?hl=fr&gl=BE&ceid=BE:fr"
    }
]

def nettoyer_texte(texte):
    if not texte:
        return ""
    # Décoder les entités HTML et supprimer les balises HTML
    texte = html.unescape(texte)
    texte = re.sub(r'<[^>]+>', '', texte)
    texte = " ".join(texte.split())
    # Couper proprement si le résumé est trop long
    if len(texte) > 240:
        texte = texte[:237] + "..."
    return texte

def extraire_articles(source):
    articles = []
    try:
        req = urllib.request.Request(
            source["url"],
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_data = response.read()
            
        root = ET.fromstring(xml_data)
        items = root.findall('.//item')
        
        for item in items[:3]:
            titre = item.find('title').text if item.find('title') is not None else "Sans titre"
            lien = item.find('link').text if item.find('link') is not None else ""
            description = item.find('description').text if item.find('description') is not None else ""
            
            titre_propre = nettoyer_texte(titre)
            desc_propre = nettoyer_texte(description)
            
            # Si la description répète juste le titre (fréquent sur Google News), on adapte le texte
            if not desc_propre or desc_propre.startswith(titre_propre[:30]):
                desc_propre = "Synthèse directe disponible via le lien de l'article complet."
                
            articles.append({
                "source": source["nom"],
                "titre": titre_propre,
                "lien": lien,
                "resume": desc_propre
            })
    except Exception as e:
        print(f"Erreur avec la source {source['nom']}: {e}")
        
    return articles

def selectionner_5_infos():
    # Récolter les articles de chaque média
    pool_par_source = [extraire_articles(s) for s in SOURCES]
    selection = []
    titres_vus = set()
    
    # Sélection croisée pour garantir des nouvelles de toutes les sources sans doublons
    for rang in range(3):
        for liste_articles in pool_par_source:
            if rang < len(liste_articles):
                art = liste_articles[rang]
                cle_titre = art["titre"][:35].lower()
                if cle_titre not in titres_vus:
                    titres_vus.add(cle_titre)
                    selection.append(art)
                if len(selection) == 5:
                    return selection
    return selection

def envoyer_discord(articles):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_ACTUS")
    if not webhook_url:
        raise ValueError("Le secret DISCORD_WEBHOOK_ACTUS est introuvable ou vide.")
        
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
        "content": f"📰 **Le Récap' Actus du Soir ({date_str}) — Belgique & Monde**\nVoici les 5 informations les plus importantes de la journée :",
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
      
