import os
from datetime import datetime
import requests
import pytz

def envoyer_rappel():
    # Configuration du fuseau horaire (Europe/Paris gère aussi la Belgique)
    tz = pytz.timezone('Europe/Paris')
    maintenant = datetime.now(tz)
    heure_actuelle = maintenant.hour
    minute_actuelle = maintenant.minute

    # Récupération du webhook Discord depuis les secrets GitHub
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Erreur : Le secret DISCORD_WEBHOOK_URL n'est pas défini.")
        return

    message = ""

    # Plage horaire pour Midi (par exemple entre 12:00 et 12:30)
    if heure_actuelle == 12:
        message = "⏰ **Rappel : Il est midi !** C'est l'heure de faire une pause et de déjeuner. Bon appétit ! 🍽️"
    
    # Plage horaire pour le Soir (par exemple entre 20:00 et 20:30)
    elif heure_actuelle == 20:
        message = "🌙 **Rappel du soir : Il est 20h !** Pense à souffler et à te détendre pour la soirée. ✨"
    
    else:
        print(f"Heure actuelle ({heure_actuelle:02d}:{minute_actuelle:02d}) : Aucun rappel programmé pour cette heure.")
        return

    # Envoi du message sur Discord
    payload = {"content": message}
    response = requests.post(webhook_url, json=payload)

    if response.status_code == 204:
        print("Rappel envoyé avec succès sur Discord !")
    else:
        print(f"Erreur lors de l'envoi : {response.status_code}, {response.text}")

if __name__ == "__main__":
    envoyer_rappel()
