"""PokéScalp FR — bot d'alertes restock (une passe par exécution, pour GitHub Actions).

Surveille les boutiques Shopify (catalogue JSON public) et poste sur Discord
quand un produit qui correspond aux mots-clés est disponible :
  - nouveau produit en ligne (ex. ouverture de précommande)
  - retour en stock (rupture -> disponible)
Le webhook Discord est lu dans la variable d'environnement DISCORD_WEBHOOK.
"""
import json
import os
import sys
import time
import unicodedata

import requests

CONFIG = json.load(open("config.json", encoding="utf-8"))
STATE_FILE = "state.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}


def norm(s):
    """Minuscules, sans accents : 'Règne Delta' -> 'regne delta'."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


KEYWORDS = [norm(k) for k in CONFIG["keywords"]]
EXCLUDE = [norm(k) for k in CONFIG.get("exclude", [])]


def matches(title):
    t = norm(title)
    return any(k in t for k in KEYWORDS) and not any(x in t for x in EXCLUDE)


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False, sort_keys=True)


def shopify_products(base):
    """Tous les produits d'une boutique Shopify (pagination, 250 par page)."""
    out = []
    for page in range(1, CONFIG.get("max_pages", 8) + 1):
        r = requests.get(f"{base}/products.json", params={"limit": 250, "page": page},
                         headers=HEADERS, timeout=20)
        r.raise_for_status()
        batch = r.json().get("products", [])
        if not batch:
            break
        out.extend(batch)
        time.sleep(1)
    return out


def send_alert(webhook, kind, shop, p, base):
    prices = [float(v["price"]) for v in p.get("variants", []) if v.get("price")]
    price = f"{min(prices):.2f} €".replace(".", ",") if prices else "?"
    img = (p.get("images") or [{}])[0].get("src")
    title = {"new": "🆕 NOUVEAU / PRÉCO", "restock": "🔥 RESTOCK"}[kind]
    embed = {
        "title": f"{title} : {p['title']}"[:256],
        "url": f"{base}/products/{p['handle']}",
        "color": 0xFFCB05 if kind == "restock" else 0x3498DB,
        "fields": [
            {"name": "Boutique", "value": shop, "inline": True},
            {"name": "Prix", "value": price, "inline": True},
        ],
        "footer": {"text": "PokéScalp FR • vérifie toujours avant d'acheter"},
    }
    if img:
        embed["thumbnail"] = {"url": img}
    payload = {"content": CONFIG.get("ping", ""), "embeds": [embed],
               "allowed_mentions": {"parse": ["roles", "everyone"]}}
    r = requests.post(webhook, json=payload, timeout=15)
    if r.status_code == 429:  # limite Discord : on attend et on réessaie une fois
        time.sleep(float(r.json().get("retry_after", 2)))
        r = requests.post(webhook, json=payload, timeout=15)
    r.raise_for_status()
    time.sleep(1)


def run(webhook, dry_run=False):
    state = load_state()
    first_run = "_init" not in state  # 1re exécution : on mémorise sans alerter (pas de spam)
    alerts = 0
    for store in CONFIG["shopify_stores"]:
        shop, base = store["name"], store["url"].rstrip("/")
        try:
            products = shopify_products(base)
        except Exception as e:  # une boutique en panne ne bloque pas les autres
            print(f"[!] {shop} : {e}")
            continue
        for p in products:
            if not matches(p["title"]):
                continue
            key = f"{base}#{p['id']}"
            available = any(v.get("available") for v in p.get("variants", []))
            before = state.get(key)
            state[key] = available
            if first_run or not available:
                continue
            kind = "new" if before is None else ("restock" if before is False else None)
            if kind:
                print(f"[ALERTE {kind}] {shop} : {p['title']}")
                alerts += 1
                if not dry_run:
                    send_alert(webhook, kind, shop, p, base)
    state["_init"] = True
    save_state(state)
    print(f"OK : {len(state) - 1} produits suivis, {alerts} alerte(s)"
          + (" — 1re exécution, état mémorisé sans alerte" if first_run else ""))


if __name__ == "__main__":
    hook = os.environ.get("DISCORD_WEBHOOK", "")
    dry = "--dry-run" in sys.argv
    if not hook and not dry:
        sys.exit("DISCORD_WEBHOOK manquant (ajoute-le dans les Secrets GitHub).")
    run(hook, dry_run=dry)
