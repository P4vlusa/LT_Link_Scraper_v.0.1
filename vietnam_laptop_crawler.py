#!/usr/bin/env python3
import argparse, asyncio, csv, html, json, os, random, re, time, unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA=["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"]
DELAY=1.0
TERMS=("laptop","macbook","notebook","chromebook","vivobook","zenbook","expertbook","ideapad","thinkpad","thinkbook","legion"," loq","aspire","nitro","swift","predator","pavilion","victus","elitebook","probook","omnibook","inspiron","latitude","vostro","xps","alienware","msi ","katana","cyborg","stealth","modern","prestige","aorus","lg gram","surface")
SKIP=("balo","tui laptop","de laptop","sac laptop","adapter","pin laptop","ram laptop","ban phim","chuot","tai nghe","man hinh","bao hanh","phu kien","linh kien")
CARDS=(".product-item",".product-card",".product-info-container",".p-item",".item-product",".product-loop",".proloop","li.product","article.product","[data-product-id]")
NAMES=(".product-name",".product__name",".product-item-name",".p-name",".hover_name",".proloop-title","h2","h3","h4")

def clean(x): return re.sub(r"\s+"," ",html.unescape(str(x or ""))).strip()
def fold(x): return "".join(c for c in unicodedata.normalize("NFKD",clean(x).lower()) if not unicodedata.combining(c))
def is_laptop(x):
    x=fold(x)
    return not any(k in x for k in SKIP) and any(k in x for k in TERMS)
def canon(u,base=""):
    u=urljoin(base,clean(u)); p=urlsplit(u)
    if p.scheme not in ("http","https") or not p.netloc:return ""
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/") or "/","",""))
def dedup(rows):
    out=[]; seen=set()
    for r in rows:
        u=canon(r.get("url","")); n=clean(r.get("name",""))
        if u and len(n)>4 and u not in seen: seen.add(u); out.append({"name":n,"url":u})
    return out

def session():
    s=requests.Session(); retry=Retry(total=4,backoff_factor=1,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset(["GET"]))
    s.mount("https://",HTTPAdapter(max_retries=retry)); return s
def get(s,u):
    try:
        r=s.get(u,headers={"User-Agent":random.choice(UA),"Accept-Language":"vi-VN,vi;q=0.9"},timeout=35)
        print(" GET",r.status_code,len(r.content),u,flush=True)
        return r if r.status_code==200 else None
    except Exception as e: print(" GET ERROR",u,e,flush=True); return None

def extract(doc,base,pattern):
    soup=BeautifulSoup(doc,"lxml"); cards=[]
    for q in CARDS: cards += soup.select(q)
    if not cards: cards=soup.select("a[href]")
    out=[]
    for card in cards:
        anchors=[card] if getattr(card,"name",None)=="a" else card.select("a[href]")
        a=u=None
        for x in anchors:
            z=canon(x.get("href"),base)
            if z and re.search(pattern,z,re.I): a,u=x,z; break
        if not a: continue
        name=""
        if getattr(card,"name",None)!="a":
            for q in NAMES:
                x=card.select_one(q)
                if x and clean(x.get_text(" ")): name=clean(x.get_text(" ")); break
        name=name or clean(a.get("title") or a.get("aria-label") or a.get_text(" "))
        if is_laptop(name): out.append({"name":name,"url":u})
    return dedup(out)

async def browser_crawl(label,starts,base,pattern,max_clicks=80):
    try: from playwright.async_api import async_playwright
    except ImportError:return []
    out=[]; seen=set()
    async with async_playwright() as pw:
        b=await pw.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"])
        ctx=await b.new_context(user_agent=random.choice(UA),locale="vi-VN",viewport={"width":1440,"height":1000})
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page=await ctx.new_page()
        for start in starts:
            try: await page.goto(start,wait_until="domcontentloaded",timeout=50000); await page.wait_for_timeout(3000)
            except Exception as e: print(label,"NAV",e); continue
            stale=0
            for i in range(max_clicks+1):
                await page.evaluate("window.scrollTo(0,document.body.scrollHeight)"); await page.wait_for_timeout(1200)
                rows=extract(await page.content(),base,pattern); fresh=[r for r in rows if r["url"] not in seen]
                for r in fresh: seen.add(r["url"]); out.append(r)
                print(f"[{label}] round={i} visible={len(rows)} new={len(fresh)} total={len(out)}",flush=True)
                stale=stale+1 if not fresh else 0
                if stale>=2: break
                btn=None
                for q in ("a.btn-show-more","button.btn-show-more",".button__show-more-product","[class*='show-more']","text=/Xem thêm|Tải thêm|Hiển thị thêm/i"):
                    try:
                        x=page.locator(q).first
                        if await x.is_visible(timeout=400): btn=x; break
                    except: pass
                if not btn: break
                try: await btn.scroll_into_view_if_needed(); await btn.click(timeout=5000); await page.wait_for_timeout(2500)
                except: break
        await b.close()
    return dedup(out)

def static_crawl(label,starts,base,pattern,max_pages=100):
    s=session(); out=[]; seen=set()
    for start in starts:
        for pg in range(1,max_pages+1):
            urls=[start] if pg==1 else [f"{start}{'&' if '?' in start else '?'}page={pg}",f"{start}{'&' if '?' in start else '?'}p={pg}"]
            best=[]
            for u in urls:
                r=get(s,u)
                if r:
                    x=extract(r.text,base,pattern)
                    if len(x)>len(best):best=x
            fresh=[r for r in best if r["url"] not in seen]
            for r in fresh:seen.add(r["url"]);out.append(r)
            print(f"[{label}] page={pg} found={len(best)} new={len(fresh)} total={len(out)}",flush=True)
            if not best or not fresh:break
            time.sleep(DELAY)
    return dedup(out)

CONFIG={
"gearvn":("https://gearvn.com",["https://gearvn.com/collections/laptop","https://gearvn.com/collections/laptop-gaming-ban-chay"],r"gearvn\.com/products/[a-z0-9-]+$",1),
"xgear":("https://xgear.net",["https://xgear.net/collections/laptop"],r"xgear\.net/products/[a-z0-9-]+$",0),
"tinhocngoisao":("https://tinhocngoisao.com",["https://tinhocngoisao.com/collections/laptop"],r"tinhocngoisao\.com/products/[a-z0-9-]+$",0),
"hangchinhhieu":("https://hangchinhhieu.vn",["https://hangchinhhieu.vn/collections/laptop"],r"hangchinhhieu\.vn/products/[a-z0-9-]+$",0),
"laptopnew":("https://laptopnew.vn",["https://laptopnew.vn/collections/laptop-gaming","https://laptopnew.vn/collections/laptop-van-phong"],r"laptopnew\.vn/products/[a-z0-9-]+$",0),
"memoryzone":("https://memoryzone.com.vn",["https://memoryzone.com.vn/laptop"],r"memoryzone\.com\.vn/(?:products/)?[a-z0-9-]+$",0),
"cellphones":("https://cellphones.com.vn",["https://cellphones.com.vn/laptop.html"],r"cellphones\.com\.vn/(?!laptop(?:/|\.html$))[a-z0-9-/]+\.html$",1),
"hoanghamobile":("https://hoanghamobile.com",["https://hoanghamobile.com/laptop"],r"hoanghamobile\.com/laptop/(?!phan-loai-san-pham/)[a-z0-9-]+$",0),
"laptopworld":("https://laptopworld.vn",["https://laptopworld.vn/laptop-van-phong.html","https://laptopworld.vn/laptop-games-do-hoa.html"],r"laptopworld\.vn/(?!laptop-(?:van-phong|games-do-hoa)\.html$)(?:[a-z0-9-]+/)*[a-z0-9-]+\.html$",0),
"laptop88":("https://laptop88.vn",["https://laptop88.vn/may-tinh-xach-tay.html"],r"laptop88\.vn/(?!may-tinh-xach-tay\.html$|thuong-hieu/|nhu-cau/|tin-tuc/)(?:new-100-)?[a-z0-9][a-z0-9-]+(?:\.html)?$",1),
"anphatpc":("https://www.anphatpc.com.vn",["https://www.anphatpc.com.vn/laptop.html"],r"anphatpc\.com\.vn/(?!laptop\.html$)[a-z0-9-/]+\.html$",0),
"hacom":("https://hacom.vn",["https://hacom.vn/laptop","https://hacom.vn/laptop-gaming-do-hoa"],r"hacom\.vn/laptop-(?!gaming-do-hoa$)[a-z0-9-]+$",1),
"phucanh":("https://www.phucanh.vn",["https://www.phucanh.vn/may-tinh-xach-tay-laptop.html"],r"phucanh\.vn/(?!may-tinh-xach-tay-laptop(?:-[a-z]+)?\.html$)[a-z0-9-]+\.html$",1),
"ankhang":("https://www.ankhang.vn",["https://www.ankhang.vn/laptop.html"],r"ankhang\.vn/(?!laptop(?:-[a-z0-9-]+)?\.html$)[a-z0-9-]+\.html$",1),
"fptshop":("https://fptshop.com.vn",["https://fptshop.com.vn/may-tinh-xach-tay"],r"fptshop\.com\.vn/may-tinh-xach-tay/(?!asus$|acer$|hp$|dell$|lenovo$|msi$|ai$)[a-z0-9-]+$",1),
"thegioididong":("https://www.thegioididong.com",["https://www.thegioididong.com/laptop"],r"thegioididong\.com/laptop/[a-z0-9][a-z0-9-]+$",1),
"phongvu":("https://phongvu.vn",["https://phongvu.vn/c/laptop"],r"phongvu\.vn/(?:p/)?(?!c/)[a-z0-9][a-z0-9-]+$",1),
}

def run_site(name,no_pw=False):
    base,starts,pattern,use_pw=CONFIG[name]
    rows=[] if no_pw or not use_pw else asyncio.run(browser_crawl(name.upper(),starts,base,pattern))
    return rows or static_crawl(name.upper(),starts,base,pattern)
def main():
    global DELAY
    ap=argparse.ArgumentParser(); ap.add_argument("--sites",nargs="+",choices=sorted(CONFIG)); ap.add_argument("--output",default="vietnam_laptops.csv"); ap.add_argument("--delay",type=float,default=1); ap.add_argument("--no-playwright",action="store_true"); ap.add_argument("--json",action="store_true"); a=ap.parse_args(); DELAY=max(.1,a.delay)
    selected=a.sites or list(CONFIG); allrows=[]
    os.makedirs(os.path.dirname(os.path.abspath(a.output)),exist_ok=True)
    for site in selected:
        try: rows=run_site(site,a.no_playwright)
        except Exception as e: print(f"[{site}] FATAL {e}",flush=True); rows=[]
        allrows += [{"site":site,**r} for r in rows]
        with open(a.output,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f,fieldnames=["site","name","url"]);w.writeheader();w.writerows(allrows)
        print(f"[{site}] SAVED {len(rows)}",flush=True)
    if a.json:
        with open(os.path.splitext(a.output)[0]+".json","w",encoding="utf-8") as f:json.dump(allrows,f,ensure_ascii=False,indent=2)
if __name__=="__main__":main()
