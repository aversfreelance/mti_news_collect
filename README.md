# MTI → Pest Megyei Hírlap JSON import

Ez egy Playwright-alapú scraper-váz, amely:
1. belép az mti.hu felületére,
2. megpróbálja kigyűjteni a 15 legfrissebb hírt,
3. egyszerű, óvatos szövegátírást végez,
4. létrehozza a Pest Megyei Hírlap admin importjához használható JSON-fájlt.

## Telepítés

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Töltsd ki a `.env` fájlt.

## Futtatás

```bash
python mti_scrape_to_pmh_json.py
```

A kimenet alapból:

```text
public/import/pest-megye-news.json
```

Ezt GitHubra feltöltve raw URL-ként be tudod tölteni az admin felületen.

## Fontos

A bejelentkezési adatokat ne tedd bele publikus GitHub repóba. Használj `.env` fájlt helyben, GitHub Actions esetén pedig GitHub Secrets-et.
