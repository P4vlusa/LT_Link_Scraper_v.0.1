#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vietnam Laptop Crawler v3.0

Collect laptop product names and links from 13 Vietnamese retailers.

Install:
    pip install requests beautifulsoup4 lxml playwright
    python -m playwright install chromium

Examples:
    python vietnam_laptop_crawler_v3.py
    python vietnam_laptop_crawler_v3.py --sites gearvn laptopworld hacom
    python vietnam_laptop_crawler_v3.py --output laptops.csv --delay 1
    python vietnam_laptop_crawler_v3.py --no-playwright

Notes:
- Public storefronts change frequently. The crawler combines Shopify JSON,
  sitemaps, HTML parsing, and Playwright fallbacks.
- Please respect each site's robots.txt, terms, and rate limits.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import os
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_OUTPUT = "vietnam_laptops.csv"
DEFAULT_DELAY = 0.75
DELAY = DEFAULT_DELAY

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
]

TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "ref", "source",
}

LAPTOP_TERMS = (
    "laptop", "may tinh xach tay", "macbook", "notebook", "chromebook",
    "vivobook", "zenbook", "expertbook", "rog", "tuf gaming",
    "ideapad", "thinkpad", "thinkbook", "legion", "lenovo loq",
    "aspire", "nitro", "travelmate", "swift", "predator",
    "pavilion", "victus", "elitebook", "probook", "omnibook",
    "inspiron", "latitude", "vostro", "dell xps", "alienware",
    "msi modern", "msi prestige", "msi katana", "msi cyborg",
    "msi stealth", "msi vector", "msi raider", "gigabyte gaming",
    "aorus", "lg gram", "surface laptop",
)

NON_LAPTOP_TERMS = (
    "balo", "tui laptop", "de laptop", "gia do laptop", "ban laptop",
    "sac laptop", "adapter", "pin laptop", "ram laptop", "o cung",
    "ban phim", "chuot", "tai nghe", "man hinh", "bao hanh", "ve sinh",
    "phu kien", "linh kien", "dock", "hub", "skin", "mieng dan",
)

PRODUCT_SELECTORS = (
    ".product-item", ".product-card", ".product-loop", ".proloop",
    ".p-item", "li.product", "article.product", "[data-product-id]",
    ".item-product", ".product-info-container",
)
NAME_SELECTORS = (
    ".product-name", ".product__name", ".product-item-name", ".p-name",
    ".proloop-title", ".hover_name", "h2", "h3", "h4",
)


def log(site: str, message: str) -> None:
    print(f"  [{site}] {message}", flush=True)


def clean(value: object) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def ascii_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", clean(value).lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def looks_like_laptop(text: str) -> bool:
    value = ascii_text(text)
    if any(term in value for term in NON_LAPTOP_TERMS):
        return False
    return any(term in value for term in LAPTOP_TERMS)


def canonical_url(url: str, base: str = "") -> str:
    value = clean(url)
    if not value or value.startswith(("javascript:", "mailto:", "tel:", "#")):
        return ""
    value = urljoin(base, value)
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_KEYS]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       urlencode(query), ""))


def dedup(products: Iterable[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for item in products:
        name = clean(item.get("name"))
        url = canonical_url(item.get("url", ""))
        if not url or url in seen or len(name) < 5:
            continue
        seen.add(url)
        result.append({"name": name, "url": url})
    return result


def sleep(factor: float = 1.0) -> None:
    time.sleep(max(0, DELAY * factor * random.uniform(0.85, 1.15)))


def headers(json_mode: bool = False) -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.7,en;q=0.6",
        "Accept": "application/json,text/plain,*/*" if json_mode else
                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(("GET", "HEAD")),
        respect_retry_after_header=True,
    )
    session.mount("http://", HTTPAdapter(max_retries=retry, pool_connections=10,
                                         pool_maxsize=10))
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=10,
                                          pool_maxsize=10))
    return session


def fetch(session: requests.Session, url: str, *, json_mode: bool = False,
          timeout: int = 30) -> requests.Response | None:
    try:
        response = session.get(url, headers=headers(json_mode), timeout=timeout,
                               allow_redirects=True)
        if response.status_code != 200:
            log("HTTP", f"{response.status_code} {url}")
            return None
        return response
    except requests.RequestException as exc:
        log("HTTP", f"{url}: {exc}")
        return None


def soup_from_url(session: requests.Session, url: str) -> BeautifulSoup | None:
    response = fetch(session, url)
    if not response or len(response.content) < 100:
        return None
    return BeautifulSoup(response.text, "lxml")


def extract_page_name(soup: BeautifulSoup | None) -> str:
    if not soup:
        return ""
    for selector in ("h1", '[property="og:title"]', "title"):
        item = soup.select_one(selector)
        if not item:
            continue
        name = clean(item.get("content") if item.name == "meta" else item.get_text(" "))
        name = re.sub(r"\s*[|\-–]\s*(GEARVN|HACOM|CellphoneS|Laptop88).*$",
                      "", name, flags=re.I)
        if name:
            return name
    return ""


def parse_sitemap_xml(xml_text: str, base: str) -> tuple[list[str], list[str]]:
    soup = BeautifulSoup(xml_text, "xml")
    urls = [canonical_url(loc.get_text(), base) for loc in soup.find_all("loc")]
    urls = [url for url in urls if url]
    sitemaps = [url for url in urls if "sitemap" in urlsplit(url).path.lower()
                and urlsplit(url).path.lower().endswith((".xml", ".xml.gz"))]
    pages = [url for url in urls if url not in sitemaps]
    return sitemaps, pages


def discover_sitemap_urls(base: str, label: str, max_sitemaps: int = 50) -> list[str]:
    session = make_session()
    candidates = [
        f"{base.rstrip('/')}/sitemap.xml",
        f"{base.rstrip('/')}/sitemap_index.xml",
        f"{base.rstrip('/')}/sitemap_products_1.xml",
        f"{base.rstrip('/')}/sitemap_product.xml",
    ]
    queue = list(dict.fromkeys(candidates))
    visited: set[str] = set()
    product_urls: set[str] = set()

    while queue and len(visited) < max_sitemaps:
        sitemap_url = queue.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        response = fetch(session, sitemap_url)
        if not response:
            continue
        nested, pages = parse_sitemap_xml(response.text, base)
        for nested_url in nested:
            if nested_url not in visited and nested_url not in queue:
                queue.append(nested_url)
        for page_url in pages:
            path = urlsplit(page_url).path.lower()
            if any(token in path for token in ("/products/", "/product/", ".html")):
                product_urls.add(page_url)
        log(label, f"sitemap {len(visited)}: total candidates {len(product_urls)}")
        sleep(0.15)
    return sorted(product_urls)


def extract_cards(soup: BeautifulSoup, base: str,
                  product_url_regex: str | None = None,
                  require_laptop_name: bool = True) -> list[dict]:
    rows: list[dict] = []
    cards = []
    for selector in PRODUCT_SELECTORS:
        cards.extend(soup.select(selector))
    if not cards:
        cards = soup.find_all("a", href=True)

    for card in cards:
        if getattr(card, "name", None) == "a":
            anchor = card
        else:
            anchor = card.select_one("a[href]")
        if not anchor:
            continue
        url = canonical_url(anchor.get("href", ""), base)
        if not url:
            continue
        if product_url_regex and not re.search(product_url_regex, url, re.I):
            continue

        name = ""
        if getattr(card, "name", None) != "a":
            for selector in NAME_SELECTORS:
                node = card.select_one(selector)
                if node:
                    name = clean(node.get_text(" "))
                    if name:
                        break
        if not name:
            name = clean(anchor.get("title") or anchor.get("aria-label") or
                         anchor.get_text(" "))
        if not name or len(name) < 5:
            continue
        if require_laptop_name and not looks_like_laptop(name):
            continue
        rows.append({"name": name, "url": url})
    return dedup(rows)


def crawl_html_pages(label: str, base: str, starts: list[str],
                     product_url_regex: str, max_pages: int = 80,
                     page_params: tuple[str, ...] = ("page", "p"),
                     require_laptop_name: bool = True) -> list[dict]:
    session = make_session()
    products: list[dict] = []
    global_seen: set[str] = set()

    for start in starts:
        repeated = 0
        for page_no in range(1, max_pages + 1):
            candidates = [start] if page_no == 1 else [
                f"{start}{'&' if '?' in start else '?'}{param}={page_no}"
                for param in page_params
            ]
            best_rows: list[dict] = []
            for page_url in candidates:
                soup = soup_from_url(session, page_url)
                if not soup:
                    continue
                rows = extract_cards(soup, base, product_url_regex,
                                     require_laptop_name=require_laptop_name)
                if len(rows) > len(best_rows):
                    best_rows = rows
            if not best_rows:
                log(label, f"{start} page {page_no}: no products")
                break
            new_rows = [row for row in best_rows if row["url"] not in global_seen]
            for row in new_rows:
                global_seen.add(row["url"])
                products.append(row)
            log(label, f"page {page_no}: received={len(best_rows)}, "
                       f"new={len(new_rows)}, total={len(products)}")
            if not new_rows:
                repeated += 1
                if repeated >= 1:
                    break
            else:
                repeated = 0
            sleep(0.4)
    return dedup(products)


def shopify_collection(base: str, collections: list[str], label: str,
                       max_pages: int = 200) -> list[dict]:
    session = make_session()
    rows: list[dict] = []
    seen_ids: set[str] = set()

    for collection in collections:
        stagnant = 0
        for page in range(1, max_pages + 1):
            url = (f"{base.rstrip('/')}/collections/{collection}/products.json"
                   f"?limit=250&page={page}")
            response = fetch(session, url, json_mode=True)
            if not response:
                break
            try:
                items = response.json().get("products") or []
            except (ValueError, AttributeError):
                log(label, f"invalid JSON: {url}")
                break
            if not items:
                break

            new_count = 0
            for item in items:
                title = clean(item.get("title") or item.get("name"))
                handle = clean(item.get("handle"))
                item_url = canonical_url(item.get("url", ""), base)
                if not item_url and handle:
                    item_url = canonical_url(f"/products/{handle}", base)
                identity = str(item.get("id") or handle or item_url)
                if not title or not item_url or identity in seen_ids:
                    continue
                seen_ids.add(identity)
                rows.append({"name": title, "url": item_url})
                new_count += 1

            log(label, f"{collection} page {page}: received={len(items)}, "
                       f"new={new_count}, total={len(rows)}")
            if new_count == 0:
                stagnant += 1
                if stagnant >= 1:
                    break
            else:
                stagnant = 0
            sleep(0.3)
    return dedup(rows)


async def playwright_categories(label: str, base: str, starts: list[str],
                                product_url_regex: str,
                                max_clicks: int = 60) -> list[dict] | None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    rows: list[dict] = []
    seen: set[str] = set()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--ignore-certificate-errors",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1440, "height": 1000},
            ignore_https_errors=True,
            locale="vi-VN",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        for start in starts:
            try:
                await page.goto(start, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2500)
            except Exception as exc:
                log(label, f"navigation failed {start}: {exc}")
                continue

            stale_rounds = 0
            for click_no in range(max_clicks + 1):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)
                soup = BeautifulSoup(await page.content(), "lxml")
                current = extract_cards(soup, base, product_url_regex,
                                        require_laptop_name=True)
                fresh = [row for row in current if row["url"] not in seen]
                for row in fresh:
                    seen.add(row["url"])
                    rows.append(row)
                log(label, f"click {click_no}: visible={len(current)}, "
                           f"new={len(fresh)}, total={len(rows)}")
                stale_rounds = stale_rounds + 1 if not fresh else 0

                button = None
                for selector in (
                    "a.btn-show-more", "button.btn-show-more",
                    ".button__show-more-product", "[class*='show-more']",
                    "text=/Xem thêm|Tải thêm|Hiển thị thêm/i",
                ):
                    try:
                        locator = page.locator(selector).first
                        if await locator.is_visible(timeout=500):
                            button = locator
                            break
                    except Exception:
                        pass
                if button is None or stale_rounds >= 2:
                    break
                try:
                    await button.scroll_into_view_if_needed()
                    await button.click(timeout=5000)
                    await page.wait_for_timeout(2500)
                except Exception:
                    break
        await browser.close()
    return dedup(rows)


# Site crawlers ---------------------------------------------------------------

def crawl_gearvn() -> list[dict]:
    label, base = "GEARVN", "https://gearvn.com"
    print("\n[1/13] GEARVN ...", flush=True)
    # Collection JSON first; sitemap metadata fallback if collection is blocked.
    rows = shopify_collection(base, ["laptop", "laptop-gaming"], label)
    if len(rows) >= 10:
        return rows
    session = make_session()
    candidates = discover_sitemap_urls(base, label)
    products: list[dict] = []
    for idx, url in enumerate(candidates, 1):
        if "/products/" not in url:
            continue
        handle = urlsplit(url).path.split("/products/", 1)[-1].strip("/")
        response = fetch(session, f"{base}/products/{handle}.json", json_mode=True,
                         timeout=20)
        if not response:
            continue
        try:
            item = response.json().get("product") or {}
        except ValueError:
            continue
        title = clean(item.get("title"))
        searchable = " ".join([
            title, clean(item.get("product_type")), clean(item.get("vendor")),
            " ".join(item.get("tags") or []),
        ])
        if title and looks_like_laptop(searchable):
            products.append({"name": title, "url": url})
        if idx % 100 == 0:
            log(label, f"checked={idx}, laptops={len(products)}")
        sleep(0.08)
    return dedup(products)


def crawl_xgear() -> list[dict]:
    print("\n[2/13] XGEAR ...", flush=True)
    return shopify_collection("https://xgear.net", ["laptop"], "XGEAR")


def crawl_tinhocngoisao() -> list[dict]:
    print("\n[3/13] TINHOCNGOISAO ...", flush=True)
    return shopify_collection("https://tinhocngoisao.com", ["laptop"],
                              "TINHOCNGOISAO")


def crawl_hangchinhhieu() -> list[dict]:
    print("\n[4/13] HANGCHINHHIEU ...", flush=True)
    rows = shopify_collection(
        "https://hangchinhhieu.vn",
        ["laptop", "laptop-gaming-do-hoa-studio"],
        "HANGCHINHHIEU",
    )
    return [row for row in rows if looks_like_laptop(row["name"])]


def crawl_laptopnew() -> list[dict]:
    print("\n[5/13] LAPTOPNEW ...", flush=True)
    return shopify_collection("https://laptopnew.vn",
                              ["laptop-gaming", "laptop-van-phong", "laptop"],
                              "LAPTOPNEW")


def crawl_memoryzone() -> list[dict]:
    print("\n[6/13] MEMORYZONE ...", flush=True)
    rows = shopify_collection("https://memoryzone.com.vn", ["laptop"],
                              "MEMORYZONE")
    if len(rows) >= 10:
        return rows
    return crawl_html_pages(
        "MEMORYZONE", "https://memoryzone.com.vn",
        ["https://memoryzone.com.vn/laptop",
         "https://memoryzone.com.vn/laptop-do-hoa"],
        r"memoryzone\.com\.vn/(?:products/)?[^/?#]+$",
    )


def crawl_cellphones(no_playwright: bool = False) -> list[dict]:
    print("\n[7/13] CELLPHONES ...", flush=True)
    base = "https://cellphones.com.vn"
    starts = [
        f"{base}/laptop.html", f"{base}/laptop/van-phong.html",
        f"{base}/laptop/gaming.html", f"{base}/laptop/do-hoa.html",
        f"{base}/laptop/sinh-vien.html", f"{base}/laptop/mong-nhe.html",
        f"{base}/laptop/asus.html", f"{base}/laptop/hp.html",
        f"{base}/laptop/lenovo.html", f"{base}/laptop/acer.html",
        f"{base}/laptop/dell.html", f"{base}/laptop/msi.html",
        f"{base}/laptop/gigabyte.html", f"{base}/laptop/lg.html",
        f"{base}/laptop/apple.html",
    ]
    regex = r"cellphones\.com\.vn/(?!laptop(?:/|\.html?$))[^?#]+\.html$"
    if not no_playwright:
        rows = asyncio.run(playwright_categories("CELLPHONES", base, starts, regex))
        if rows:
            return rows
    return crawl_html_pages("CELLPHONES", base, starts, regex,
                            max_pages=30, page_params=("page",))


def crawl_hoanghamobile() -> list[dict]:
    print("\n[8/13] HOANGHAMOBILE ...", flush=True)
    base = "https://hoanghamobile.com"
    starts = [
        f"{base}/laptop", f"{base}/laptop/van-phong-sinh-vien",
        f"{base}/laptop/phan-loai-san-pham/do-hoa-ki-thuat",
        f"{base}/laptop/phan-loai-san-pham/laptop-gaming",
        f"{base}/laptop/macbook", f"{base}/laptop/asus",
        f"{base}/laptop/dell", f"{base}/laptop/hp",
        f"{base}/laptop/lenovo", f"{base}/laptop/acer",
        f"{base}/laptop/msi", f"{base}/laptop/lg",
    ]
    return crawl_html_pages(
        "HOANGHAMOBILE", base, starts,
        r"hoanghamobile\.com/laptop/(?!phan-loai-san-pham/)[^/?#]+$",
        page_params=("p", "page"),
    )


def crawl_laptopworld() -> list[dict]:
    print("\n[9/13] LAPTOPWORLD ...", flush=True)
    base = "https://laptopworld.vn"
    return crawl_html_pages(
        "LAPTOPWORLD", base,
        [f"{base}/laptop-van-phong.html", f"{base}/laptop-games-do-hoa.html"],
        r"laptopworld\.vn/(?!laptop-(?:van-phong|games-do-hoa)\.html$)[^/?#]+\.html$",
        page_params=("page",),
    )


def crawl_laptop88() -> list[dict]:
    print("\n[10/13] LAPTOP88 ...", flush=True)
    base = "https://laptop88.vn"
    return crawl_html_pages(
        "LAPTOP88", base, [f"{base}/may-tinh-xach-tay.html"],
        r"laptop88\.vn/new-100-[a-z0-9-]+(?:\.html)?$",
        page_params=("page",), require_laptop_name=False,
    )


def crawl_anphatpc() -> list[dict]:
    print("\n[11/13] ANPHATPC ...", flush=True)
    label, base = "ANPHATPC", "https://www.anphatpc.com.vn"
    urls = discover_sitemap_urls(base, label)
    session = make_session()
    rows: list[dict] = []
    for idx, url in enumerate(urls, 1):
        if not looks_like_laptop(url):
            continue
        if any(term in ascii_text(url) for term in NON_LAPTOP_TERMS):
            continue
        soup = soup_from_url(session, url)
        name = extract_page_name(soup)
        if name and looks_like_laptop(name):
            rows.append({"name": name, "url": url})
        if idx % 100 == 0:
            log(label, f"checked={idx}, laptops={len(rows)}")
        sleep(0.12)
    return dedup(rows)


def crawl_hacom(no_playwright: bool = False) -> list[dict]:
    print("\n[12/13] HACOM ...", flush=True)
    base = "https://hacom.vn"
    starts = [f"{base}/laptop", f"{base}/laptop-gaming-do-hoa"]
    regex = r"hacom\.vn/laptop-(?!gaming-do-hoa$|tablets?$)[a-z0-9][a-z0-9-]+$"
    if not no_playwright:
        rows = asyncio.run(playwright_categories("HACOM", base, starts, regex))
        if rows:
            return rows
    brands = [
        "asus-vivobook", "asus-zenbook", "asus-expertbook", "asus-rog",
        "asus-tuf", "dell-inspiron", "dell-latitude", "dell-xps",
        "dell-vostro", "acer-aspire", "acer-gaming", "acer-swift",
        "hp-pavilion", "hp-victus", "hp-elitebook", "lenovo-ideapad",
        "lenovo-thinkpad", "lenovo-legion", "lenovo-loq", "msi-gaming",
        "msi-modern", "apple-macbook-air", "apple-macbook-pro",
        "gigabyte-gaming", "lg-gram",
    ]
    return crawl_html_pages("HACOM", base,
                            starts + [f"{base}/laptop-{b}" for b in brands],
                            regex, page_params=("page",))


def crawl_phucanh() -> list[dict]:
    print("\n[13/13] PHUCANH ...", flush=True)
    base = "https://www.phucanh.vn"
    starts = [
        f"{base}/may-tinh-xach-tay-laptop.html",
        f"{base}/may-tinh-xach-tay-laptop-dell.html",
        f"{base}/may-tinh-xach-tay-laptop-asus.html",
        f"{base}/may-tinh-xach-tay-laptop-hp.html",
        f"{base}/may-tinh-xach-tay-laptop-acer.html",
        f"{base}/may-tinh-xach-tay-laptop-lenovo.html",
        f"{base}/may-tinh-xach-tay-laptop-msi.html",
        f"{base}/laptop-lg.html", f"{base}/laptop-apple.html",
    ]
    category_names = (
        "may-tinh-xach-tay-laptop", "laptop-lg", "laptop-apple",
        "laptop-gaming", "laptop-van-phong", "laptop-mong-nhe",
        "laptop-cao-cap",
    )
    negative = "|".join(re.escape(name) for name in category_names)
    regex = rf"phucanh\.vn/(?!({negative})(?:-[a-z]+)?\.html$)[a-z0-9-]+\.html$"
    return crawl_html_pages("PHUCANH", base, starts, regex,
                            max_pages=30, page_params=("page",))


@dataclass(frozen=True)
class Site:
    name: str
    crawler: object
    playwright: bool = False


SITES = {
    "gearvn": Site("gearvn", crawl_gearvn),
    "xgear": Site("xgear", crawl_xgear),
    "tinhocngoisao": Site("tinhocngoisao", crawl_tinhocngoisao),
    "hangchinhhieu": Site("hangchinhhieu", crawl_hangchinhhieu),
    "laptopnew": Site("laptopnew", crawl_laptopnew),
    "memoryzone": Site("memoryzone", crawl_memoryzone),
    "cellphones": Site("cellphones", crawl_cellphones, True),
    "hoanghamobile": Site("hoanghamobile", crawl_hoanghamobile),
    "laptopworld": Site("laptopworld", crawl_laptopworld),
    "laptop88": Site("laptop88", crawl_laptop88),
    "anphatpc": Site("anphatpc", crawl_anphatpc),
    "hacom": Site("hacom", crawl_hacom, True),
    "phucanh": Site("phucanh", crawl_phucanh),
}


def save_csv(products: list[dict], filename: str) -> str:
    path = os.path.abspath(filename)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["site", "name", "url"])
        writer.writeheader()
        writer.writerows(products)
    return path


def save_json(products: list[dict], filename: str) -> str:
    path = os.path.splitext(os.path.abspath(filename))[0] + ".json"
    with open(path, "w", encoding="utf-8") as file:
        json.dump(products, file, ensure_ascii=False, indent=2)
    return path


def main() -> int:
    global DELAY
    parser = argparse.ArgumentParser(description="Vietnam Laptop Crawler v3.0")
    parser.add_argument("--sites", nargs="+", choices=sorted(SITES))
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--no-playwright", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="also save a JSON copy")
    args = parser.parse_args()
    DELAY = max(0.05, args.delay)
    selected = args.sites or list(SITES)

    print("=" * 68)
    print("Vietnam Laptop Crawler v3.0")
    print(f"Sites: {', '.join(selected)}")
    print(f"Delay: {DELAY}s")
    print("=" * 68)

    combined: list[dict] = []
    summary: dict[str, int] = {}
    for site_name in selected:
        site = SITES[site_name]
        try:
            if site.playwright:
                rows = site.crawler(args.no_playwright)
            else:
                rows = site.crawler()
        except KeyboardInterrupt:
            print("\nInterrupted; saving collected data.")
            break
        except Exception as exc:
            log(site_name.upper(), f"fatal error: {exc}")
            rows = []

        rows = dedup(rows)
        summary[site_name] = len(rows)
        combined.extend({"site": site_name, **row} for row in rows)
        combined = dedup_with_site(combined)
        save_csv(combined, args.output)
        log(site_name.upper(), f"saved {len(rows)}; combined={len(combined)}")

    output_path = save_csv(combined, args.output)
    if args.json:
        save_json(combined, args.output)

    print("\n" + "=" * 68)
    for site_name in selected:
        print(f"{site_name:24s}: {summary.get(site_name, 0):6d}")
    print(f"TOTAL                   : {len(combined):6d}")
    print(f"OUTPUT                  : {output_path}")
    print("=" * 68)
    return 0


def dedup_with_site(products: Iterable[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for item in products:
        url = canonical_url(item.get("url", ""))
        name = clean(item.get("name"))
        site = clean(item.get("site"))
        if not url or not name or url in seen:
            continue
        seen.add(url)
        result.append({"site": site, "name": name, "url": url})
    return result


if __name__ == "__main__":
    raise SystemExit(main())
