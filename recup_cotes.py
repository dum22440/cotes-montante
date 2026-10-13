"""
Récupère chaque jour les cotes Winamax FR, Unibet FR et Pinnacle (référence)
via l'API OddsPapi, pour le foot (8 championnats + coupes d'Europe) et le tennis ATP/WTA.

Sortie (dossier cotes/) :
  cotes/AAAA-MM-JJ_foot.csv, cotes/AAAA-MM-JJ_tennis.csv  -> une ligne par pari possible
  cotes/latest_foot.csv, cotes/latest_tennis.csv          -> copie du jour (toujours le même nom)

Colonnes : date, heure, competition, match, marche, issue, joueur,
           winamax, unibet, pinnacle, meilleure_cote, meilleur_book,
           proba_reelle (Pinnacle sans marge), ev_meilleure_cote

La clé API est lue dans la variable d'environnement ODDSPAPI_KEY (secret GitHub).
"""

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE = "https://api.oddspapi.io/v4"
KEY = os.environ.get("ODDSPAPI_KEY", "").strip()
BOOKS = ["winamax.fr", "unibet.fr", "pinnacle"]   # max 3 par requête
FR_BOOKS = {"winamax.fr": "winamax", "unibet.fr": "unibet"}
SOCCER, TENNIS = 10, 13

OUT = Path("cotes")
CACHE = OUT / "cache"
DEBUG = OUT / "debug"
CACHE_DAYS = 7          # liste des compétitions / noms / marchés rafraîchis 1 fois par semaine
HOURS_AHEAD = 36        # matchs commençant dans les 36 prochaines heures

# Compétitions foot : (mots dans le pays/catégorie, mots dans le nom de la compétition)
FOOT_TARGETS = [
    ("france", ["ligue 1"]),
    ("england", ["premier league"]),
    ("italy", ["serie a"]),
    ("germany", ["bundesliga"]),
    ("spain", ["laliga", "la liga"]),
    ("portugal", ["liga portugal", "primeira liga"]),
    ("turk", ["super lig", "süper lig", "superlig"]),
    ("netherlands", ["eredivisie"]),
    ("", ["uefa champions league"]),
    ("", ["uefa europa league"]),
    ("", ["uefa conference league", "uefa europa conference league"]),
]
FOOT_EXCLUDE = ["women", "féminin", "u19", "u21", "youth", "qualification", "2. ", "ii"]

TENNIS_INCLUDE = ["atp", "wta"]
TENNIS_EXCLUDE = ["doubles", "double", "qualification", "qualifying", "challenger", "itf", "125"]

calls = 0


# ---------------------------------------------------------------- outils API
def api(path, **params):
    global calls
    params["apiKey"] = KEY
    time.sleep(1.2)  # l'API impose 1 s entre deux appels
    r = requests.get(f"{BASE}/{path}", params=params, timeout=60)
    calls += 1
    if r.status_code == 429:
        sys.exit("Quota OddsPapi épuisé pour ce mois (erreur 429).")
    if r.status_code != 200:
        sys.exit(f"Erreur API {path}: {r.status_code} {r.text[:300]}")
    return r.json()


def cached(name, fetch):
    """Garde une réponse en cache CACHE_DAYS jours pour économiser le quota."""
    f = CACHE / f"{name}.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        saved = datetime.fromisoformat(data["saved"])
        if datetime.now(timezone.utc) - saved < timedelta(days=CACHE_DAYS):
            return data["value"]
    value = fetch()
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"saved": datetime.now(timezone.utc).isoformat(), "value": value},
                            ensure_ascii=False), encoding="utf-8")
    return value


def save_debug(name, data):
    DEBUG.mkdir(parents=True, exist_ok=True)
    (DEBUG / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1)[:2_000_000],
                                        encoding="utf-8")


# ---------------------------------------------------------------- sélection des compétitions
def low(s):
    return (s or "").lower()


def pick_foot(tournaments):
    ids, names = [], {}
    for cat_kw, name_kws in FOOT_TARGETS:
        for t in tournaments:
            n, c = low(t.get("tournamentName")), low(t.get("categoryName"))
            if cat_kw and cat_kw not in c:
                continue
            if any(x in n for x in FOOT_EXCLUDE):
                continue
            if any(n == k or n.startswith(k) for k in name_kws):
                ids.append(t["tournamentId"])
                names[t["tournamentId"]] = t.get("tournamentName")
                break
    return ids, names


def pick_tennis(tournaments):
    ids, names = [], {}
    for t in tournaments:
        n, c = low(t.get("tournamentName")), low(t.get("categoryName"))
        full = f"{c} {n}"
        if not any(k in full for k in TENNIS_INCLUDE):
            continue
        if any(x in full for x in TENNIS_EXCLUDE):
            continue
        if not (t.get("upcomingFixtures") or t.get("liveFixtures")):
            continue
        ids.append(t["tournamentId"])
        names[t["tournamentId"]] = f"{t.get('categoryName')} - {t.get('tournamentName')}"
    return ids, names


# ---------------------------------------------------------------- transformation en lignes CSV
def market_index(markets):
    idx = {}
    for m in markets or []:
        outs = {str(o.get("outcomeId")): o.get("outcomeName") for o in m.get("outcomes", [])}
        idx[str(m.get("marketId"))] = (m.get("marketName"), outs, m.get("handicap"))
    return idx


def build_rows(fixtures, participants, markets, tour_names):
    now = datetime.now(timezone.utc)
    limit = now + timedelta(hours=HOURS_AHEAD)
    paris = timezone(timedelta(hours=2)) if 3 < now.month < 11 else timezone(timedelta(hours=1))
    rows = []
    for fx in fixtures if isinstance(fixtures, list) else fixtures.values():
        try:
            start = datetime.fromisoformat(str(fx.get("startTime")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (now - timedelta(minutes=5) <= start <= limit):
            continue
        p1 = participants.get(str(fx.get("participant1Id")), str(fx.get("participant1Id")))
        p2 = participants.get(str(fx.get("participant2Id")), str(fx.get("participant2Id")))
        loc = start.astimezone(paris)
        bo = fx.get("bookmakerOdds") or {}

        # regroupe: (marketId, outcomeId, playerId) -> {book: prix}
        table = {}
        for book in BOOKS:
            b = bo.get(book) or {}
            if b.get("suspended"):
                continue
            for mid, m in (b.get("markets") or {}).items():
                if m.get("marketActive") is False:
                    continue
                for oid, o in (m.get("outcomes") or {}).items():
                    for pid, p in (o.get("players") or {}).items():
                        if p.get("active") is False or not p.get("price"):
                            continue
                        key = (str(mid), str(oid), str(pid))
                        table.setdefault(key, {"player": p.get("playerName")})[book] = float(p["price"])

        # proba réelle = cotes Pinnacle sans marge (marchés hors joueurs)
        fair = {}
        by_market = {}
        for (mid, oid, pid), v in table.items():
            if pid == "0" and "pinnacle" in v:
                by_market.setdefault(mid, {})[oid] = v["pinnacle"]
        for mid, outs in by_market.items():
            mname, mouts, _ = markets.get(mid, (None, {}, None))
            expected = len(mouts) if mouts else len(outs)
            if len(outs) < max(2, expected):
                continue
            total = sum(1 / x for x in outs.values())
            for oid, price in outs.items():
                fair[(mid, oid)] = (1 / price) / total

        for (mid, oid, pid), v in sorted(table.items()):
            fr = {FR_BOOKS[b]: v[b] for b in FR_BOOKS if b in v}
            if not fr:
                continue  # pari pas dispo sur Winamax ni Unibet
            best_book = max(fr, key=fr.get)
            best = fr[best_book]
            mname, mouts, _ = markets.get(mid, (f"marché {mid}", {}, None))
            proba = fair.get((mid, oid)) if pid == "0" else None
            rows.append({
                "date": loc.strftime("%Y-%m-%d"),
                "heure": loc.strftime("%H:%M"),
                "competition": tour_names.get(fx.get("tournamentId"), fx.get("tournamentId")),
                "match": f"{p1} - {p2}",
                "marche": mname or f"marché {mid}",
                "issue": mouts.get(oid, oid),
                "joueur": v.get("player") if pid != "0" else "",
                "winamax": fr.get("winamax", ""),
                "unibet": fr.get("unibet", ""),
                "pinnacle": v.get("pinnacle", ""),
                "meilleure_cote": best,
                "meilleur_book": best_book,
                "proba_reelle": f"{proba*100:.1f}%" if proba else "",
                "ev_meilleure_cote": f"{(best*proba-1)*100:+.1f}%" if proba else "",
            })
    rows.sort(key=lambda r: (r["date"], r["heure"], r["match"], r["marche"]))
    return rows


def write_csv(rows, name):
    OUT.mkdir(exist_ok=True)
    cols = ["date", "heure", "competition", "match", "marche", "issue", "joueur", "winamax",
            "unibet", "pinnacle", "meilleure_cote", "meilleur_book", "proba_reelle", "ev_meilleure_cote"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for path in (OUT / f"{today}_{name}.csv", OUT / f"latest_{name}.csv"):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
            w.writeheader()
            w.writerows(rows)
    print(f"{name}: {len(rows)} paris écrits")


# ---------------------------------------------------------------- programme principal
def run_sport(sport_id, name, picker, markets, refresh_tournaments_daily):
    fetch_t = lambda: api("tournaments", sportId=sport_id)
    tournaments = fetch_t() if refresh_tournaments_daily else cached(f"tournaments_{sport_id}", fetch_t)
    ids, tour_names = picker(tournaments)
    if not ids:
        print(f"{name}: aucune compétition trouvée")
        save_debug(f"tournaments_{sport_id}", tournaments)
        write_csv([], name)
        return
    print(f"{name}: {len(ids)} compétitions -> {list(tour_names.values())}")
    participants = cached(f"participants_{sport_id}", lambda: api("participants", sportId=sport_id))

    fixtures = []
    for i in range(0, len(ids), 15):
        chunk = ",".join(str(x) for x in ids[i:i + 15])
        data = api("odds-by-tournaments", tournamentIds=chunk, bookmakers=",".join(BOOKS), verbosity=3)
        fixtures.extend(data if isinstance(data, list) else list(data.values()))
    if os.environ.get("ODDSPAPI_DEBUG"):
        save_debug(f"odds_{name}", fixtures[:3])

    # noms inconnus (nouveau joueur) -> on rafraîchit la liste une fois
    missing = any(str(fx.get(k)) not in participants
                  for fx in fixtures for k in ("participant1Id", "participant2Id"))
    if missing:
        participants = api("participants", sportId=sport_id)
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / f"participants_{sport_id}.json").write_text(json.dumps(
            {"saved": datetime.now(timezone.utc).isoformat(), "value": participants},
            ensure_ascii=False), encoding="utf-8")

    write_csv(build_rows(fixtures, participants, markets, tour_names), name)


def main():
    if not KEY:
        sys.exit("Clé API manquante : ajoute le secret ODDSPAPI_KEY dans GitHub.")
    markets = market_index(cached("markets", lambda: api("markets", language="fr")))
    run_sport(SOCCER, "foot", pick_foot, markets, refresh_tournaments_daily=False)
    run_sport(TENNIS, "tennis", pick_tennis, markets, refresh_tournaments_daily=True)
    try:
        acc = api("account")
        print(f"Quota : {acc.get('request_count')} / {acc.get('request_limit')} requêtes utilisées")
    except SystemExit:
        pass
    print(f"Appels API cette exécution : {calls}")


if __name__ == "__main__":
    main()
