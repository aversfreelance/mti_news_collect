# MTI rovat scraper → Pest Megyei Hírlap import JSON

Ez a csomag belépés után rovatonként legfeljebb 5 friss MTI-cikket gyűjt le:

- Közélet: `https://mti.hu/kozelet`
- Gazdaság: `https://mti.hu/gazdasag`
- Külföld: `https://mti.hu/vilag`
- Kultúra: `https://mti.hu/kultura`
- Sport: `https://mti.hu/sport`

A kimenet:

```text
public/import/pest-megye-news.json
```

A JSON tartalmazza a kép URL-jét is az `image` mezőben.

## Telepítés helyben

```bash
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

Windows PowerShellben inkább:

```powershell
Copy-Item .env.example .env
```

Majd töltsd ki a `.env` fájlt.

## Futtatás

```bash
python mti_rovat_scraper.py
```

## JSON struktúra

```json
[
  {
    "title": "",
    "category": "hirek",
    "city": "",
    "excerpt": "",
    "body": "",
    "author": "MTI",
    "url": "",
    "date": "YYYY-MM-DD",
    "image": "",
    "titleEn": "",
    "excerptEn": "",
    "bodyEn": "",
    "videoUrl": "",
    "audioUrl": ""
  }
]
```

## Pest megyei városok kezelése

Ha a cikk szövegében vagy címében szerepel Pest vármegye, Pest megye, vagy valamelyik ismert Pest megyei település neve, a script kitölti a `city` mezőt.  
Ha csak általános Pest megyei kapcsolódás van, de nincs konkrét város, akkor a `city` üres marad.

## GitHub Actions

A `.github/workflows/mti-scrape.yml` fájl kézzel indítható workflow-t tartalmaz.  
A repóban állítsd be ezeket:

Repository → Settings → Secrets and variables → Actions → New repository secret

- `MTI_USERNAME`
- `MTI_PASSWORD`

Ezután az Actions fülön kézzel indítható a scraper.
