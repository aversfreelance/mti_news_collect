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
MTI_LOGIN_URL = os.getenv("MTI_LOGIN_URL", "https://mti.hu/regisztralt-latogatok-bejelentkezes/realms/visitor/protocol/openid-connect/auth?response_type=code&client_id=frontend&redirect_uri=https%3A%2F%2Fmti.hu%2Fauth%2Fcallback%2Fvisitor&ui_locales=hu&code_challenge=vrdKEhV-vzssn4_f_gMQh99L1KrUFu8B2h3L0k1CjLo&code_challenge_method=S256&scope=openid+profile+email")
OUTPUT_FILE = os.getenv("OUTPUT_FILE", "public/import/pest-megye-news.json")
ARTICLES_PER_SECTION = int(os.getenv("ARTICLES_PER_SECTION", "5"))
HEADLESS = os.getenv("HEADLESS", "1") != "0"

SECTIONS = {
    "kozelet": {"name": "Közélet", "url": "https://mti.hu/kozelet", "category": "hirek"},
    "gazdasag": {"name": "Gazdaság", "url": "https://mti.hu/gazdasag", "category": "hirek"},
    "vilag": {"name": "Külföld", "url": "https://mti.hu/vilag", "category": "hirek"},
    "kultura": {"name": "Kultúra", "url": "https://mti.hu/kultura", "category": "turizmus"},
    "sport": {"name": "Sport", "url": "https://mti.hu/sport", "category": "sport"},
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


def make_excerpt(body: str, max_len: int = 260) -> str:
    body = clean_text(body)
    if len(body) <= max_len:
        return body
    return body[:max_len].rsplit(" ", 1)[0] + "…"


def light_rewrite(text: str) -> str:
    text = clean_text(text)
    replacements = [
        (r"\bközölte\b", "ismertette"),
        (r"\belmondta\b", "beszámolt arról, hogy"),
        (r"\bhozzátette\b", "kiemelte"),
        (r"\bjelezte\b", "közölte"),
        (r"\bjelentették be\b", "hangzott el"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return clean_text(text)


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
    return ""


def looks_like_login_page(html: str, url: str = "") -> bool:
    text = clean_text(BeautifulSoup(html, "html.parser").get_text(" ")).lower()
    url_l = (url or "").lower()
    markers = [
        "bejelentkezés szerződéssel rendelkezőként",
        "e-mail cím",
        "jelszó",
        "elfelejtette jelszavát",
        "belépés",
    ]
    if "openid-connect" in url_l or "regisztralt-latogatok-bejelentkezes" in url_l:
        return True
    return sum(1 for marker in markers if marker in text) >= 3


def accept_cookies_if_present(page):
    for selector in [
        'button:has-text("Minden süti elfogadása")',
        'button:has-text("Elfogadom")',
        'button:has-text("Rendben")',
        'text=Minden süti elfogadása',
    ]:
        try:
            if page.locator(selector).first.is_visible(timeout=1500):
                page.locator(selector).first.click()
                time.sleep(0.5)
                return
        except Exception:
            pass


def login(page):
    if not MTI_USERNAME or not MTI_PASSWORD:
        raise RuntimeError("Hiányzik az MTI_USERNAME vagy MTI_PASSWORD GitHub Secret.")

    page.goto(MTI_LOGIN_URL, wait_until="domcontentloaded")
    accept_cookies_if_present(page)

    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    print("Login oldal URL:", page.url)

    email_selectors = [
        'input[type="email"]',
        'input[name="email"]',
        'input[id*="email" i]',
        'input[name="username"]',
        'input[id*="username" i]',
        'input[type="text"]',
    ]
    password_selectors = [
        'input[type="password"]',
        'input[name="password"]',
        'input[id*="password" i]',
    ]

    email_field = None
    password_field = None

    for selector in email_selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=2000):
                email_field = selector
                break
        except Exception:
            pass

    for selector in password_selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=2000):
                password_field = selector
                break
        except Exception:
            pass

    if not email_field or not password_field:
        Path("debug-login.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="debug-login.png", full_page=True)
        raise RuntimeError("Nem találom az e-mail vagy jelszó mezőt. debug-login.html és debug-login.png elkészült.")

    print("E-mail mező:", email_field)
    print("Jelszó mező:", password_field)

    page.fill(email_field, MTI_USERNAME)
    page.fill(password_field, MTI_PASSWORD)

    clicked = False
    for selector in ['button:has-text("Belépés")', 'input[type="submit"]', 'button[type="submit"]']:
        try:
            if page.locator(selector).first.is_visible(timeout=2000):
                page.locator(selector).first.click()
                clicked = True
                break
        except Exception:
            pass

    if not clicked:
        page.keyboard.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass

    time.sleep(2)
    print("Login utáni URL:", page.url)

    if looks_like_login_page(page.content(), page.url):
        Path("debug-after-login.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="debug-after-login.png", full_page=True)
        raise RuntimeError("MTI login sikertelen: továbbra is a bejelentkezési oldalon vagyunk.")


def is_article_link(href: str, text: str) -> bool:
    if not href:
        return False
    href_l = href.lower()
    text = clean_text(text)
    
    if len(text) < 15:
        return False
bad_extensions = [
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip"
]
if any(href.lower().endswith(ext) for ext in bad_extensions):
    return False

    bad_parts = [
        "javascript:", "mailto:", "#", "/login", "/auth/", "/regisztralt-latogatok",
        "suti", "adatvedelem", "impresszum"
    ]
    if any(part in href_l for part in bad_parts):
        return False

    return urlparse(urljoin("https://mti.hu", href)).netloc.endswith("mti.hu")


def collect_section_links(page, section_url: str, limit: int) -> list[str]:
    page.goto(section_url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    if looks_like_login_page(page.content(), page.url):
        raise RuntimeError(f"A rovatoldal helyett login oldal jött be: {section_url}")

    soup = BeautifulSoup(page.content(), "html.parser")
    links = []

    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" "))
        href = a.get("href", "")
        if is_article_link(href, text):
            full_url = urljoin(section_url, href)
            if full_url not in links:
                links.append(full_url)

    return links[:limit]


def extract_image(soup: BeautifulSoup, base_url: str) -> str:
    for tag in [
        soup.find("meta", property="og:image"),
        soup.find("meta", attrs={"name": "twitter:image"}),
    ]:
        if tag and tag.get("content"):
            return urljoin(base_url, tag["content"])

    article = soup.find("article") or soup.find("main") or soup.body
    if article:
        for img in article.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original")
            if src:
                return urljoin(base_url, src)

    return ""


def parse_article(page, url: str, section: dict) -> dict | None:
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

    html = page.content()
    if looks_like_login_page(html, page.url):
        raise RuntimeError(f"Cikk helyett login oldal jött be: {url}")

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    for selector in ["h1", "h2", ".title", ".article-title"]:
        el = soup.select_one(selector)
        if el and clean_text(el.get_text(" ")):
            title = clean_text(el.get_text(" "))
            break

    article = soup.find("article") or soup.find("main") or soup.body
    paragraphs = []

    if article:
        for p in article.find_all("p"):
            txt = clean_text(p.get_text(" "))
            if len(txt) > 40 and txt not in paragraphs:
                paragraphs.append(txt)

    body = "\n\n".join(paragraphs)

    if not title or len(body) < 80:
        print(f"Kihagyva, nem sikerült cikket olvasni: {url}")
        return None

    date_text = ""
    time_tag = soup.find("time")
    if time_tag:
        date_text = time_tag.get("datetime") or time_tag.get_text(" ")

    title = light_rewrite(title)
    body = light_rewrite(body)

    return {
        "title": title,
        "category": section.get("category", "hirek"),
        "city": detect_pest_city(title, body),
        "excerpt": make_excerpt(body),
        "body": body,
        "author": "MTI",
        "url": url,
        "date": normalize_date(date_text),
        "image": extract_image(soup, url),
        "titleEn": "",
        "excerptEn": "",
        "bodyEn": "",
        "videoUrl": "",
        "audioUrl": ""
    }


def main():
    out_path = Path(OUTPUT_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_items = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(locale="hu-HU")
        page = context.new_page()

        login(page)

        for section_key, section in SECTIONS.items():
            print(f"Rovat: {section['name']} — {section['url']}")
            links = collect_section_links(page, section["url"], ARTICLES_PER_SECTION)
            print(f"Talált link: {len(links)}")

            saved = 0
            for link in links:
                if link in seen_urls:
                    continue

                item = parse_article(page, link, section)
                if item:
                    all_items.append(item)
                    seen_urls.add(link)
                    saved += 1
                    print("OK:", item["title"][:100])

                time.sleep(0.7)

            print(f"Mentett cikk ebből a rovatból: {saved}")

        browser.close()

    out_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Kész: {out_path} — összesen {len(all_items)} cikk")


if __name__ == "__main__":
    main()
