"""Preisabfrage für das Hohlkammer Dashboard.

Fragt bei elf Online-Druckereien die Preise ab für
  * Hohlkammerplakate DIN A1 und DIN A0 (10 Lochbohrungen, einseitig 4/0, Standardlieferung)
    in den Auflagen 1, 100, 500, 1.000, 5.000 und 10.000 Stück,
  * 18/1-Großflächenplakate (356 × 252 cm, Affichenpapier Blueback, einseitig 4/0)
    in den Auflagen 1, 3, 5 und 10 Stück,
und schreibt einen Datenstand pro Tag nach data/prices.json.

Preisregeln
  * netto und brutto, jeweils INKLUSIVE Standardversand innerhalb Deutschlands,
    ohne Gutscheine, Rabatte oder Zubehör;
  * Abweichungen vom Standardprodukt stehen als "notes" am Angebot;
  * nicht bestellbare Kombinationen stehen mit Grund unter "unavailable";
  * schlägt ein Abruf fehl, wird der letzte bekannte Preis übernommen und der
    Fehler unter "errors" vermerkt.

Aufruf:  python scraper/fetch_prices.py [--only fla,wmd] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "prices.json"

A1, A0, F181 = "A1", "A0", "18/1"
HK_FORMATS = [A1, A0]                      # Hohlkammerplakate
QTYS = {A1: [1, 100, 500, 1000, 5000, 10000],
        A0: [1, 100, 500, 1000, 5000, 10000],
        F181: [1, 3, 5, 10]}
VAT = 1.19
TIMEOUT = 30
RETRY_WAITS = [20, 60]  # Pausen (s) vor dem 2. und 3. Versuch
STALE_NOTE = "Abruf fehlgeschlagen, Preis vom letzten Datenstand"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})


# ============================================================================ Hilfsfunktionen
def r2(x: float) -> float:
    return round(x + 1e-9, 2)


def de_number(s: str) -> float:
    """'1.558,40' -> 1558.4"""
    return float(s.strip().replace(".", "").replace(",", "."))


def item(shop, fmt, material, qty, net, gross, url, notes=None) -> dict:
    d = {"shop": shop, "format": fmt, "material": material, "qty": qty,
         "net": r2(net), "gross": r2(gross), "url": url}
    if notes:
        d["notes"] = list(notes)
    return d


def from_net(shop, fmt, material, qty, net, url, notes=None) -> dict:
    return item(shop, fmt, material, qty, net, net * VAT, url, notes)


def qtys(fmt, lo=1, hi=math.inf):
    return [q for q in QTYS[fmt] if lo <= q <= hi]


def get(url, **kw):
    r = session.get(url, timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


def post(url, **kw):
    r = session.post(url, timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


def require(items: list, what="keine Preise in der Antwort") -> list:
    if not items:
        raise RuntimeError(what)
    return items


def wmd_select_prices(html: str) -> dict[int, float]:
    """WIRmachenDRUCK: Auflagen-Select 'n Stück (x Euro netto)'; erste Liste = ein Motiv."""
    prices: dict[int, float] = {}
    for qty_s, net_s in re.findall(r"([\d.]+) Stück \(([\d.,]+) Euro netto\)", html):
        prices.setdefault(int(qty_s.replace(".", "")), de_number(net_s))
    return prices


# Registry: JOBS[(format, shop)] = Abruffunktion(fmt) -> list[item]
JOBS: dict[tuple[str, str], Callable[[str], list]] = {}


def job(shop: str, *formats: str):
    def deco(fn):
        for f in formats:
            JOBS[(f, shop)] = fn
        return fn
    return deco


# ================================================================================ Flyeralarm
FLA_URL = "https://www.flyeralarm.com/de/p/wahlplakate-9456837.html"
FLA_API = ("https://www.flyeralarm.com/de/p/9456837/variant?"
           "selection[att.format.conf]={fmt}&selection[att.material.conf]={mat}"
           "&selection[att.farbigkeit.conf]=value.40farbig.conf")
FLA_FORMATS = {A1: "value.dinA1594X84Cm.conf", A0: "value.dinA0.conf"}
FLA_MATERIALS = {
    "value.450GHohlkammerplatteBasic.conf": "450 g Hohlkammerplatte Basic",
    "value.450GHohlkammerplatte.conf": "450 g Hohlkammerplatte",
    "value.450GRecyclingHohlkammerplatte.conf": "450 g Recycling-Hohlkammerplatte",
}
FLA181_URL = "https://www.flyeralarm.com/de/p/181-plakate-22634376.html"
FLA181_API = "https://www.flyeralarm.com/de/p/22634376/variant"


def fla_standard_prices(api_url) -> dict:
    j = get(api_url, timeout=(15, 60), headers={"Accept": "application/json"}).json()
    return {q["amount"]: q["deliveryTypes"]["standard"] for q in j["variantPrice"]["quantities"]}


@job("fla", A1, A0)
def fetch_fla(fmt):
    out = []
    for mat, name in FLA_MATERIALS.items():
        prices = fla_standard_prices(FLA_API.format(fmt=FLA_FORMATS[fmt], mat=mat))
        for qty in qtys(fmt):
            if p := prices.get(qty):  # Cent, inkl. Standardversand
                out.append(item("fla", fmt, name, qty, p["salesPriceNet"] / 100, p["salesPriceGross"] / 100, FLA_URL))
    return require(out)


@job("fla", F181)
def fetch_fla_181(fmt):
    prices = fla_standard_prices(FLA181_API)
    return require([
        item("fla", fmt, "115 g Affichenpapier", q, prices[q]["salesPriceNet"] / 100,
             prices[q]["salesPriceGross"] / 100, FLA181_URL)
        for q in qtys(fmt) if q in prices
    ])


# ============================================================================ WIRmachenDRUCK
WMD_URLS = {fmt: "https://www.wir-machen-druck.de/"
            f"wahlplakat-auf-hohlkammerplatte-din-{fmt.lower()}-einseitig-40farbig-bedruckt-mit-10-bohrungen.html"
            for fmt in HK_FORMATS}
WMD181_URL = "https://www.wir-machen-druck.de/grossflaechenplakat-181-356-x-252-cm-einseitig-40farbig-bedruckt.html"


@job("wmd", A1, A0, F181)
def fetch_wmd(fmt):
    url = WMD181_URL if fmt == F181 else WMD_URLS[fmt]
    material = "120 g Affichenpapier Blueback" if fmt == F181 else "Hohlkammerplatte 3 mm"
    prices = wmd_select_prices(get(url).text)  # netto, Versand inklusive
    return require([from_net("wmd", fmt, material, q, prices[q], url) for q in qtys(fmt) if q in prices],
                   "Auflagenliste nicht gefunden")


# ================================================================================= Saxoprint
SAX_URL = "https://www.saxoprint.de/plakate/hohlkammerplakate-drucken"
SAX_API = "https://api.saxoprint.de/product-configuration/get-product-prices"
SAX_MAX = 1000
SAX_FORMATS = {A1: 42, A0: 41}  # propertyId 9 = Endformat, 44 = Auflage
SAX_CONFIG = [
    (6, 1476), (44, None), (152, 1433), (9, None), (10, 120), (7, 76), (8, 1133), (48, 1436),
    (64, 1438), (153, 1477), (1, 222), (13, 205), (102, 1055), (2, 179), (27, 188),
]


@job("sax", A1, A0)
def fetch_sax(fmt):
    out = []
    for qty in qtys(fmt, hi=SAX_MAX):
        values = {44: qty, 9: SAX_FORMATS[fmt]}
        body = {"productGroupId": 1476, "customerNumber": 0,
                "propertyConfiguration": [{"propertyId": pid, "value": values.get(pid, val)} for pid, val in SAX_CONFIG],
                "printRuns": [], "deliverySplitPrintRuns": []}
        j = post(SAX_API, json=body, headers={"Origin": "https://www.saxoprint.de"}).json()
        out.append(item("sax", fmt, "PP-Hohlkammerplatte 2,5 mm", qty, float(j["priceNet"]), float(j["priceGross"]), SAX_URL))
    return out


# =============================================================================== Drucknische
DN_URLS = {A1: "https://drucknische.de/products/hohlkammerplakate-din-a1",
           A0: "https://drucknische.de/products/a0-plakat"}
DN_MAX = 1000
DN_NOTES = ["12-fach statt 10-fach Lochung"]
DN_QTY_INPUT = 'input[name="properties[Anzahl der gewünschten Plakate]"]'


@job("dn", A1, A0)
def fetch_dn(fmt):
    """Der Preis wird im Browser von einem Rechner-Plugin berechnet, daher Playwright."""
    from playwright.sync_api import sync_playwright

    url, out = DN_URLS[fmt], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(locale="de-DE", user_agent=UA)
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_selector(DN_QTY_INPUT, state="attached", timeout=30000)
            for qty in qtys(fmt, hi=DN_MAX):
                page.evaluate(
                    """([sel, v]) => {
                        const inp = document.querySelector(sel);
                        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(inp, String(v));
                        for (const e of ['input', 'change', 'keyup', 'blur']) inp.dispatchEvent(new Event(e, {bubbles: true}));
                    }""",
                    [DN_QTY_INPUT, qty],
                )
                page.wait_for_timeout(3000)
                m = re.search(r"Preis inkl\. gesetzl\. Mwst\.\s*€\s*([\d.,]+)", page.inner_text("body"))
                if not m:
                    raise RuntimeError("Preis auf der Seite nicht gefunden")
                gross = float(m.group(1).replace(",", ""))  # Format "€ 2203.88"
                out.append(item("dn", fmt, "PP-Hohlkammerplatte 2,5 mm", qty, gross / VAT, gross, url, DN_NOTES))
        finally:
            browser.close()
    return out


# ============================================================================= highway2print
H2P_AJAX = "https://www.highway2print.de/wp-admin/admin-ajax.php"
H2P_PRODUCTS = {
    # Format: Produktseite, product_id, kg pro Plakat, Mindestmenge, Lochung, Material, Hinweise
    A1: ("https://www.highway2print.de/produkt/hohlkammerplakat-din-a1/", "180", 0.225, 1,
         "10-fach-Lochung", "Hohlkammerplatte 2,5 mm", []),
    A0: ("https://www.highway2print.de/produkt/hohlkammerplakat-din-a0/", "177", 0.450, 2,
         "10-fach-Lochung", "Hohlkammerplatte 2,5 mm", []),
    F181: ("https://www.highway2print.de/produkt/grossflaechen-plakat-18-1-plakate/", "438", 1.077, 1,
           "", "115 g Plakatpapier Blueback", ["4 Teile, gefalzt und gemappt"]),
}
# Versand brutto laut https://www.highway2print.de/versandarten/ (Stand 10/2026)
H2P_DPD = [(3, 10.71), (10, 14.28), (15, 17.85), (20, 21.42), (25, 23.80), (30, 29.75)]
H2P_FREIGHT = [
    (100, 91.63), (200, 116.62), (300, 128.52), (400, 183.26), (500, 224.91), (600, 249.90),
    (700, 265.37), (800, 284.41), (900, 297.50), (1000, 314.16), (1250, 364.14), (1500, 383.18),
    (1750, 421.26), (2000, 458.15), (2500, 529.55),
]


def h2p_shipping_gross(kg: float) -> tuple[float, str]:
    for limit, price in H2P_DPD:
        if kg <= limit:
            return price, "Versand nach Gewichtstabelle berechnet"
    for limit, price in H2P_FREIGHT:
        if kg <= limit:
            return price, "Versand per Spedition, nach Gewichtstabelle berechnet"
    max_kg, max_price = H2P_FREIGHT[-1]  # über 2.500 kg: mehrere volle Speditionssendungen
    n = math.ceil(kg / max_kg)
    return n * max_price, f"Versand geschätzt ({n} Speditionssendungen, über Versandtabelle hinaus)"


@job("h2p", A1, A0, F181)
def fetch_h2p(fmt):
    url, product_id, kg_each, min_qty, lochung, material, base_notes = H2P_PRODUCTS[fmt]
    m = re.search(r'highwayOfferNonce\s*=\s*"([^"]+)"', get(url).text)
    if not m:
        raise RuntimeError("Nonce nicht gefunden")
    out = []
    for qty in qtys(fmt, lo=min_qty):
        data = {"action": "highway_live_offer", "nonce": m.group(1), "product_id": product_id,
                "total_qty": str(qty), "motif_count": "1", "lochung": lochung, "datencheck": "ohne"}
        j = post(H2P_AJAX, data=data, headers={"Referer": url}).json()
        if not j.get("success"):
            raise RuntimeError(f"Antwort ohne Erfolg für {qty} Stück: {str(j.get('data'))[:120]}")
        net_goods = de_number(re.sub(r"<[^>]+>|&nbsp;|&euro;|€", "", j["data"]["net_total"]))
        ship_gross, note = h2p_shipping_gross(qty * kg_each)
        out.append(from_net("h2p", fmt, material, qty, net_goods + ship_gross / VAT, url, base_notes + [note]))
    return out


# ============================================================================ wahlplakatshop
WPS_AJAX = "https://www.wahlplakatshop.de/?wc-ajax=get_variation"
WPS_PRODUCTS = {
    # Format: Produktseite, product_id, Material, Hinweise, Mindest-, Höchstmenge
    A1: ("https://www.wahlplakatshop.de/product/hohlkammerplakate-a1/", "24",
         "Hohlkammer-Stegplatte 2,5 mm", None, 10, 5000),
    A0: ("https://www.wahlplakatshop.de/product/hohlkammerplakate-a0/", "75",
         "Hohlkammer-Stegplatte 2,5 mm", ["12-fach statt 10-fach Lochung"], 10, 5000),
    F181: ("https://www.wahlplakatshop.de/affichenpapier-18-1-plakate/", "13042",
           "115 g Affichenpapier Blueback", ["4 Teile"], 1, 100),
}


@job("wps", A1, A0, F181)
def fetch_wps(fmt):
    url, product_id, material, notes, q_min, q_max = WPS_PRODUCTS[fmt]
    out = []
    for qty in qtys(fmt, q_min, q_max):
        j = post(WPS_AJAX, data={"product_id": product_id, "attribute_menge": f"{qty} Stk."}).json()
        if not j or "display_price" not in j:
            raise RuntimeError(f"keine Variante für {qty} Stück")
        out.append(from_net("wps", fmt, material, qty, float(j["display_price"]), url, notes))  # netto inkl. Versand
    return out


# ==================================================================================== jajabo
JJB_URL = "https://www.jajabo.de/wahlplakate.htm"
JJB_FORMATS = {A1: "8468", A0: "8469"}  # Parameter "sorten"


@job("jjb", A1, A0)
def fetch_jjb(fmt):
    """Staffelpreistabelle steht statisch im HTML; das Format wählt ?sorten=. Versand gratis."""
    sorte = JJB_FORMATS[fmt]
    html = get(JJB_URL, params={"sorten": sorte}).text
    if not re.search(rf"addSelectedListValue\('sorten',\s*{sorte},\s*'DIN {fmt}'", html):
        raise RuntimeError(f"Format DIN {fmt} ist auf der Seite nicht ausgewählt")
    rows = re.findall(
        r'data-value="(\d+)"[^>]*>\s*<td class="quantity">[^<]*</td>\s*'
        r'<td class="price_netto">\s*([\d.,]+)\s*EUR</td>\s*'
        r'<td class="price_brutto">\s*([\d.,]+)\s*EUR</td>', html)
    prices = {int(q): (de_number(n), de_number(g)) for q, n, g in rows}
    url = f"{JJB_URL}?sorten={sorte}"
    return require([item("jjb", fmt, "PP-Wellenstrukturplatte 3 mm", q, *prices[q], url)
                    for q in qtys(fmt) if q in prices], "Preistabelle nicht gefunden")


# =================================================================================== print24
P24_URL = "https://print24.com/de/druckprodukte/plakate/wahlplakate"
P24_API = "https://print24.com/api/de/"
P24_FORMATS = {A1: "184", A0: "314"}
P24_QTY_IDS = {1: "340", 100: "400", 500: "350", 1000: "444", 5000: "499", 10000: "399"}
P24_PROPERTIES = [  # Hohlkammerplatte 450 g, 10-fach Lochung 7 mm, 4/0, ohne Zubehör/Proof
    ("availability", "3765"), ("quality", "4560"), ("format", None), ("aspect_ratio", "6309"),
    ("material_spec", "7953"), ("farben", "115"), ("verarbeitung", "7954"), ("finishing", "6307"),
    ("finishing_desc", "24"), ("finishing_size", "4016"), ("accessories", "6003"), ("proofformat", "823"),
    ("proofpages", "840"), ("motive", "27"),
]


@job("p24", A1, A0)
def fetch_p24(fmt):
    """Preis-Matrix-API mit kurzlebigem Token; total_* enthält den Versand."""
    headers = {"portal": "print24", "Accept": "application/json, text/plain, */*"}
    headers["auth"] = get(P24_API + "token/create/expiry", headers=headers).json()["token"]
    body = {"delivery_country_code": "DE", "product_alias_id": 111,
            "properties": [{"id": P24_FORMATS[fmt] if pid is None else pid, "name": n} for n, pid in P24_PROPERTIES],
            "quantities": [P24_QTY_IDS[q] for q in qtys(fmt)], "premium_file_check": 0}
    out = []
    for row in post(P24_API + "itemmaster/calculation/price-matrix", json=body, headers=headers).json():
        types = row["quantity"]["shipping_type"]
        price = next((t for t in types if t.get("delivery_type") == "standard"), types[0])["price"]
        qty, fp = int(price["quantity_value"]), price["final_prices"]
        if qty in QTYS[fmt]:
            out.append(item("p24", fmt, "Hohlkammerplatte 450 g, 2,5 mm", qty,
                            fp["total_net_value"], fp["total_gross_value"], P24_URL))
    return require(out)


# ================================================================================= maxxprint
# Seit der Umstellung auf Shopware 6 (Mitte Oktober 2026) führt maxxprint keine
# Hohlkammer-Wahlplakate mehr (alte Produktseite /wahlplakate liefert 404, in der Sitemap gibt
# es kein Nachfolgeprodukt). A1/A0 stehen deshalb unter "unavailable".
# Das 18/1-Plakat ist umgezogen und wird über den neuen Produktkonfigurator berechnet.
MX181_URL = "https://maxxprint.de/18-1-plakat-mp10040/"
MX_CONFIG_API = "https://maxxprint.de/widgets/product-configurator"
MX181_CONFIGURATOR = "019db51cbf0b7025bb1c249583d026e4"
MX181_STANDARD = "01a0f695658474538638597d9882a869"  # Lieferart "Standard"
MX181_MAX = 50  # online höchstens 50 Stück je Motiv (bisherige Shopregel)


@job("mx", F181)
def fetch_mx_181(fmt):
    html = get(MX181_URL).text
    if MX181_CONFIGURATOR not in html or MX181_STANDARD not in html:
        raise RuntimeError("Konfigurator oder Lieferart 'Standard' auf der Produktseite nicht gefunden")
    out = []
    for qty in qtys(fmt, hi=MX181_MAX):
        body = {"width": 0, "height": 0, "quantity": qty, "configuratorId": MX181_CONFIGURATOR,
                "configuration": {"1": MX181_STANDARD}, "shippingOptionIds": [MX181_STANDARD]}
        j = post(MX_CONFIG_API, json=body, headers={"Referer": MX181_URL, "Accept": "application/json"}).json()
        net = float(j["subtotalNet"]) + float(j.get("shippingCostsNet") or 0)  # laut Shop inkl. Versand DE
        gross = float(j["subtotalGross"]) + float(j.get("shippingCostsGross") or 0)
        if net <= 0:
            raise RuntimeError(f"kein Preis für {qty} Stück")
        out.append(item("mx", fmt, "115 g Blueback-Affichenpapier", qty, net, gross, MX181_URL,
                        ["4 Teile, verklebefertig gemappt"]))
    return out


# =============================================================================== Bannerkönig
BK_CALC = "https://www.bannerkoenig.de/"
BK_SHIP_MIN_NET = 6.90  # Mindestversand laut Versandseite; genaue Kosten erst im Warenkorb
BK_PRODUCTS = {
    # Format: Produktseite, product_id, Material-Key, Größen-Key, Material, Hinweise
    A1: ("https://www.bannerkoenig.de/shop/wahlplakat-hohlkammerplatte/", "2558888", "hohlkammerplatte_3mm",
         "din_a1", "Hohlkammerplatte 3 mm", ["ohne Lochung (nicht wählbar)"]),
    A0: ("https://www.bannerkoenig.de/shop/wahlplakat-hohlkammerplatte/", "2558888", "hohlkammerplatte_3mm",
         "din_a0", "Hohlkammerplatte 3 mm", ["ohne Lochung (nicht wählbar)"]),
    F181: ("https://www.bannerkoenig.de/shop/18-1-grossflaechenplakat/", "2593015", "blueback_affichenpapier",
           "18_1_356x252cm", "120 g Blueback-Affichenpapier", []),
}


@job("bk", A1, A0, F181)
def fetch_bk(fmt):
    url, product_id, material_key, size_key, material, notes = BK_PRODUCTS[fmt]
    notes = notes + ["Versand: nur Mindestbetrag 6,90 € eingerechnet"]
    m = re.search(r'bkcmz_core_ajax\s*=\s*\{[^}]*"nonce":"([a-f0-9]+)"', get(url).text)
    if not m:
        raise RuntimeError("Nonce nicht gefunden")
    out = []
    for qty in qtys(fmt):
        data = {"product_id": product_id, "erp_cpo_material": material_key, "erp_cpo_druck": "einseitiger_druck",
                "erp_cpo_groesse": size_key, "erp_cpo_menge": str(qty)}
        j = post(BK_CALC, params={"bkcmz_perform_calculations": "1", "nonce": m.group(1)}, data=data,
                 headers={"Referer": url}).json()
        if not j.get("success"):
            raise RuntimeError(f"Berechnung fehlgeschlagen für {qty} Stück")
        out.append(from_net("bk", fmt, material, qty, float(j["data"]["price_netto"]) + BK_SHIP_MIN_NET, url, notes))
    return out


# ================================================================================ myDisplays
MD_API = "https://www.mydisplays.net/website_product_configurator/onchange"
MD_SHIP_PER_30KG = 9.90  # Standardversand DE je Paket bis 30 kg (https://www.mydisplays.net/versandkosten)
MD_SHIP_NOTE = "Versand geschätzt (9,90 € je 30-kg-Paket)"
MD_PRODUCTS = {
    # Format: Produktseite, Template, feste Optionen, Zusatzwerte, kg je Stück, Material, Hinweise
    # Hohlkammer: Konfektion, Zuschnitt rechteckig, 2,5 mm Platte, ohne Bohrungen, Basis-Datencheck + Größe
    A1: ("https://www.mydisplays.net/wahlplakat-hohlkammer", 3926, [643, 370, 567, 380, 316, 1099], {}, 0.225,
         "Hohlkammerplatte 2,5 mm", ["ohne Lochung (nur 4 Eckbohrungen wählbar)"]),
    A0: ("https://www.mydisplays.net/wahlplakat-hohlkammer", 3926, [643, 370, 567, 380, 316, 1100], {}, 0.45,
         "Hohlkammerplatte 2,5 mm", ["ohne Lochung (nur 4 Eckbohrungen wählbar)"]),
    # 18/1: Affichenpapier im Freiformat – Konfektion, 130 g Blueback, Rolle, Basis-Datencheck; 8,97 m² × 130 g/m²
    F181: ("https://www.mydisplays.net/affichen-papier", 3931, [592, 571, 531, 316], {"57": "356", "58": "252"}, 1.17,
           "130 g Affichenpapier Blueback", ["Freiformat 356 × 252 cm, in Bahnen bis 130 cm gedruckt"]),
}


@job("md", A1, A0, F181)
def fetch_md(fmt):
    url, template, selected, extra, kg_each, material, notes = MD_PRODUCTS[fmt]
    out = []
    for qty in qtys(fmt):
        payload = {"id": 1, "jsonrpc": "2.0", "method": "call", "params": {
            "product_id": template, "selected_ids": selected,
            "custom_values": {"52": str(qty), **extra, "56": "1", "93": False}}}
        res = post(MD_API, json=payload, headers={"Referer": url}).json().get("result")
        if not res:
            raise RuntimeError(f"keine Antwort für {qty} Stück")
        ship = math.ceil(qty * kg_each / 30) * MD_SHIP_PER_30KG
        out.append(from_net("md", fmt, material, qty, float(res["prices"]["price"]) + ship, url, notes + [MD_SHIP_NOTE]))
    return out


# ======================================================================= nicht verfügbar
def na(shop, reason, formats, qty_list=None):
    return [{"shop": shop, "format": f, "qty": q, "reason": reason}
            for f in formats for q in (qty_list or QTYS[f])]


MAX_ONLINE = "online höchstens {} Stück"
UNAVAILABLE = [
    *na("wps", "Mindestbestellmenge 10 Stück", HK_FORMATS, [1]),
    *na("h2p", "Mindestbestellmenge 2 Stück", [A0], [1]),
    *na("fla", MAX_ONLINE.format("5.000"), HK_FORMATS, [10000]),
    *na("sax", MAX_ONLINE.format("1.000"), HK_FORMATS, [5000, 10000]),
    *na("dn", MAX_ONLINE.format("1.000"), HK_FORMATS, [5000, 10000]),
    *na("wps", "online höchstens 5.000 Stück, größere Mengen auf Anfrage", HK_FORMATS, [10000]),
    *na("mx", "kein Hohlkammer-Wahlplakat mehr im Sortiment (seit Shop-Umstellung Oktober 2026)", HK_FORMATS),
    # 18/1-Großflächenplakate
    *na("p24", "18/1 erst ab 100 Stück (Offsetdruck)", [F181]),
    *na("sax", "kein 18/1-Plakat im Sortiment (höchstens DIN A0)", [F181]),
    *na("dn", "kein 18/1-Plakat im Sortiment (Affichenpapier höchstens DIN A0)", [F181]),
    *na("jjb", "kein 18/1-Plakat im Sortiment", [F181]),
]


# ===================================================================================== Ablauf
def with_retries(fn, fmt, shop):
    for attempt, wait in enumerate([0, *RETRY_WAITS], start=1):
        if wait:
            print(f"WARTE  {fmt} {shop}: {wait}s vor Versuch {attempt}", flush=True)
            time.sleep(wait)
        try:
            return fn(fmt)
        except Exception as exc:  # noqa: BLE001
            if attempt > len(RETRY_WAITS):
                raise
            print(f"RETRY  {fmt} {shop}: Versuch {attempt} fehlgeschlagen: {str(exc)[:200]}", flush=True)


def last_known(snapshots, shop, fmt, today):
    """Preise des jüngsten Datenstands, der für Shop und Format Angebote enthält."""
    for s in reversed(snapshots):
        if s["date"] == today:
            continue
        old = [i for i in s.get("items", []) if i["shop"] == shop and i.get("format", A1) == fmt]
        if old:
            return old
    return []


def is_unavailable(shop, fmt, qty):
    return any(u["shop"] == shop and u["format"] == fmt and u["qty"] == qty for u in UNAVAILABLE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="nur diese Shop-Kürzel abfragen, z. B. fla,mx")
    ap.add_argument("--dry-run", action="store_true", help="nichts speichern")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    data = json.loads(DATA_FILE.read_text("utf-8")) if DATA_FILE.exists() else {"snapshots": []}
    snapshots = sorted(data.get("snapshots", []), key=lambda s: s["date"])
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("Europe/Berlin")).date().isoformat()
    todays = next((s for s in snapshots if s["date"] == today), None)

    items, errors, ran = [], [], 0
    for (fmt, shop), fn in JOBS.items():
        if only and shop not in only:
            # Teillauf: übrige Shops aus dem heutigen Datenstand bzw. dem letzten bekannten übernehmen
            keep = [i for i in (todays or {}).get("items", []) if i["shop"] == shop and i.get("format") == fmt]
            items.extend(keep or last_known(snapshots, shop, fmt, today))
            errors.extend(e for e in (todays or {}).get("errors", []) if e["shop"] == shop and e["format"] == fmt)
            continue
        ran += 1
        try:
            got = [i for i in with_retries(fn, fmt, shop) if not is_unavailable(shop, fmt, i["qty"])]
            items.extend(got)
            print(f"OK     {fmt:4} {shop:4} {len(got)} Preise", flush=True)
        except Exception as exc:  # noqa: BLE001 – ein Shop darf die anderen nicht stoppen
            traceback.print_exc()
            errors.append({"shop": shop, "format": fmt, "message": str(exc)[:300]})
            old = last_known(snapshots, shop, fmt, today)
            for i in old:
                i = dict(i, format=fmt)
                i["notes"] = [n for n in i.get("notes", []) if n != STALE_NOTE] + [STALE_NOTE]
                items.append(i)
            print(f"FEHLER {fmt:4} {shop:4} {exc} (übernehme {len(old)} alte Preise)", flush=True)

    if ran and len([e for e in errors if not only or e["shop"] in only]) == ran:
        print("Alle Abfragen fehlgeschlagen, Datei bleibt unverändert.")
        return 1

    order = {f: n for n, f in enumerate([A1, A0, F181])}
    items.sort(key=lambda i: (order.get(i["format"], 9), i["shop"], i["material"], i["qty"]))
    snapshot = {"date": today, "updated": now.isoformat(timespec="seconds"), "items": items, "unavailable": UNAVAILABLE}
    if errors:
        snapshot["errors"] = errors
    snapshots = [s for s in snapshots if s["date"] != today] + [snapshot]
    print(f"Datenstand {today}: {len(items)} Preise, {len(errors)} Fehler, {len(snapshots)} Datenstände")
    if args.dry_run:
        print("--dry-run: nichts gespeichert")
        return 0
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps({"snapshots": snapshots}, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
