# Hohlkammer Dashboard

Preisvergleich für Wahlplakate bei elf Online-Druckereien. Läuft als statische Seite auf GitHub Pages;
eine GitHub Action fragt die Preise jeden Morgen automatisch ab.

**Verglichen werden:**

| Format | Produkt | Auflagen |
|---|---|---|
| DIN A1 (594 × 841 mm) | Hohlkammerplatte, 10 Lochbohrungen, einseitig 4/0, Standardlieferung | 1, 100, 500, 1.000, 5.000, 10.000 |
| DIN A0 (841 × 1189 mm) | Hohlkammerplatte, 10 Lochbohrungen, einseitig 4/0, Standardlieferung | 1, 100, 500, 1.000, 5.000, 10.000 |
| 18/1-Großfläche (356 × 252 cm) | Affichenpapier Blueback, einseitig 4/0 | 1, 3, 5, 10 |

**Shops (Kürzel):** Flyeralarm (`fla`), WIRmachenDRUCK (`wmd`), Saxoprint (`sax`), Drucknische (`dn`), highway2print (`h2p`),
wahlplakatshop (`wps`), jajabo (`jjb`), print24 (`p24`), maxxprint (`mx`), Bannerkönig (`bk`), myDisplays (`md`).
18/1 nur bei fla, wmd, h2p, wps, mx, bk und md.

**Preisregeln:** netto und brutto, jeweils inklusive Standardversand innerhalb Deutschlands, ohne Gutscheine, Rabatte oder Zubehör
(z. B. Kabelbinder). Abweichungen vom Standardprodukt (andere Lochung, geschätzter Versand, andere Plattenstärke) stehen als Hinweis am Angebot.
Nicht bestellbare Kombinationen werden mit Grund gespeichert. Schlägt ein Abruf fehl, bleibt der letzte bekannte Preis stehen und der Fehler wird vermerkt.

Direktlinks zu einem Format: `…/#A1`, `…/#A0`, `…/#18/1`.

## Aufbau

| Datei | Zweck |
|---|---|
| `index.html` | Die Webseite: Ranking, Hinweise, Preisverlauf, alle Datenstände. Lädt `data/prices.json`. |
| `data/prices.json` | Alle gespeicherten Preise, ein Datenstand pro Tag (seit 07.10.2026). |
| `scraper/fetch_prices.py` | Fragt die Shops ab und schreibt den Datenstand des Tages. |
| `scraper/requirements.txt` | Python-Abhängigkeiten (requests, Playwright). |
| `.github/workflows/update-prices.yml` | Startet die Abfrage täglich und auf Knopfdruck, committet nur bei Änderungen. |
| `.nojekyll` | GitHub Pages liefert die Dateien unverändert aus. |

### Datenformat `data/prices.json`

```json
{ "snapshots": [ {
    "date": "2026-11-05",
    "updated": "2026-11-05T04:21:10+00:00",
    "items":       [ { "shop": "p24", "format": "A1", "material": "Hohlkammerplatte 450 g, 2,5 mm", "qty": 100,
                       "net": 201.09, "gross": 239.30, "url": "https://…", "notes": ["…"] } ],
    "unavailable": [ { "shop": "sax", "format": "A1", "qty": 5000, "reason": "online höchstens 1.000 Stück" } ],
    "errors":      [ { "shop": "p24", "format": "A0", "message": "…" } ]
} ] }
```

`format` ist `"A1"`, `"A0"` oder `"18/1"`; `notes` und `errors` fehlen, wenn es nichts zu melden gibt.
Ein Datenstand pro Tag (Datum nach deutscher Zeit). Läuft die Abfrage am selben Tag erneut, ersetzt sie den Datenstand dieses Tages.

## Einrichtung (einmalig)

1. **GitHub Pages einschalten:** Repository → *Settings* → *Pages* → *Build and deployment* → *Source*: „Deploy from a branch“,
   Branch **`main`**, Ordner **`/ (root)`** → *Save*. Nach ein bis zwei Minuten ist die Seite unter
   `https://<konto>.github.io/<repository>/` erreichbar, hier also `https://twgkrueger3.github.io/Hohlkammer-Dashboard/`.
2. **Actions Schreibrechte geben:** Repository → *Settings* → *Actions* → *General* → *Workflow permissions*:
   **Read and write permissions** → *Save*. Ohne das kann die Action `data/prices.json` nicht committen.
3. **Ersten Lauf starten:** Reiter *Actions* → falls nötig „I understand my workflows, go ahead and enable them“ →
   Workflow „Preise aktualisieren“ → *Run workflow* → Branch `main` → *Run workflow*. Ein Lauf dauert etwa 5 bis 10 Minuten.
   Fehlt `data/prices.json` noch, übernimmt dieser erste Lauf einmalig alle bisherigen Datenstände aus dem alten Repository
   [twgkrueger/Hohlkammer-Dashboard](https://github.com/twgkrueger/Hohlkammer-Dashboard) und hängt den heutigen an.
   Bis dahin zeigt die Seite „Preisdaten nicht erreichbar“.

## Preise aktualisieren

- **Automatisch:** täglich um 04:17 UTC (5:17 Uhr Winterzeit, 6:17 Uhr Sommerzeit). GitHub startet geplante Läufe manchmal mit etwas Verspätung.
- **Von Hand:** Knopf „Preise aktualisieren“ auf der Seite. Er öffnet die Actions-Seite des Workflows; dort *Run workflow* wählen
  (nur mit Schreibrechten am Repository). Das Repository ermittelt die Seite aus der github.io-Adresse; bei einer eigenen Domain
  den Eintrag `<meta name="github-repo" content="konto/repository">` in `index.html` einkommentieren.

Der Workflow committet `data/prices.json` nur, wenn sich etwas geändert hat. Er deployt selbst nichts: GitHub Pages veröffentlicht den
neuen Stand anschließend automatisch aus dem Branch `main`. Fällt Pages aus, läuft die Datenabfrage trotzdem weiter.

Schlägt ein Shop fehl (z. B. nach einem Umbau seiner Seite), wird jeder Abruf zweimal wiederholt (nach 20 und 60 Sekunden).
Klappt es dann nicht, übernimmt das Skript die letzten bekannten Preise, markiert sie und vermerkt den Fehler.
Die Seite zeigt das unter „Hinweise“; Details stehen im Protokoll des Actions-Laufs.

## Hinweise zu einzelnen Shops

### Hohlkammerplakate A1/A0

| Shop | Abruf | Besonderheiten |
|---|---|---|
| Flyeralarm | Varianten-API (JSON, Cent) | drei Materialien (Basic, Standard, Recycling), je 450 g; online höchstens 5.000 Stück; Versand inklusive |
| WIRmachenDRUCK | Auflagenliste im HTML | Hohlkammerplatte 3 mm; Versand inklusive |
| Saxoprint | Preis-API (POST) | PP-Hohlkammerplatte 2,5 mm; online höchstens 1.000 Stück |
| Drucknische | **Playwright** (Preis wird im Browser berechnet) | 12-fach statt 10-fach Lochung; online höchstens 1.000 Stück |
| highway2print | Live-Angebot per WordPress-AJAX (Nonce) | Versand kommt extra und wird nach Gewichtstabelle des Shops hinzugerechnet (A1 0,225 kg, A0 0,45 kg je Plakat); über 2.500 kg geschätzt. A0 erst ab 2 Stück |
| wahlplakatshop | WooCommerce-Variante | online 10 bis 5.000 Stück; A0 nur mit 12-fach-Stanzung; Preis netto inkl. Versand |
| jajabo | Staffelpreistabelle im HTML (`?sorten=`) | PP-Wellenstrukturplatte 3 mm; Versand gratis |
| print24 | Preis-Matrix-API mit kurzlebigem **Token** | Hohlkammerplatte 450 g, 10-fach Lochung 7 mm; Versand inklusive |
| maxxprint | – | **seit der Shop-Umstellung im Oktober 2026 keine Hohlkammer-Wahlplakate mehr im Sortiment** (alte Seite 404, kein Nachfolgeprodukt); als „nicht verfügbar“ geführt |
| Bannerkönig | Rechner-AJAX (Nonce) | keine Lochung wählbar; Versand erst im Warenkorb, eingerechnet nur der Mindestbetrag 6,90 € netto |
| myDisplays | Konfigurator-JSON-RPC | keine 10-fach-Lochung (nur 4 Eckbohrungen); Versand geschätzt mit 9,90 € je 30-kg-Paket |

### 18/1-Großflächenplakate

| Shop | Material | Besonderheiten |
|---|---|---|
| Flyeralarm | 115 g Affichenpapier | eigenes Produkt „18/1 Plakate“, Versand inklusive |
| WIRmachenDRUCK | 120 g Affichenpapier Blueback | Versand inklusive |
| highway2print | 115 g Plakatpapier Blueback | 4 Teile; Versand nach Gewichtstabelle (1,077 kg je Plakat) |
| wahlplakatshop | 115 g Affichenpapier Blueback | 4 Teile; 1–100 Stück, Versand inklusive |
| maxxprint | 115 g Blueback-Affichenpapier | neuer Shopware-Produktkonfigurator (`/widgets/product-configurator`), Lieferart „Standard“, Versand inklusive; 4 Teile, verklebefertig gemappt |
| Bannerkönig | 120 g Blueback-Affichenpapier | Versand erst im Warenkorb, eingerechnet nur 6,90 € netto |
| myDisplays | 130 g Affichenpapier Blueback | Freiformat 356 × 252 cm, in Bahnen bis 130 cm gedruckt; Versand geschätzt |

Nicht enthalten: **print24** (18/1 erst ab 100 Stück), **Saxoprint**, **Drucknische** und **jajabo** (kein 18/1-Format).
**Salierdruck** bietet Wahlplakate nur in 120 × 80, 140 × 100 und 170 × 120 cm an und ist deshalb nicht im Vergleich.

## Lokal testen

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r scraper/requirements.txt
python -m playwright install chromium
python scraper/fetch_prices.py              # alle Shops; --dry-run speichert nichts
python scraper/fetch_prices.py --only mx    # nur einzelne Shops neu abfragen, Rest bleibt
python -m http.server 8000                  # dann http://localhost:8000 öffnen
```

Der Knopf „Preise aktualisieren“ erscheint nur auf github.io (oder mit dem `github-repo`-Meta-Eintrag).
