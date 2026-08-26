#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vietnam Laptop Crawler v3.6 - GitHub Actions edition.

Supported sites (12): gearvn, xgear, tinhocngoisao, hangchinhhieu,
laptopnew, memoryzone, cellphones, hoanghamobile, laptopworld,
laptop88, anphatpc, hacom.

The output contains: site, name, url.
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
from collections.abc import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DELAY = 1.0
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
LAPTOP_TERMS = (
    "laptop", "macbook", "notebook", "chromebook", "vivobook", "zenbook",
    "expertbook", "ideapad", "thinkpad", "thinkbook", "legion", " loq",
    "aspire", "nitro", "swift", "predator", "pavilion", "victus",
    "elitebook", "probook", "omnibook", "inspiron", "latitude", "vostro",
    "xps", "alienware", "katana", "cyborg", "stealth", "modern",
    "prestige", "aorus", "lg gram", "surface", "proart", "matebook",
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
    return "".join(c for c in value if not unicodedata.combining(c))


def is_laptop(value) -> bool:
    value = fold(value)
    return (
        len(value) > 4
        and any(term in value for term in LAPTOP_TERMS)
        and not any(term in value for term in EXCLUDE_TERMS)
    )


def canonical_url(url: str, base: str = "") -> str:
    url = urljoin(base, clean(url))
    parts = urlsplit(url)
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


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4, connect=4, read=4, status=4, backoff_factor=1,
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
                "Accept": "application/json,*/*" if json_mode else
                          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=timeout,
            allow_redirects=True,
        )
        print(f" GET {response.status_code} {len(response.content)} {url}", flush=True)
        return response if response.status_code == 200 else None
    except requests.RequestException as exc:
        print(f" GET ERROR {url} {exc}", flush=True)
        return None


def product_name(card, anchor) -> str:
    if getattr(card, "select_one", None):
        for selector in NAME_SELECTORS:
            node = card.select_one(selector)
            if node:
                name = clean(node.get_text(" "))
                if name:
                    return name
    return clean(anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" "))


def extract_products(document: str, base: str, pattern: str | None = None,
                     loose: bool = False) -> list[dict]:
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
            name = product_name(card, anchor)
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


def shopify_collection(base: str, collections: list[str], label: str) -> list[dict]:
    session = build_session()
    output, seen = [], set()
    for collection in collections:
        for page in range(1, 201):
            url = f"{base}/collections/{collection}/products.json?limit=250&page={page}"
            response = fetch(session, url, json_mode=True)
            if not response:
                break
            try:
                items = (response.json() or {}).get("products") or []
            except ValueError:
                items = []
            if not items:
                break
            new_count = 0
            for item in items:
                name = clean(item.get("title") or item.get("name"))
                handle = clean(item.get("handle"))
                url = canonical_url(item.get("url") or (f"/products/{handle}" if handle else ""), base)
                key = str(item.get("id") or url)
                if name and url and key not in seen and is_laptop(name):
                    seen.add(key)
                    output.append({"name": name, "url": url})
                    new_count += 1
            log(label, f"JSON {collection} page={page} received={len(items)} new={new_count} total={len(output)}")
            if new_count == 0:
                break
            time.sleep(DELAY * 0.25)
    return deduplicate(output)


def paged(label: str, base: str, starts: list[str], pattern: str,
          params=("page",), loose=False, max_pages=100) -> list[dict]:
    session = build_session()
    output, seen = [], set()
    for start in starts:
        repeated = 0
        for page_number in range(1, max_pages + 1):
            page_urls = [start] if page_number == 1 else [
                f"{start}{'&' if '?' in start else '?'}{param}={page_number}"
                for param in params
            ]
            best = []
            for page_url in page_urls:
                response = fetch(session, page_url)
                if response:
                    rows = extract_products(response.text, base, pattern, loose)
                    if len(rows) > len(best):
                        best = rows
            fresh = [row for row in best if row["url"] not in seen]
            for row in fresh:
                seen.add(row["url"])
                output.append(row)
            log(label, f"page={page_number} found={len(best)} new={len(fresh)} total={len(output)}")
            if not best:
                break
            if not fresh:
                repeated += 1
                if repeated >= 2:
                    break
            else:
                repeated = 0
            time.sleep(DELAY)
    return deduplicate(output)


def path_paged(label: str, base: str, start_path: str, pattern: str,
               loose=False, max_pages=100) -> list[dict]:
    session = build_session()
    output, seen = [], set()
    repeated = 0
    for page_number in range(1, max_pages + 1):
        page_url = f"{base}{start_path}" if page_number == 1 else \
                   f"{base}{start_path.rstrip('/')}/{page_number}/"
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
        if not fresh:
            repeated += 1
            if repeated >= 2:
                break
        else:
            repeated = 0
        time.sleep(DELAY)
    return deduplicate(output)


def slug_name(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.(?:html?|aspx?)$", "", slug, flags=re.I)
    return clean(slug.replace("-", " ").replace("_", " "))


def sitemap_products(label: str, base: str, include: list[str], exclude=(),
                     max_sitemaps=100) -> list[dict]:
    session = build_session()
    queue = [
        f"{base}/sitemap.xml", f"{base}/sitemap_index.xml",
        f"{base}/sitemap-product.xml", f"{base}/sitemap_product.xml",
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
            elif any(re.search(rule, url, re.I) for rule in include) and not any(
                re.search(rule, url, re.I) for rule in exclude
            ):
                urls.add(url)
        log(label, f"sitemap={len(visited)} products={len(urls)}")
    return deduplicate({"name": slug_name(url), "url": url} for url in sorted(urls))


async def rendered(label: str, base: str, starts: list[str], pattern: str,
                   loose=False, max_rounds=100) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    output, seen = [], set()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS), locale="vi-VN",
            viewport={"width": 1440, "height": 1000},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await context.new_page()

        for start in starts:
            try:
                await page.goto(start, wait_until="domcontentloaded", timeout=50000)
                await page.wait_for_timeout(3000)
            except Exception as exc:
                log(label, f"NAV {exc}")
                continue

            stale = 0
            for round_number in range(max_rounds):
                await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                await page.wait_for_timeout(1300)
                rows = extract_products(await page.content(), base, pattern, loose)
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


# Site crawlers ---------------------------------------------------------------

def crawl_gearvn(no_playwright=False):
    base = "https://gearvn.com"
    starts = [f"{base}/collections/laptop", f"{base}/collections/laptop-gaming-ban-chay"]
    pattern = r"gearvn\.com/products/[a-z0-9-]+$"
    rows = paged("GEARVN", base, starts, pattern, params=("page",), loose=True, max_pages=50)
    return rows or ([] if no_playwright else asyncio.run(
        rendered("GEARVN", base, starts, pattern, loose=True)
    ))


def crawl_laptopnew(_no_playwright=False):
    base = "https://laptopnew.vn"
    rows = shopify_collection(base, ["laptop-gaming", "laptop-van-phong", "laptop"], "LAPTOPNEW")
    return rows or paged(
        "LAPTOPNEW", base,
        [f"{base}/collections/laptop-gaming", f"{base}/collections/laptop-van-phong"],
        r"laptopnew\.vn/(?:products/)?[a-z0-9-]+$", loose=True,
    )


def crawl_cellphones(no_playwright=False):
    base = "https://cellphones.com.vn"
    starts = [
        f"{base}/laptop.html", f"{base}/laptop/van-phong.html",
        f"{base}/laptop/gaming.html", f"{base}/laptop/do-hoa.html",
        f"{base}/laptop/sinh-vien.html", f"{base}/laptop/mong-nhe.html",
        f"{base}/laptop/asus.html", f"{base}/laptop/acer.html",
        f"{base}/laptop/dell.html", f"{base}/laptop/hp.html",
        f"{base}/laptop/lenovo.html", f"{base}/laptop/msi.html",
        f"{base}/laptop/gigabyte.html", f"{base}/laptop/lg.html",
    ]
    pattern = r"cellphones\.com\.vn/(?!laptop(?:/|\.html$))[a-z0-9-/]+\.html$"
    rows = [] if no_playwright else asyncio.run(
        rendered("CELLPHONES", base, starts, pattern, loose=True, max_rounds=100)
    )
    map_rows = sitemap_products(
        "CELLPHONES", base,
        include=[r"cellphones\.com\.vn/(?:laptop|macbook)/[a-z0-9-]+\.html$"],
        exclude=[r"/laptop/(?:asus|acer|dell|hp|lenovo|msi|gigabyte|lg|gaming|van-phong|do-hoa|sinh-vien|mong-nhe)\.html$"],
    )
    return deduplicate(rows + map_rows) or paged(
        "CELLPHONES", base, starts, pattern, loose=True
    )


def crawl_laptopworld(_no_playwright=False):
    base = "https://laptopworld.vn"
    return paged(
        "LAPTOPWORLD", base,
        [f"{base}/laptop-van-phong.html", f"{base}/laptop-games-do-hoa.html"],
        r"laptopworld\.vn/(?!laptop-(?:van-phong|games-do-hoa)\.html$)(?:[a-z0-9-]+/)*[a-z0-9-]+(?:\.html)?$",
        params=("page",), loose=True,
    )


def crawl_laptop88(no_playwright=False):
    base = "https://laptop88.vn"
    starts = [f"{base}/may-tinh-xach-tay.html"]
    pattern = r"laptop88\.vn/(?!may-tinh-xach-tay\.html$|tin-tuc/|khuyen-mai/|thuong-hieu/|nhu-cau/|phu-kien/)[a-z0-9][a-z0-9-/]+(?:\.html)?$"
    rows = [] if no_playwright else asyncio.run(
        rendered("LAPTOP88", base, starts, pattern, loose=True, max_rounds=100)
    )
    map_rows = sitemap_products(
        "LAPTOP88", base,
        include=[r"laptop88\.vn/(?:new-100-|laptop-)[a-z0-9-]+(?:\.html)?$"],
        exclude=[r"/laptop-(?:asus|acer|dell|hp|lenovo|msi|gaming|van-phong)(?:\.html)?$"],
    )
    return deduplicate(rows + map_rows) or paged(
        "LAPTOP88", base, starts, pattern, params=("page",), loose=True
    )


def crawl_anphatpc(_no_playwright=False):
    base = "https://www.anphatpc.com.vn"
    rows = paged(
        "ANPHATPC", base,
        [f"{base}/may-tinh-xach-tay-laptop.html", f"{base}/gaming-laptop.html", f"{base}/laptop-do-hoa.html"],
        r"anphatpc\.com\.vn/(?!may-tinh-xach-tay-laptop\.html$|gaming-laptop\.html$|laptop-do-hoa\.html$)[a-z0-9-/]+\.html$",
        params=("page",), loose=True, max_pages=100,
    )
    map_rows = sitemap_products(
        "ANPHATPC", base,
        include=[r"anphatpc\.com\.vn/[a-z0-9-]*(?:laptop|macbook)[a-z0-9-]*\.html$"],
        exclude=[r"/(?:may-tinh-xach-tay-laptop|gaming-laptop|laptop-do-hoa|laptop-theo-hang)\.html$"],
    )
    return deduplicate(rows + map_rows)


def crawl_hacom(_no_playwright=False):
    base = "https://hacom.vn"
    pattern = r"hacom\.vn/laptop-(?!gaming-do-hoa$)[a-z0-9-]+$"
    category_rows = path_paged(
        "HACOM", base, "/laptop", pattern, loose=True, max_pages=80
    )
    brand_starts = [
        f"{base}/laptop-asus-vivobook", f"{base}/laptop-asus-zenbook",
        f"{base}/laptop-asus-rog", f"{base}/laptop-asus-tuf",
        f"{base}/laptop-dell-inspiron", f"{base}/laptop-dell-latitude",
        f"{base}/laptop-dell-xps", f"{base}/laptop-hp-pavilion",
        f"{base}/laptop-hp-victus", f"{base}/laptop-hp-elitebook",
        f"{base}/laptop-lenovo-ideapad", f"{base}/laptop-lenovo-thinkpad",
        f"{base}/laptop-lenovo-legion", f"{base}/laptop-lenovo-loq",
        f"{base}/laptop-msi-gaming", f"{base}/laptop-msi-modern",
        f"{base}/laptop-apple-macbook-air", f"{base}/laptop-apple-macbook-pro",
    ]
    brand_rows = paged(
        "HACOM-BRANDS", base, brand_starts, pattern,
        params=("page",), loose=True, max_pages=30,
    )
    map_rows = sitemap_products(
        "HACOM", base,
        include=[r"hacom\.vn/laptop-[a-z0-9-]+$"],
        exclude=[r"/laptop-(?:gaming-do-hoa|ai|asus|acer|dell|hp|lenovo|msi|apple|lg)(?:-[a-z0-9-]+)?$"],
    )
    return deduplicate(category_rows + brand_rows + map_rows)


CRAWLERS = {
    "gearvn": crawl_gearvn,
    "xgear": lambda _n: shopify_collection("https://xgear.net", ["laptop"], "XGEAR"),
    "tinhocngoisao": lambda _n: shopify_collection("https://tinhocngoisao.com", ["laptop"], "TINHOCNGOISAO"),
    "hangchinhhieu": lambda _n: shopify_collection(
        "https://hangchinhhieu.vn", ["laptop", "laptop-gaming-do-hoa-studio"], "HANGCHINHHIEU"
    ),
    "laptopnew": crawl_laptopnew,
    "memoryzone": lambda _n: shopify_collection("https://memoryzone.com.vn", ["laptop"], "MEMORYZONE") or paged(
        "MEMORYZONE", "https://memoryzone.com.vn", ["https://memoryzone.com.vn/laptop"],
        r"memoryzone\.com\.vn/(?:products/)?[a-z0-9-]+$", loose=True,
    ),
    "cellphones": crawl_cellphones,
    "hoanghamobile": lambda _n: paged(
        "HOANGHAMOBILE", "https://hoanghamobile.com", ["https://hoanghamobile.com/laptop"],
        r"hoanghamobile\.com/laptop/(?!phan-loai-san-pham/)[a-z0-9-]+$",
        params=("p",), loose=True,
    ),
    "laptopworld": crawl_laptopworld,
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


def main() -> int:
    global DELAY
    parser = argparse.ArgumentParser(description="Vietnam Laptop Crawler v3.6")
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
            break
        except Exception as exc:
            log(site, f"FATAL {exc}")
            rows = []
        all_rows.extend({"site": site, **row} for row in rows)
        save_csv(all_rows, args.output)
        log(site, f"SAVED {len(rows)}")

    if args.json:
        json_path = os.path.splitext(args.output)[0] + ".json"
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(all_rows, file, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
