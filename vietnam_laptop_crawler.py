#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vietnam Laptop Crawler v4.1 - GitHub Actions edition.

Supported sites (12): gearvn, xgear, tinhocngoisao, hangchinhhieu,
laptopnew, memoryzone, cellphones, hoanghamobile, laptopworld,
laptop88, anphatpc, hacom.

Output columns: site, name, url.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import html
import json
import os
import random
import re
import time
import unicodedata
from collections.abc import Iterable, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DELAY = 1.0
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

LAPTOP_TERMS = (
    "laptop", "macbook", "notebook", "chromebook", "vivobook", "zenbook",
    "expertbook", "ideapad", "thinkpad", "thinkbook", "legion", " loq",
    "aspire", "nitro", "swift", "predator", "pavilion", "victus",
    "elitebook", "probook", "omnibook", "inspiron", "latitude", "vostro",
    "xps", "alienware", "katana", "cyborg", "stealth", "modern",
    "prestige", "aorus", "lg gram", "surface", "proart", "matebook",
    "zephyrus", "rog flow", "tuf gaming", "omen", "surface pro",
)
EXCLUDE_TERMS = (
    "balo", "tui laptop", "de laptop", "gia do laptop", "sac laptop",
    "adapter", "pin laptop", "ram laptop", "ban phim", "chuot", "tai nghe",
    "man hinh", "bao hanh", "phu kien", "linh kien", "ve sinh laptop",
)
CARD_SELECTORS = (
    ".p-item", ".product-item", ".product-card", ".product-info-container",
    ".item-product", ".product-loop", ".proloop", ".product", "li.product",
    "article.product", "[data-product-id]", "[data-product]",
)
NAME_SELECTORS = (
    ".hover_name", ".p-name", ".product-name", ".product__name",
    ".product-item-name", ".proloop-title", ".product-title",
    "h2", "h3", "h4",
)


def log(label: str, message: str) -> None:
    print(f"[{label}] {message}", flush=True)


def clean(value) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def fold(value) -> str:
    value = unicodedata.normalize("NFKD", clean(value).lower())
    return "".join(char for char in value if not unicodedata.combining(char))


def is_laptop(value) -> bool:
    text = fold(value)
    return (
        len(text) > 4
        and any(term in text for term in LAPTOP_TERMS)
        and not any(term in text for term in EXCLUDE_TERMS)
    )


def canonical_url(url: str, base: str = "") -> str:
    absolute = urljoin(base, clean(url))
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def deduplicate(rows: Iterable[dict]) -> list[dict]:
    output, seen = [], set()
    for row in rows:
        url = canonical_url(row.get("url", ""))
        name = clean(row.get("name", ""))
        if url and len(name) > 4 and url not in seen:
            seen.add(url)
            output.append({"name": name, "url": url})
    return output


def merge_rows(*groups: Sequence[dict]) -> list[dict]:
    return deduplicate(row for group in groups for row in group)


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch(session, url: str, json_mode: bool = False, timeout: int = 40):
    try:
        response = session.get(
            url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.7",
                "Accept": (
                    "application/json,*/*" if json_mode
                    else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "Cache-Control": "no-cache",
            },
            timeout=timeout,
            allow_redirects=True,
        )
        print(f" GET {response.status_code} {len(response.content)} {url}", flush=True)
        return response if response.status_code == 200 else None
    except requests.RequestException as exc:
        print(f" GET ERROR {url} {exc}", flush=True)
        return None


def name_from_card(card, anchor) -> str:
    if hasattr(card, "select_one"):
        for selector in NAME_SELECTORS:
            node = card.select_one(selector)
            if node:
                name = clean(node.get("title") or node.get_text(" "))
                if name:
                    return name
    return clean(anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" "))


def extract_products(
    document: str,
    base: str,
    pattern: str | None = None,
    loose: bool = False,
) -> list[dict]:
    soup = BeautifulSoup(document, "lxml")
    output, cards = [], []
    for selector in CARD_SELECTORS:
        cards.extend(soup.select(selector))
    if not cards:
        cards = soup.select("a[href]")

    for card in cards:
        anchors = [card] if getattr(card, "name", None) == "a" else card.select("a[href]")
        for anchor in anchors:
            url = canonical_url(anchor.get("href", ""), base)
            if not url or (pattern and not re.search(pattern, url, re.I)):
                continue
            name = name_from_card(card, anchor)
            if is_laptop(name):
                output.append({"name": name, "url": url})
                break

    if loose:
        for anchor in soup.select("a[href]"):
            url = canonical_url(anchor.get("href", ""), base)
            name = clean(anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" "))
            if url and (not pattern or re.search(pattern, url, re.I)) and is_laptop(name):
                output.append({"name": name, "url": url})

    return deduplicate(output)


def shopify_collection(base: str, collections: Sequence[str], label: str) -> list[dict]:
    session = build_session()
    output, seen = [], set()
    for collection in collections:
        for page_number in range(1, 301):
            url = f"{base}/collections/{collection}/products.json?limit=250&page={page_number}"
            response = fetch(session, url, json_mode=True)
            if not response:
                break
            try:
                products = (response.json() or {}).get("products") or []
            except ValueError:
                products = []
            if not products:
                break

            new_count = 0
            for product in products:
                name = clean(product.get("title") or product.get("name"))
                handle = clean(product.get("handle"))
                product_url = canonical_url(
                    product.get("url") or (f"/products/{handle}" if handle else ""),
                    base,
                )
                key = str(product.get("id") or product_url)
                if name and product_url and key not in seen and is_laptop(name):
                    seen.add(key)
                    output.append({"name": name, "url": product_url})
                    new_count += 1

            log(
                label,
                f"JSON {collection} page={page_number} received={len(products)} "
                f"new={new_count} total={len(output)}",
            )
            if new_count == 0:
                break
            time.sleep(DELAY * 0.25)
    return deduplicate(output)


def query_paged(
    label: str,
    base: str,
    starts: Sequence[str],
    pattern: str,
    parameter: str = "page",
    max_pages: int = 100,
    loose: bool = True,
) -> list[dict]:
    session = build_session()
    output, seen = [], set()

    for start in starts:
        repeated = 0
        for page_number in range(1, max_pages + 1):
            page_url = start if page_number == 1 else (
                f"{start}{'&' if '?' in start else '?'}{parameter}={page_number}"
            )
            response = fetch(session, page_url)
            if not response:
                break
            rows = extract_products(response.text, base, pattern, loose)
            fresh = [row for row in rows if row["url"] not in seen]
            for row in fresh:
                seen.add(row["url"])
                output.append(row)
            log(label, f"page={page_number} found={len(rows)} new={len(fresh)} total={len(output)}")
            if not rows:
                break
            repeated = repeated + 1 if not fresh else 0
            if repeated >= 2:
                break
            time.sleep(DELAY)
    return deduplicate(output)


def slug_name(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.(?:html?|aspx?)$", "", slug, flags=re.I)
    return clean(slug.replace("-", " ").replace("_", " "))


def sitemap_products(
    label: str,
    base: str,
    include: Sequence[str],
    exclude: Sequence[str] = (),
    max_sitemaps: int = 150,
) -> list[dict]:
    session = build_session()
    queue = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-product.xml",
        f"{base}/sitemap_product.xml",
    ]
    visited, urls = set(), set()

    while queue and len(visited) < max_sitemaps:
        sitemap_url = queue.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        response = fetch(session, sitemap_url, timeout=50)
        if not response:
            continue
        body = response.content
        if sitemap_url.lower().endswith(".gz"):
            try:
                body = gzip.decompress(body)
            except OSError:
                continue
        soup = BeautifulSoup(body, "xml")
        for node in soup.find_all("loc"):
            url = canonical_url(node.get_text(" "), base)
            if not url:
                continue
            path = urlsplit(url).path.lower()
            if "sitemap" in path and path.endswith((".xml", ".xml.gz")):
                if url not in visited and url not in queue:
                    queue.append(url)
                continue
            if any(re.search(rule, url, re.I) for rule in include) and not any(
                re.search(rule, url, re.I) for rule in exclude
            ):
                urls.add(url)
        log(label, f"sitemap={len(visited)} products={len(urls)}")

    return deduplicate({"name": slug_name(url), "url": url} for url in sorted(urls))


async def rendered(
    label: str,
    base: str,
    starts: Sequence[str],
    pattern: str,
    max_rounds: int = 120,
) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    output, seen = [], set()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="vi-VN",
            viewport={"width": 1440, "height": 1000},
        )
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = await context.new_page()

        for start in starts:
            try:
                await page.goto(start, wait_until="domcontentloaded", timeout=50000)
                await page.wait_for_timeout(3000)
            except Exception as exc:
                log(label, f"NAV {start} {exc}")
                continue

            stale = 0
            for round_number in range(max_rounds):
                await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                await page.wait_for_timeout(1300)
                rows = extract_products(await page.content(), base, pattern, True)
                fresh = [row for row in rows if row["url"] not in seen]
                for row in fresh:
                    seen.add(row["url"])
                    output.append(row)
                log(label, f"round={round_number} visible={len(rows)} new={len(fresh)} total={len(output)}")
                stale = stale + 1 if not fresh else 0

                button = None
                for selector in (
                    "a.btn-show-more", "button.btn-show-more",
                    ".button__show-more-product", "[class*='show-more']",
                    "text=/Xem thêm|Tải thêm|Hiển thị thêm/i",
                ):
                    try:
                        candidate = page.locator(selector).first
                        if await candidate.is_visible(timeout=400):
                            button = candidate
                            break
                    except Exception:
                        pass
                if not button or stale >= 2:
                    break
                try:
                    await button.scroll_into_view_if_needed()
                    await button.click(timeout=5000)
                    await page.wait_for_timeout(2500)
                except Exception:
                    break
        await browser.close()
    return deduplicate(output)


# GEARVN ----------------------------------------------------------------------

def gearvn_sitemap_laptops() -> list[dict]:
    rows = sitemap_products(
        "GEARVN",
        "https://gearvn.com",
        include=[r"gearvn\.com/products/[a-z0-9-]+$"],
    )
    return [row for row in rows if is_laptop(row["name"])]


def crawl_gearvn(no_playwright: bool = False) -> list[dict]:
    base = "https://gearvn.com"
    collections = [
        "laptop", "laptop-gaming", "laptop-van-phong", "laptop-do-hoa",
        "laptop-gaming-ban-chay", "laptop-asus", "laptop-acer", "laptop-dell",
        "laptop-hp", "laptop-lenovo", "laptop-msi", "laptop-gigabyte",
        "macbook",
    ]
    starts = [f"{base}/collections/{collection}" for collection in collections]
    pattern = r"gearvn\.com/products/[a-z0-9-]+$"

    # GearVN commonly disables products.json, so HTML pagination is the primary source.
    page_rows = query_paged(
        "GEARVN", base, starts, pattern,
        parameter="page", max_pages=80, loose=True,
    )
    sitemap_rows = gearvn_sitemap_laptops()
    rendered_rows = []
    if not no_playwright:
        rendered_rows = asyncio.run(rendered("GEARVN", base, starts, pattern, 150))

    return merge_rows(page_rows, sitemap_rows, rendered_rows)


# HACOM -----------------------------------------------------------------------

HACOM_CATEGORY_PATHS = {
    "/laptop", "/laptop-tablet-mobile", "/laptop-acer", "/laptop-asus",
    "/laptop-dell", "/laptop-hp", "/laptop-lenovo", "/laptop-msi",
    "/laptop-gigabyte", "/laptop-lg", "/laptop-apple",
    "/laptop-microsoft-surface", "/laptop-gaming", "/laptop-do-hoa",
    "/laptop-van-phong", "/laptop-mong-nhe",
}


def hacom_is_product_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.netloc.lower() not in {"hacom.vn", "www.hacom.vn"}:
        return False
    path = parts.path.rstrip("/").lower()
    if not path or path in HACOM_CATEGORY_PATHS or re.fullmatch(r"/laptop/\d+", path):
        return False
    if path.startswith(("/tin-tuc", "/khuyen-mai", "/gio-hang", "/cart", "/search", "/thuong-hieu")):
        return False
    if not path.startswith("/laptop-"):
        return False
    slug = path.rsplit("/", 1)[-1]
    if slug in {item.lstrip("/") for item in HACOM_CATEGORY_PATHS}:
        return False
    return bool(re.search(r"\d", slug)) or len(slug) >= 28


def extract_hacom_products(document: str) -> list[dict]:
    soup = BeautifulSoup(document, "lxml")
    output = []
    cards = []
    for selector in (
        ".p-item", ".p-container", ".product-item", ".product-card",
        ".product", "[data-id]", "[data-product-id]",
        "li[class*='product']", "div[class*='product-item']",
    ):
        cards.extend(soup.select(selector))

    for card in cards:
        for anchor in card.select("a[href]"):
            url = canonical_url(anchor.get("href", ""), "https://hacom.vn")
            if not hacom_is_product_url(url):
                continue
            name = name_from_card(card, anchor)
            if is_laptop(name):
                output.append({"name": name, "url": url})
                break

    for anchor in soup.select("a[href]"):
        url = canonical_url(anchor.get("href", ""), "https://hacom.vn")
        if not hacom_is_product_url(url):
            continue
        name = clean(anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" "))
        if not is_laptop(name):
            parent = anchor.find_parent(["article", "li", "div"])
            if parent:
                name = name_from_card(parent, anchor)
        if is_laptop(name):
            output.append({"name": name, "url": url})
    return deduplicate(output)


def crawl_hacom(_no_playwright: bool = False) -> list[dict]:
    session = build_session()
    output, seen = [], set()
    empty_streak = 0

    for page_number in range(1, 121):
        urls = ["https://hacom.vn/laptop"] if page_number == 1 else [
            f"https://hacom.vn/laptop/{page_number}/",
            f"https://hacom.vn/laptop/{page_number}/?sort=new",
            f"https://hacom.vn/laptop?page={page_number}",
        ]
        best_rows, best_fresh, best_url = [], [], ""
        for page_url in urls:
            response = fetch(session, page_url, timeout=50)
            if not response:
                continue
            rows = extract_hacom_products(response.text)
            fresh = [row for row in rows if row["url"] not in seen]
            if len(fresh) > len(best_fresh) or (len(fresh) == len(best_fresh) and len(rows) > len(best_rows)):
                best_rows, best_fresh, best_url = rows, fresh, page_url

        for row in best_fresh:
            seen.add(row["url"])
            output.append(row)
        log("HACOM", f"page={page_number} source={best_url or 'none'} found={len(best_rows)} new={len(best_fresh)} total={len(output)}")
        empty_streak = 0 if best_fresh else empty_streak + 1
        if (not best_rows and empty_streak >= 2) or empty_streak >= 3:
            break
        time.sleep(DELAY)

    for brand in ("acer", "asus", "dell", "hp", "lenovo", "msi", "gigabyte", "lg", "apple", "microsoft-surface"):
        page_url = f"https://hacom.vn/laptop-{brand}"
        response = fetch(session, page_url, timeout=50)
        if not response:
            continue
        rows = extract_hacom_products(response.text)
        fresh = [row for row in rows if row["url"] not in seen]
        for row in fresh:
            seen.add(row["url"])
            output.append(row)
        log("HACOM-BRAND", f"source={page_url} found={len(rows)} new={len(fresh)} total={len(output)}")
        time.sleep(DELAY)
    return deduplicate(output)


# Remaining sites -------------------------------------------------------------

def crawl_laptopnew(_no_playwright=False):
    base = "https://laptopnew.vn"
    return shopify_collection(base, ["laptop-gaming", "laptop-van-phong", "laptop"], "LAPTOPNEW") or query_paged(
        "LAPTOPNEW", base,
        [f"{base}/collections/laptop-gaming", f"{base}/collections/laptop-van-phong"],
        r"laptopnew\.vn/(?:products/)?[a-z0-9-]+$",
    )


def crawl_cellphones(no_playwright=False):
    base = "https://cellphones.com.vn"
    starts = [f"{base}/laptop.html"] + [
        f"{base}/laptop/{slug}.html" for slug in (
            "van-phong", "gaming", "do-hoa", "sinh-vien", "mong-nhe",
            "asus", "acer", "dell", "hp", "lenovo", "msi", "gigabyte", "lg",
        )
    ]
    pattern = r"cellphones\.com\.vn/(?!laptop(?:/|\.html$))[a-z0-9-/]+\.html$"
    render_rows = [] if no_playwright else asyncio.run(rendered("CELLPHONES", base, starts, pattern))
    map_rows = sitemap_products(
        "CELLPHONES", base,
        [r"cellphones\.com\.vn/(?:laptop|macbook)/[a-z0-9-]+\.html$"],
        [r"/laptop/(?:asus|acer|dell|hp|lenovo|msi|gigabyte|lg|gaming|van-phong|do-hoa|sinh-vien|mong-nhe)\.html$"],
    )
    return merge_rows(render_rows, map_rows) or query_paged("CELLPHONES", base, starts, pattern)


def crawl_laptop88(no_playwright=False):
    base = "https://laptop88.vn"
    starts = [f"{base}/may-tinh-xach-tay.html"]
    pattern = r"laptop88\.vn/(?!may-tinh-xach-tay\.html$|tin-tuc/|khuyen-mai/|thuong-hieu/|nhu-cau/|phu-kien/)[a-z0-9][a-z0-9-/]+(?:\.html)?$"
    render_rows = [] if no_playwright else asyncio.run(rendered("LAPTOP88", base, starts, pattern))
    map_rows = sitemap_products(
        "LAPTOP88", base,
        [r"laptop88\.vn/(?:new-100-|laptop-)[a-z0-9-]+(?:\.html)?$"],
        [r"/laptop-(?:asus|acer|dell|hp|lenovo|msi|gaming|van-phong)(?:\.html)?$"],
    )
    return merge_rows(render_rows, map_rows) or query_paged("LAPTOP88", base, starts, pattern)


def crawl_anphatpc(_no_playwright=False):
    base = "https://www.anphatpc.com.vn"
    starts = [f"{base}/may-tinh-xach-tay-laptop.html", f"{base}/gaming-laptop.html", f"{base}/laptop-do-hoa.html"]
    pattern = r"anphatpc\.com\.vn/(?!may-tinh-xach-tay-laptop\.html$|gaming-laptop\.html$|laptop-do-hoa\.html$)[a-z0-9-/]+\.html$"
    return query_paged("ANPHATPC", base, starts, pattern, max_pages=100)


CRAWLERS = {
    "gearvn": crawl_gearvn,
    "xgear": lambda _n: shopify_collection("https://xgear.net", ["laptop"], "XGEAR"),
    "tinhocngoisao": lambda _n: shopify_collection("https://tinhocngoisao.com", ["laptop"], "TINHOCNGOISAO"),
    "hangchinhhieu": lambda _n: shopify_collection("https://hangchinhhieu.vn", ["laptop", "laptop-gaming-do-hoa-studio"], "HANGCHINHHIEU"),
    "laptopnew": crawl_laptopnew,
    "memoryzone": lambda _n: shopify_collection("https://memoryzone.com.vn", ["laptop"], "MEMORYZONE") or query_paged(
        "MEMORYZONE", "https://memoryzone.com.vn", ["https://memoryzone.com.vn/laptop"], r"memoryzone\.com\.vn/(?:products/)?[a-z0-9-]+$"
    ),
    "cellphones": crawl_cellphones,
    "hoanghamobile": lambda _n: query_paged(
        "HOANGHAMOBILE", "https://hoanghamobile.com", ["https://hoanghamobile.com/laptop"],
        r"hoanghamobile\.com/laptop/(?!phan-loai-san-pham/)[a-z0-9-]+$", parameter="p"
    ),
    "laptopworld": lambda _n: query_paged(
        "LAPTOPWORLD", "https://laptopworld.vn",
        ["https://laptopworld.vn/laptop-van-phong.html", "https://laptopworld.vn/laptop-games-do-hoa.html"],
        r"laptopworld\.vn/(?!laptop-(?:van-phong|games-do-hoa)\.html$)(?:[a-z0-9-]+/)*[a-z0-9-]+(?:\.html)?$"
    ),
    "laptop88": crawl_laptop88,
    "anphatpc": crawl_anphatpc,
    "hacom": crawl_hacom,
}


def save_csv(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["site", "name", "url"])
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows: list[dict], csv_path: str) -> None:
    with open(os.path.splitext(csv_path)[0] + ".json", "w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)


def main() -> int:
    global DELAY
    parser = argparse.ArgumentParser(description="Vietnam Laptop Crawler v4.1")
    parser.add_argument("--sites", nargs="+", choices=sorted(CRAWLERS))
    parser.add_argument("--output", default="vietnam_laptops.csv")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--no-playwright", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    DELAY = max(0.1, args.delay)

    all_rows = []
    for site in args.sites or list(CRAWLERS):
        try:
            rows = deduplicate(CRAWLERS[site](args.no_playwright))
        except KeyboardInterrupt:
            log(site, "INTERRUPTED")
            break
        except Exception as exc:
            log(site, f"FATAL {exc}")
            rows = []
        all_rows.extend({"site": site, **row} for row in rows)
        save_csv(all_rows, args.output)
        if args.json:
            save_json(all_rows, args.output)
        log(site, f"SAVED {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
