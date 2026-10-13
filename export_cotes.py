# ============================================================
#  EXPORT DES COTES POUR CLAUDE
#  Récupère les cotes Betclic, Winamax, Unibet et Pinnacle
#  des matchs des 36 prochaines heures, et les écrit dans
#  cotes.json (publié sur GitHub, lu par Claude à chaque passage).
#  La clé API n'est PAS dans ce fichier : elle est dans les
#  "secrets" GitHub (variable ODDS_API_KEY).
# ============================================================

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

CLE_API = os.environ["ODDS_API_KEY"]
HORIZON_HEURES = 36
BOOKS = {"pinnacle": "Pinnacle", "betclic_fr": "Betclic",
         "winamax_fr": "Winamax", "unibet_fr": "Unibet"}

FOOT = {
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_epl": "Premier League",
    "soccer_italy_serie_a": "Serie A",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_spain_la_liga": "LaLiga",
    "soccer_portugal_primeira_liga": "Liga Portugal",
    "soccer_turkey_super_league": "Super Lig",
    "soccer_netherlands_eredivisie": "Eredivisie",
    "soccer_uefa_champs_league": "Ligue des Champions",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
}

restant = "?"


def appel(chemin):
    global restant
    sep = "&" if "?" in chemin else "?"
    url = "https://api.the-odds-api.com/v4/" + chemin + sep + "apiKey=" + CLE_API
    with urllib.request.urlopen(url) as reponse:
        restant = reponse.headers.get("x-requests-remaining") or restant
        return json.loads(reponse.read())


maintenant = datetime.now(timezone.utc)
limite = maintenant + timedelta(hours=HORIZON_HEURES)

# Liste des compétitions ouvertes (cet appel ne coûte aucune requête)
actifs = {s["key"]: s for s in appel("sports/") if s.get("active")}
a_lire = {k: v for k, v in FOOT.items() if k in actifs}
for k, s in actifs.items():
    if s["group"] == "Tennis" and ("ATP" in s["title"] or "WTA" in s["title"]):
        a_lire[k] = s["title"]

matchs_export = []
erreurs = []
for code, nom in a_lire.items():
    try:
        matchs = appel("sports/" + code + "/odds/?markets=h2h&oddsFormat=decimal&bookmakers="
                       + ",".join(BOOKS))
    except Exception as e:
        erreurs.append(nom + " : " + str(e))
        continue
    for m in matchs:
        debut = datetime.strptime(m["commence_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if debut <= maintenant or debut > limite:
            continue
        cotes = {}
        for b in m["bookmakers"]:
            if b["key"] in BOOKS and b["markets"]:
                cotes[BOOKS[b["key"]]] = {o["name"]: o["price"] for o in b["markets"][0]["outcomes"]}
        proba = None
        if "Pinnacle" in cotes:
            total = sum(1 / c for c in cotes["Pinnacle"].values())
            proba = {k: round((1 / c) / total * 100, 1) for k, c in cotes["Pinnacle"].items()}
        matchs_export.append({
            "competition": nom,
            "match": m["home_team"] + " vs " + m["away_team"],
            "debut_utc": m["commence_time"],
            "cotes": cotes,
            "proba_pinnacle_pct": proba,
        })

matchs_export.sort(key=lambda x: x["debut_utc"])
sortie = {
    "mis_a_jour_utc": maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "horizon_heures": HORIZON_HEURES,
    "requetes_restantes_ce_mois": restant,
    "erreurs": erreurs,
    "matchs": matchs_export,
}
with open("cotes.json", "w", encoding="utf-8") as f:
    json.dump(sortie, f, ensure_ascii=False, indent=1)
print(len(matchs_export), "matchs exportés. Requêtes restantes :", restant)
