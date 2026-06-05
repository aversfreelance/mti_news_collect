import os
import re
import json
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


load_dotenv()

MTI_USERNAME = os.getenv("MTI_USERNAME", "")
MTI_PASSWORD = os.getenv("MTI_PASSWORD", "")
MTI_LOGIN_URL = os.getenv("MTI_LOGIN_URL", "https://mti.hu")
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "public/import/pest-megye-news.json")
ARTICLES_PER_SECTION = int(os.getenv("ARTICLES_PER_SECTION", "5"))
HEADLESS = os.getenv("HEADLESS", "1") != "0"

SECTIONS = {
    "kozelet": {
        "name": "Közélet",
        "url": "https://mti.hu/kozelet",
        "category": "hirek"
    },
    "gazdasag": {
        "name": "Gazdaság",
        "url": "https://mti.hu/gazdasag",
        "category": "hirek"
    },
    "vilag": {
        "name": "Külföld",
        "url": "https://mti.hu/vilag",
        "category": "hirek"
    },
    "kultura": {
        "name": "Kultúra",
        "url": "https://mti.hu/kultura",
        "category": "turizmus"
    },
    "sport": {
        "name": "Sport",
        "url": "https://mti.hu/sport",
        "category": "sport"
    },
}

PEST_COUNTY_CITIES = [
    "Abony", "Albertirsa", "Aszód", "Biatorbágy", "Budaörs", "Budakalász",
    "Budakeszi", "Cegléd", "Csömör", "Dabas", "Diósd", "Dunaharaszti",
    "Dunakeszi", "Dunavarsány", "Érd", "Fót", "Göd", "Gödöllő", "Gyál",
    "Gyömrő", "Halásztelek", "Isaszeg", "Kistarcsa", "Maglód", "Monor",
    "Nagykáta", "Nagykőrös", "Nagymaros", "Pécel", "Pilis", "Pilisvörösvár",
    "Pomáz", "Ráckeve", "Solymár", "Sülysáp", "Szentendre", "Szigethalom",
    "Szigetszentmiklós", "Százhalombatta", "Tápiószele", "Törökbálint",
    "Tura", "Üllő", "Vác", "Vecsés", "Veresegyház", "Visegrád", "Zsámbék",
    "Nagykovácsi", "Tahitótfalu", "Dunabogdány", "Leányfalu", "Őrbottyán",
    "Kerepes", "Mogyoród", "Bugyi", "Ócsa", "Inárcs", "Kakucs",
    "Újhartyán", "Hernád", "Újlengyel", "Tápiószecső", "Tápiószentmárton",
    "Kóka", "Tóalmás", "Kartal", "Verőce", "Szob"
]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def light_rewrite(text: str) -> str:
    """
    Óvatos átírás. Nem talál ki új tényeket.
    Csak néhány hírügynökségi fordulatot cserél, és tisztítja a szöveget.
    """
    text = clean_text(text)
    replacements = [
        (r"\bközölte\b", "ismertette"),
        (r"\belmondta\b", "beszámolt arról, hogy"),
        (r"\bhozzátette\b", "kiemelte"),
        (r"\bjelezte\b", "közölte"),
        (r"\ba kormány döntött arról\b", "a kabinet határozott arról"),
        (r"\ba kormány szerint\b", "a kabinet álláspontja szerint"),
        (r"\bjelentették be\b", "hangzott el"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return clean_text(text.replace("; ", ". "))


def excerpt_from_body(body: str, max_len: int = 260) -> str:
    body = clean_text(body)
    if len(body) <= max_len:
        return body
    return body[:max_len].rsplit(" ", 1)[0] + "…"


def normalize_date(text: str) -> str:
    text = clean_text(text)
    if not text:
        return datetime.today().strftime("%Y-%m-%d")
    try:
        return date_parser.parse(text, fuzzy=True).strftime("%Y-%m-%d")
    except Exception:
        return datetime.today().strftime("%Y-%m-%d")


def detect_pest_city(title: str, body: str) -> str:
    haystack = f" {title} {body} ".lower()

    for city in PEST_COUNTY_CITIES:
        pattern = r"(?<![a-záéíóöőúüű])" + re.escape(city.lower()) + r"(?![a-záéíóöőúüű])"
        if re.search(pattern, haystack):
            return city

    if "pest vármegye" in haystack or "pest megye" in haystack:
        return ""

    return ""


def is_probably_article_link(href: str, text: str) -> bool:
    if not href:
        return False
    href_l = href.lower()
    text = clean_text(text)
    if len(text) < 15:
        return False
    bad = ["javascript:", "#", "mailto:", "/login", "/belepes", "/regisztracio"]
    if any(href_l.startswith(x) or x in href_l for x in bad):
        return False
    return any(x in href_l for x in ["/hir", "hir=", "/cikk", "news", "article", "/mti"])


def login_if_needed(page):
    page.goto(MTI_LOGIN_URL, wait_until="domcontentloaded")

    # Ha nincs login mező, feltételezzük, hogy nem kell külön login ezen az URL-en,
    # vagy már sessionben vagyunk.
    username_selectors = [
        'input[name="username"]',
        'input[name="email"]',
        'input[type="email"]',
        'input[type="text"]',
        '#username',
        '#email',
        '#login',
    ]
    password_selectors = [
        'input[name="password"]',
        'input[type="password"]',
        '#password',
    ]

    user_sel = None
    pass_sel = None

    for sel in username_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=1200):
                user_sel = sel
                break
        except Exception:
            pass

    for sel in password_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=1200):
                pass_sel = sel
                break
        except Exception:
            pass

    if not user_sel or not pass_sel:
        print("Nem találtam belépési mezőt. Folytatom, hátha már be vagy jelentkezve.")
        return

    page.fill(user_sel, MTI_USERNAME)
    page.fill(pass_sel, MTI_PASSWORD)

    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Belépés")',
        'button:has-text("Bejelentkezés")',
        'button:has-text("Login")',
    ]

    for sel in submit_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=1000):
                page.locator(sel).first.click()
                break
        except Exception:
            continue
    else:
        page.keyboard.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass


def collect_section_links(page, section_url: str, limit: int) -> list[str]:
    page.goto(section_url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    soup = BeautifulSoup(page.content(), "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" "))
        href = a.get("href", "")
        if is_probably_article_link(href, text):
            full_url = urljoin(section_url, href)
            if urlparse(full_url).netloc.endswith("mti.hu"):
                links.append(full_url)

    seen = set()
    unique = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)

    return unique[:limit]


def extract_best_image(soup: BeautifulSoup, base_url: str) -> str:
    # OpenGraph kép a legjobb, ha van.
    for selector in [
        ('meta', {'property': 'og:image'}),
        ('meta', {'name': 'twitter:image'}),
    ]:
        tag = soup.find(*selector)
        if tag and tag.get("content"):
            return urljoin(base_url, tag["content"])

    article = soup.find("article") or soup.find("main") or soup.body
    if article:
        for img in article.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original")
            if src:
                return urljoin(base_url, src)

    img = soup.find("img")
    if img:
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if src:
            return urljoin(base_url, src)

    return ""


def parse_article(page, url: str, section_info: dict) -> dict | None:
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    soup = BeautifulSoup(page.content(), "html.parser")

    title = ""
    for selector in ["h1", "h2", ".title", ".article-title"]:
        el = soup.select_one(selector)
        if el and clean_text(el.get_text(" ")):
            title = clean_text(el.get_text(" "))
            break

    date_text = ""
    time_el = soup.find("time")
    if time_el:
        date_text = time_el.get("datetime") or time_el.get_text(" ")

    if not date_text:
        # Egyszerű dátumkeresés a teljes oldalban.
        all_text = clean_text(soup.get_text(" "))
        m = re.search(r"20\d{2}\.\s*\w+\s*\d{1,2}\.|20\d{2}-\d{2}-\d{2}", all_text)
        if m:
            date_text = m.group(0)

    article = soup.find("article") or soup.find("main") or soup.body
    paragraphs = []
    if article:
        for p in article.find_all(["p"]):
            txt = clean_text(p.get_text(" "))
            if len(txt) > 35 and txt not in paragraphs:
                paragraphs.append(txt)

    # Ha nincsenek p tagek, próbálkozzunk hosszabb div-ekkel.
    if len(paragraphs) < 2 and article:
        for div in article.find_all("div"):
            txt = clean_text(div.get_text(" "))
            if 80 < len(txt) < 3000 and txt not in paragraphs:
                paragraphs.append(txt)

    body = "\n\n".join(paragraphs)

    if not title or len(body) < 80:
        print(f"Kihagyva, nem sikerült értelmes cikket olvasni: {url}")
        return None

    title_rw = light_rewrite(title)
    body_rw = light_rewrite(body)
    city = detect_pest_city(title_rw, body_rw)

    return {
        "title": title_rw,
        "category": section_info.get("category", "hirek"),
        "city": city,
        "excerpt": excerpt_from_body(body_rw),
        "body": body_rw,
        "author": "MTI",
        "url": url,
        "date": normalize_date(date_text),
        "image": extract_best_image(soup, url),
        "titleEn": "",
        "excerptEn": "",
        "bodyEn": "",
        "videoUrl": "",
        "audioUrl": ""
    }


def main():
    if not MTI_USERNAME or not MTI_PASSWORD:
        print("Figyelem: nincs MTI_USERNAME vagy MTI_PASSWORD. Ha az oldal loginos, így nem fog sikerülni.")

    out_path = Path(OUTPUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_items = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(locale="hu-HU")
        page = context.new_page()

        login_if_needed(page)

        for key, section in SECTIONS.items():
            print(f"\nRovat: {section['name']} — {section['url']}")
            links = collect_section_links(page, section["url"], ARTICLES_PER_SECTION)
            print(f"Talált link: {len(links)}")

            count = 0
            for link in links:
                if link in seen_urls:
                    continue
                try:
                    item = parse_article(page, link, section)
                    if item:
                        all_items.append(item)
                        seen_urls.add(link)
                        count += 1
                        print(f"OK: {item['title'][:90]}")
                except Exception as exc:
                    print(f"Hiba: {link} — {exc}")
                time.sleep(0.7)

            print(f"Mentett cikk ebből a rovatból: {count}")

        browser.close()

    out_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nKész: {out_path} — összesen {len(all_items)} cikk")


if __name__ == "__main__":
    main()
