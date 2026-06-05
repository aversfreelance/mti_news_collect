import os
import re
import json
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


load_dotenv()

USERNAME = os.getenv("MTI_USERNAME", "")
PASSWORD = os.getenv("MTI_PASSWORD", "")
LOGIN_URL = os.getenv("MTI_LOGIN_URL", "https://www.mti.hu")
NEWS_URL = os.getenv("MTI_NEWS_URL", "https://www.mti.hu")
MAX_NEWS = int(os.getenv("MAX_NEWS", "15"))
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "public/import/pest-megye-news.json")
DEFAULT_CATEGORY = os.getenv("DEFAULT_CATEGORY", "hirek")
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "")

PEST_COUNTY_KEYWORDS = [
    "pest vármegye", "pest megye", "budakeszi", "cegléd", "dabas", "dunaharaszti",
    "dunakeszi", "érd", "fót", "göd", "gödöllő", "gyál", "monor", "nagykőrös",
    "szentendre", "szigetszentmiklós", "vác", "vecsés", "veresegyház"
]


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def light_rewrite(text: str) -> str:
    """
    Óvatos, automatikus átírás.
    Nem talál ki új tényeket, csak néhány hírügynökségi fordulatot cserél.
    Ha komolyabb átírás kell, ezt a részt érdemes LLM/API hívással kiváltani.
    """
    replacements = {
        "közölte": "ismertette",
        "elmondta": "beszámolt arról, hogy",
        "hozzátette": "kiemelte",
        "a kormány döntött arról": "a kabinet határozott arról",
        "a kormány szerint": "a kabinet álláspontja szerint",
        "jelentették be": "hangzott el",
    }

    rewritten = text
    for old, new in replacements.items():
        rewritten = re.sub(old, new, rewritten, flags=re.IGNORECASE)

    # Rövidítsük a túl hosszú mondatokat, ha lehet.
    rewritten = rewritten.replace("; ", ". ")
    return clean_text(rewritten)


def make_excerpt(body: str, max_len: int = 260) -> str:
    body = clean_text(body)
    if len(body) <= max_len:
        return body
    cut = body[:max_len].rsplit(" ", 1)[0]
    return cut + "…"


def detect_city_and_category(title: str, body: str) -> tuple[str, str]:
    haystack = f"{title} {body}".lower()

    for kw in PEST_COUNTY_KEYWORDS:
        if kw in haystack:
            # Ha konkrét településnév van, azt írjuk city-be.
            if kw not in ["pest vármegye", "pest megye"]:
                return DEFAULT_CATEGORY, kw.title()
            return DEFAULT_CATEGORY, ""

    return DEFAULT_CATEGORY, DEFAULT_CITY


def normalize_date(text: str) -> str:
    text = clean_text(text)
    if not text:
        return datetime.today().strftime("%Y-%m-%d")
    try:
        return date_parser.parse(text, fuzzy=True).strftime("%Y-%m-%d")
    except Exception:
        return datetime.today().strftime("%Y-%m-%d")


def login(page):
    page.goto(LOGIN_URL, wait_until="domcontentloaded")

    # Többféle gyakori login-mezőt próbálunk, mert az MTI HTML-je változhat.
    username_selectors = [
        'input[name="username"]',
        'input[name="email"]',
        'input[type="email"]',
        'input[type="text"]',
        '#username',
        '#email',
    ]
    password_selectors = [
        'input[name="password"]',
        'input[type="password"]',
        '#password',
    ]

    user_field = None
    pass_field = None

    for sel in username_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=1500):
                user_field = sel
                break
        except Exception:
            pass

    for sel in password_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=1500):
                pass_field = sel
                break
        except Exception:
            pass

    if not user_field or not pass_field:
        print("Nem találtam login mezőket. Lehet, hogy már be vagy jelentkezve, vagy más a login oldal.")
        return

    page.fill(user_field, USERNAME)
    page.fill(pass_field, PASSWORD)

    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Belépés")',
        'button:has-text("Login")',
        'button:has-text("Bejelentkezés")',
    ]

    clicked = False
    for sel in submit_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=1000):
                page.locator(sel).first.click()
                clicked = True
                break
        except Exception:
            pass

    if not clicked:
        page.keyboard.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass


def collect_news_links(page) -> list[str]:
    page.goto(NEWS_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    soup = BeautifulSoup(page.content(), "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" "))
        href = a["href"]

        # Általános szűrés hír/cikk jellegű linkekre.
        if len(text) < 20:
            continue
        if any(x in href.lower() for x in ["hir", "news", "cikk", "article"]):
            links.append(urljoin(NEWS_URL, href))

    # Duplikációk törlése, sorrend megtartása.
    seen = set()
    unique = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)

    return unique[:MAX_NEWS]


def parse_article(page, url: str) -> dict | None:
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    soup = BeautifulSoup(page.content(), "html.parser")

    title_el = soup.find(["h1", "h2"])
    title = clean_text(title_el.get_text(" ")) if title_el else ""

    # Dátum keresése
    date_text = ""
    time_el = soup.find("time")
    if time_el:
        date_text = time_el.get("datetime") or time_el.get_text(" ")

    # Fő szöveg keresése
    article = soup.find("article") or soup.find("main") or soup.body
    paragraphs = []
    if article:
        for p in article.find_all(["p", "div"]):
            txt = clean_text(p.get_text(" "))
            if len(txt) > 40 and txt not in paragraphs:
                paragraphs.append(txt)

    body = "\n\n".join(paragraphs)

    if not title or len(body) < 100:
        return None

    rewritten_title = light_rewrite(title)
    rewritten_body = light_rewrite(body)
    category, city = detect_city_and_category(rewritten_title, rewritten_body)

    image_url = ""
    img = soup.find("img")
    if img and img.get("src"):
        image_url = urljoin(url, img["src"])

    return {
        "title": rewritten_title,
        "category": category,
        "city": city,
        "excerpt": make_excerpt(rewritten_body),
        "body": rewritten_body,
        "author": "MTI",
        "url": url,
        "date": normalize_date(date_text),
        "image": image_url,
        "titleEn": "",
        "excerptEn": "",
        "bodyEn": "",
        "videoUrl": "",
        "audioUrl": ""
    }


def main():
    if not USERNAME or not PASSWORD:
        raise SystemExit("Hiányzik az MTI_USERNAME vagy MTI_PASSWORD a .env fájlból.")

    output_path = Path(OUTPUT_FILE)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="hu-HU")
        page = context.new_page()

        login(page)

        links = collect_news_links(page)
        print(f"Talált hírlinkek száma: {len(links)}")

        for link in links:
            try:
                item = parse_article(page, link)
                if item:
                    results.append(item)
                    print(f"OK: {item['title'][:80]}")
                if len(results) >= MAX_NEWS:
                    break
                time.sleep(1)
            except Exception as exc:
                print(f"Hiba ennél: {link} — {exc}")

        browser.close()

    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Kész: {output_path} ({len(results)} hír)")


if __name__ == "__main__":
    main()
