#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vietnam Laptop Crawler v3.4 - GitHub Actions edition.

12 supported sites. Five sites known to block GitHub-hosted runner IPs were removed.
GEARVN v3.3 change: crawl collection pages directly with ?page=N first;
no longer calls the unavailable Shopify products.json endpoint.
"""

import argparse
import asyncio
import csv
import html
import json
import os
import random
import re
import time
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DELAY = 1.0
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
]

LAPTOP_TERMS = (
    "laptop", "macbook", "notebook", "chromebook", "vivobook", "zenbook",
    "expertbook", "ideapad", "thinkpad", "thinkbook", "legion", " loq",
    "aspire", "nitro", "swift", "predator", "pavilion", "victus",
    "elitebook", "probook", "omnibook", "inspiron", "latitude", "vostro",
    "xps", "alienware", "katana", "cyborg", "stealth", "modern",
    "prestige", "aorus", "lg gram", "surface",
)
EXCLUDE_TERMS = (
    "balo", "tui laptop", "de laptop", "gia do laptop", "sac laptop",
    "adapter", "pin laptop", "ram laptop", "ban phim", "chuot", "tai nghe",
    "man hinh", "bao hanh", "phu kien", "linh kien", "ve sinh laptop",
)
CARD_SELECTORS = (
    ".p-item", ".product-item", ".product-card", ".product-info-container",
    ".item-product", ".product-loop", ".proloop", "li.product",
    "article.product", "[data-product-id]",
)
NAME_SELECTORS = (
    ".hover_name", ".p-name", ".product-name", ".product__name",
    ".product-item-name", ".proloop-title", "h2", "h3", "h4",
)


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def fold(value):
    value = unicodedata.normalize("NFKD", clean(value).lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def is_laptop(value):
    value = fold(value)
    return (
        len(value) > 4
        and any(term in value for term in LAPTOP_TERMS)
        and not any(term in value for term in EXCLUDE_TERMS)
    )


def canonical_url(url, base=""):
    url = urljoin(base, clean(url))
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def deduplicate(rows):
    output, seen = [], set()
    for row in rows:
        url = canonical_url(row.get("url", ""))
        name = clean(row.get("name", ""))
        if url and name and url not in seen:
            seen.add(url)
            output.append({"name": name, "url": url})
    return output


def build_session():
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


def fetch(session, url, json_mode=False, timeout=35):
    try:
        response = session.get(
            url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "vi-VN,vi;q=0.9",
                "Accept": "application/json,*/*" if json_mode else
                          "text/html,application/xhtml+xml,*/*",
            },
            timeout=timeout,
            allow_redirects=True,
        )
        print(f" GET {response.status_code} {len(response.content)} {url}", flush=True)
        return response if response.status_code == 200 else None
    except Exception as exc:
        print(f" GET ERROR {url} {exc}", flush=True)
        return None


def shopify_collection(base, collections, label):
    session = build_session()
    output, seen = [], set()

    for collection in collections:
        for page in range(1, 201):
            url = (
                f"{base}/collections/{collection}/products.json"
                f"?limit=250&page={page}"
            )
            response = fetch(session, url, json_mode=True)
            if not response:
                break
            try:
                items = (response.json() or {}).get("products") or []
            except Exception:
                items = []
            if not items:
                break

            new_count = 0
            for product in items:
                name = clean(product.get("title") or product.get("name"))
                handle = clean(product.get("handle"))
                product_url = canonical_url(
                    product.get("url") or (f"/products/{handle}" if handle else ""),
                    base,
                )
                key = str(product.get("id") or product_url)
                if (
                    name and product_url and key not in seen
                    and is_laptop(name)
                ):
                    seen.add(key)
                    output.append({"name": name, "url": product_url})
                    new_count += 1

            print(
                f"[{label}] JSON {collection} page={page} "
                f"received={len(items)} new={new_count} total={len(output)}",
                flush=True,
            )
            if new_count == 0:
                break
            time.sleep(DELAY * 0.25)

    return deduplicate(output)


def name_from_card(card, anchor):
    if getattr(card, "select_one", None):
        for selector in NAME_SELECTORS:
            node = card.select_one(selector)
            if node:
                name = clean(node.get_text(" "))
                if name:
                    return name
    return clean(
        anchor.get("title")
        or anchor.get("aria-label")
        or anchor.get_text(" ")
    )


def extract_products(document, base, pattern=None, loose=False):
    soup = BeautifulSoup(document, "lxml")
    output = []
    cards = []

    for selector in CARD_SELECTORS:
        cards.extend(soup.select(selector))
    if not cards:
        cards = soup.select("a[href]")

    for card in cards:
        anchors = [card] if getattr(card, "name", None) == "a" else card.select("a[href]")
        for anchor in anchors:
            url = canonical_url(anchor.get("href"), base)
            if not url or (pattern and not re.search(pattern, url, re.I)):
                continue
            name = name_from_card(card, anchor)
            if is_laptop(name):
                output.append({"name": name, "url": url})
                break

    if loose:
        for anchor in soup.select("a[href]"):
            url = canonical_url(anchor.get("href"), base)
            name = clean(
                anchor.get("title")
                or anchor.get("aria-label")
                or anchor.get_text(" ")
            )
            if (
                url
                and (not pattern or re.search(pattern, url, re.I))
                and is_laptop(name)
            ):
                output.append({"name": name, "url": url})

    return deduplicate(output)


def paged(
    label,
    base,
    starts,
    pattern,
    params=("page",),
    loose=False,
    max_pages=100,
):
    session = build_session()
    output, seen = [], set()

    for start in starts:
        repeated_pages = 0

        for page_number in range(1, max_pages + 1):
            page_urls = [start] if page_number == 1 else [
                f"{start}{'&' if '?' in start else '?'}{param}={page_number}"
                for param in params
            ]
            best_rows = []

            for page_url in page_urls:
                response = fetch(session, page_url)
                if not response:
                    continue
                rows = extract_products(response.text, base, pattern, loose)
                if len(rows) > len(best_rows):
                    best_rows = rows

            fresh = [row for row in best_rows if row["url"] not in seen]
            for row in fresh:
                seen.add(row["url"])
                output.append(row)

            print(
                f"[{label}] page={page_number} found={len(best_rows)} "
                f"new={len(fresh)} total={len(output)}",
                flush=True,
            )

            if not best_rows:
                break
            if not fresh:
                repeated_pages += 1
                if repeated_pages >= 2:
                    break
            else:
                repeated_pages = 0
            time.sleep(DELAY)

    return deduplicate(output)


def path_paged(label, base, start_path, pattern, loose=False, max_pages=100):
    """Crawl sites whose pagination is /category/2/, /category/3/, etc."""
    session = build_session()
    output, seen = [], set()
    repeated_pages = 0

    for page_number in range(1, max_pages + 1):
        if page_number == 1:
            page_url = f"{base}{start_path}"
        else:
            page_url = f"{base}{start_path.rstrip('/')}/{page_number}/"

        response = fetch(session, page_url)
        if not response:
            break

        rows = extract_products(response.text, base, pattern, loose)
        fresh = [row for row in rows if row["url"] not in seen]
        for row in fresh:
            seen.add(row["url"])
            output.append(row)

        print(
            f"[{label}] page={page_number} found={len(rows)} "
            f"new={len(fresh)} total={len(output)}",
            flush=True,
        )

        if not rows:
            break
        if not fresh:
            repeated_pages += 1
            if repeated_pages >= 2:
                break
        else:
            repeated_pages = 0
        time.sleep(DELAY)

    return deduplicate(output)


async def rendered(label, base, starts, pattern, loose=False, max_rounds=80):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    output, seen = [], set()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            locale="vi-VN",
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
                print(f"[{label}] NAV {exc}", flush=True)
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

                print(
                    f"[{label}] round={round_number} visible={len(rows)} "
                    f"new={len(fresh)} total={len(output)}",
                    flush=True,
                )
                stale = stale + 1 if not fresh else 0

                button = None
                for selector in (
                    "a.btn-show-more", "button.btn-show-more",
                    ".button__show-more-product", "[class*='show-more']",
                    "text=/Xem thêm|Tải thêm|Hiển thị thêm/i",
                ):
                    try:
                        locator = page.locator(selector).first
                        if await locator.is_visible(timeout=400):
                            button = locator
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


# Site-specific crawlers ------------------------------------------------------

def crawl_gearvn(no_playwright=False):
    """GEARVN v3.3: collection pagination first, no products.json calls."""
    base = "https://gearvn.com"
    starts = [
        f"{base}/collections/laptop",
        f"{base}/collections/laptop-gaming-ban-chay",
    ]
    pattern = r"gearvn\.com/products/[a-z0-9-]+$"

    rows = paged(
        label="GEARVN",
        base=base,
        starts=starts,
        pattern=pattern,
        params=("page",),
        loose=True,
        max_pages=50,
    )
    if rows:
        return rows

    if not no_playwright:
        return asyncio.run(
            rendered(
                label="GEARVN",
                base=base,
                starts=starts,
                pattern=pattern,
                loose=True,
                max_rounds=80,
            )
        )
    return []


def crawl_laptopnew(_no_playwright=False):
    base = "https://laptopnew.vn"
    rows = shopify_collection(
        base,
        ["laptop-gaming", "laptop-van-phong", "laptop"],
        "LAPTOPNEW",
    )
    return rows or paged(
        "LAPTOPNEW", base,
        [f"{base}/collections/laptop-gaming", f"{base}/collections/laptop-van-phong"],
        r"laptopnew\.vn/(?:products/)?[a-z0-9-]+$",
        loose=True,
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
        rendered("LAPTOP88", base, starts, pattern, loose=True)
    )
    return rows or paged(
        "LAPTOP88", base, starts, pattern,
        params=("page",), loose=True,
    )


def crawl_cellphones(no_playwright=False):
    base = "https://cellphones.com.vn"
    starts = [f"{base}/laptop.html"]
    pattern = r"cellphones\.com\.vn/(?!laptop(?:/|\.html$))[a-z0-9-/]+\.html$"
    rows = [] if no_playwright else asyncio.run(
        rendered("CELLPHONES", base, starts, pattern, loose=True)
    )
    return rows or paged("CELLPHONES", base, starts, pattern, loose=True)


def crawl_hacom(_no_playwright=False):
    # HACOM uses path pagination: /laptop/2/, /laptop/3/, ...
    base = "https://hacom.vn"
    pattern = r"hacom\.vn/laptop-(?!gaming-do-hoa$)[a-z0-9-]+$"
    return path_paged(
        label="HACOM",
        base=base,
        start_path="/laptop",
        pattern=pattern,
        loose=True,
        max_pages=60,
    )


CRAWLERS = {
    "gearvn": crawl_gearvn,
    "xgear": lambda _n: shopify_collection("https://xgear.net", ["laptop"], "XGEAR"),
    "tinhocngoisao": lambda _n: shopify_collection(
        "https://tinhocngoisao.com", ["laptop"], "TINHOCNGOISAO"
    ),
    "hangchinhhieu": lambda _n: shopify_collection(
        "https://hangchinhhieu.vn",
        ["laptop", "laptop-gaming-do-hoa-studio"],
        "HANGCHINHHIEU",
    ),
    "laptopnew": crawl_laptopnew,
    "memoryzone": lambda _n: shopify_collection(
        "https://memoryzone.com.vn", ["laptop"], "MEMORYZONE"
    ) or paged(
        "MEMORYZONE", "https://memoryzone.com.vn",
        ["https://memoryzone.com.vn/laptop"],
        r"memoryzone\.com\.vn/(?:products/)?[a-z0-9-]+$",
        loose=True,
    ),
    "cellphones": crawl_cellphones,
    "hoanghamobile": lambda _n: paged(
        "HOANGHAMOBILE", "https://hoanghamobile.com",
        ["https://hoanghamobile.com/laptop"],
        r"hoanghamobile\.com/laptop/(?!phan-loai-san-pham/)[a-z0-9-]+$",
        params=("p",), loose=True,
    ),
    "laptopworld": crawl_laptopworld,
    "laptop88": crawl_laptop88,
    "anphatpc": lambda _n: paged(
        "ANPHATPC", "https://www.anphatpc.com.vn",
        [
            "https://www.anphatpc.com.vn/may-tinh-xach-tay-laptop.html",
            "https://www.anphatpc.com.vn/gaming-laptop.html",
            "https://www.anphatpc.com.vn/laptop-do-hoa.html",
        ],
        r"anphatpc\.com\.vn/(?!may-tinh-xach-tay-laptop\.html$|gaming-laptop\.html$|laptop-do-hoa\.html$)[a-z0-9-/]+\.html$",
        params=("page",),
        loose=True,
        max_pages=100,
    ),
    "hacom": crawl_hacom,
}


def save_csv(rows, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["site", "name", "url"])
        writer.writeheader()
        writer.writerows(rows)


def main():
    global DELAY
    parser = argparse.ArgumentParser(description="Vietnam Laptop Crawler v3.4")
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
        except Exception as exc:
            print(f"[{site}] FATAL {exc}", flush=True)
            rows = []

        all_rows.extend({"site": site, **row} for row in rows)
        save_csv(all_rows, args.output)
        print(f"[{site}] SAVED {len(rows)}", flush=True)

    if args.json:
        json_path = os.path.splitext(args.output)[0] + ".json"
        with open(json_path, "w", encoding="utf-8") as file:
            json.dump(all_rows, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
