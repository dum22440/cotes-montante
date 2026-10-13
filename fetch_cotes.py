# Récupère chaque jour les cotes (Winamax, Unibet, Betclic, Pinnacle) via OddsPapi
# et les écrit dans cotes_oddspapi.json (cotes.json reste celui de The Odds API).
import json, os, sys, time, datetime, requests

API_KEY = os.environ["ODDSPAPI_KEY"]          # clé stockée en secret GitHub, jamais dans le code
BASE = "https://api.oddspapi.io/v4"
BOOKMAKERS = ["winamax.fr", "unibet.fr", "pinnacle"]   # Betclic n'est pas dispo sur OddsPapi

# Compétitions foot voulues : (mot dans le pays/catégorie, mot dans le nom)
FOOT = [
    ("france", "ligue 1"), ("england", "premier league"), ("italy", "serie a"),
    ("germany", "bundesliga"), ("spain", "laliga"), ("portugal", "liga portugal"),
    ("turkey", "super lig"), ("netherlands", "eredivisie"),
    ("", "champions league"), ("", "europa league"), ("", "conference league"),
]

def get(path, **params):
    params["apiKey"] = API_KEY
    time.sleep(1.2)   # l'API impose 1 s entre deux appels
    r = requests.get(f"{BASE}/{path}", params=params, timeout=60)
    print(f"GET {path} -> {r.status_code}")
    r.raise_for_status()
    return r.json()

def sport_id(name):
    for s in get("sports"):
        if name in str(s.get("sportName", "")).lower():
            return s["sportId"]
    return None

def tournois_foot():
    ids = {}
    for t in get("tournaments", sportId=10):
        cat = str(t.get("categoryName", "")).lower()
        nom = str(t.get("tournamentName", "")).lower()
        if not t.get("upcomingFixtures"):
            continue
        for c, n in FOOT:
            if c in cat and n in nom and "women" not in nom and "u19" not in nom:
                ids[t["tournamentId"]] = f'{t.get("categoryName")} - {t.get("tournamentName")}'
    return ids

def tournois_tennis():
    ids = {}
    sid = 13   # tennis
    for t in get("tournaments", sportId=sid):
        cat = str(t.get("categoryName", "")).upper()
        if t.get("upcomingFixtures") and ("ATP" in cat or "WTA" in cat):
            ids[t["tournamentId"]] = f'{t.get("categoryName")} - {t.get("tournamentName")}'
    return ids

def main():
    tournois = {**tournois_foot(), **tournois_tennis()}
    print(f"{len(tournois)} compétitions trouvées")
    if not tournois:
        sys.exit("Aucune compétition trouvée")
    liste = list(tournois)
    brut, matchs = {}, {}
    for i in range(0, len(liste), 15):   # 15 compétitions par requête, 3 bookmakers d'un coup
        try:
            data = get("odds-by-tournaments", bookmakers=",".join(BOOKMAKERS),
                       tournamentIds=",".join(map(str, liste[i:i+15])))
        except Exception as e:
            print(f"  erreur {e}")
            continue
        brut.setdefault("fixtures", []).extend(data)
        for f in data:
            m = matchs.setdefault(f["fixtureId"], {
                "competition": tournois.get(f.get("tournamentId"), ""),
                "debut": f.get("startTime"),
                "equipes": f'{f.get("participant1Name", f.get("participant1Id"))} vs '
                           f'{f.get("participant2Name", f.get("participant2Id"))}',
                "cotes": {},
            })
            for bk in BOOKMAKERS:
                marches = (f.get("bookmakerOdds") or {}).get(bk, {}).get("markets", {})
                cotes = {}
                for mid, marche in marches.items():
                    for oid, o in marche.get("outcomes", {}).items():
                        p = o.get("players", {}).get("0", {})
                        if p.get("active", True) and p.get("price"):
                            cle = f'{marche.get("bookmakerMarketId", mid)} | {p.get("bookmakerOutcomeId", oid)}'
                            cotes[cle] = p["price"]
                if cotes:
                    m["cotes"][bk] = cotes
    sortie = {
        "mis_a_jour": datetime.datetime.utcnow().isoformat() + "Z",
        "matchs": sorted(matchs.values(), key=lambda x: str(x["debut"])),
    }
    json.dump(sortie, open("cotes_oddspapi.json", "w"), ensure_ascii=False, indent=1)
    json.dump(brut, open("cotes_oddspapi_brutes.json", "w"), ensure_ascii=False)
    print(f"{len(matchs)} matchs écrits dans cotes_oddspapi.json")

if __name__ == "__main__":
    main()
