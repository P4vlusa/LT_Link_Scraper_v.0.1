#!/usr/bin/env python3
import argparse, asyncio, csv, html, json, os, random, re, time, unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DELAY=1.0
UA=["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36","Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"]
LAPTOP=("laptop","macbook","notebook","chromebook","vivobook","zenbook","expertbook","ideapad","thinkpad","thinkbook","legion"," loq","aspire","nitro","swift","predator","pavilion","victus","elitebook","probook","omnibook","inspiron","latitude","vostro","xps","alienware","katana","cyborg","stealth","modern","prestige","aorus","lg gram","surface")
EXCLUDE=("balo","tui laptop","de laptop","gia do laptop","sac laptop","adapter","pin laptop","ram laptop","ban phim","chuot","tai nghe","man hinh","bao hanh","phu kien","linh kien","ve sinh laptop")

def clean(x): return re.sub(r"\s+"," ",html.unescape(str(x or ""))).strip()
def fold(x): return "".join(c for c in unicodedata.normalize("NFKD",clean(x).lower()) if not unicodedata.combining(c))
def is_laptop(x):
    x=fold(x)
    return len(x)>4 and any(k in x for k in LAPTOP) and not any(k in x for k in EXCLUDE)
def canon(u,base=""):
    u=urljoin(base,clean(u)); p=urlsplit(u)
    if p.scheme not in ("http","https") or not p.netloc:return ""
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/") or "/","",""))
def dedup(rows):
    out=[]; seen=set()
    for r in rows:
        u=canon(r.get("url","")); n=clean(r.get("name",""))
        if u and n and u not in seen: seen.add(u); out.append({"name":n,"url":u})
    return out

def session():
    s=requests.Session(); retry=Retry(total=4,connect=4,read=4,status=4,backoff_factor=1,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset(["GET"]),respect_retry_after_header=True)
    a=HTTPAdapter(max_retries=retry,pool_connections=10,pool_maxsize=10); s.mount("https://",a); s.mount("http://",a); return s
def get(s,u,json_mode=False,timeout=35):
    try:
        r=s.get(u,headers={"User-Agent":random.choice(UA),"Accept-Language":"vi-VN,vi;q=0.9","Accept":"application/json,*/*" if json_mode else "text/html,application/xhtml+xml,*/*"},timeout=timeout,allow_redirects=True)
        print(f" GET {r.status_code} {len(r.content)} {u}",flush=True)
        return r if r.status_code==200 else None
    except Exception as e: print(" GET ERROR",u,e,flush=True); return None

def shopify(base,collections,label):
    s=session(); out=[]; seen=set()
    for col in collections:
        for page in range(1,201):
            urls=[f"{base}/collections/{col}/products.json?limit=250&page={page}",f"{base}/products.json?limit=250&page={page}"] if page==1 else [f"{base}/collections/{col}/products.json?limit=250&page={page}"]
            items=[]
            for u in urls:
                r=get(s,u,True)
                if not r: continue
                try: items=(r.json() or {}).get("products") or []
                except Exception: items=[]
                if items: break
            if not items: break
            new=0
            for p in items:
                name=clean(p.get("title") or p.get("name")); handle=clean(p.get("handle")); u=canon(p.get("url") or (f"/products/{handle}" if handle else ""),base)
                key=str(p.get("id") or u)
                if name and u and key not in seen and is_laptop(name): seen.add(key); out.append({"name":name,"url":u}); new+=1
            print(f"[{label}] JSON {col} page={page} received={len(items)} new={new} total={len(out)}",flush=True)
            if new==0: break
            time.sleep(DELAY*.25)
    return dedup(out)

def name_from(card,a):
    for q in (".hover_name",".p-name",".product-name",".product__name",".product-item-name",".proloop-title","h2","h3","h4"):
        x=card.select_one(q) if getattr(card,"select_one",None) else None
        if x and clean(x.get_text(" ")): return clean(x.get_text(" "))
    return clean(a.get("title") or a.get("aria-label") or a.get_text(" "))
def generic_extract(doc,base,pattern=None,loose=False):
    soup=BeautifulSoup(doc,"lxml"); out=[]
    cards=[]
    for q in (".p-item",".product-item",".product-card",".product-info-container",".item-product",".product-loop",".proloop","li.product","article.product","[data-product-id]"): cards+=soup.select(q)
    if not cards: cards=soup.select("a[href]")
    for card in cards:
        anchors=[card] if getattr(card,"name",None)=="a" else card.select("a[href]")
        for a in anchors:
            u=canon(a.get("href"),base)
            if not u or (pattern and not re.search(pattern,u,re.I)): continue
            n=name_from(card,a)
            if is_laptop(n): out.append({"name":n,"url":u}); break
    if loose:
        for a in soup.select("a[href]"):
            u=canon(a.get("href"),base); n=clean(a.get("title") or a.get("aria-label") or a.get_text(" "))
            if u and (not pattern or re.search(pattern,u,re.I)) and is_laptop(n): out.append({"name":n,"url":u})
    return dedup(out)

def paged(label,base,starts,pattern,params=("page",),loose=False,max_pages=100):
    s=session(); out=[]; seen=set()
    for start in starts:
        for pg in range(1,max_pages+1):
            urls=[start] if pg==1 else [f"{start}{'&' if '?' in start else '?'}{p}={pg}" for p in params]
            best=[]
            for u in urls:
                r=get(s,u)
                if r:
                    rows=generic_extract(r.text,base,pattern,loose)
                    if len(rows)>len(best):best=rows
            fresh=[r for r in best if r["url"] not in seen]
            for r in fresh: seen.add(r["url"]); out.append(r)
            print(f"[{label}] page={pg} found={len(best)} new={len(fresh)} total={len(out)}",flush=True)
            if not best or not fresh: break
            time.sleep(DELAY)
    return dedup(out)

async def rendered(label,base,starts,pattern,loose=False,max_rounds=80):
    try: from playwright.async_api import async_playwright
    except ImportError:return []
    out=[]; seen=set()
    async with async_playwright() as pw:
        b=await pw.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"])
        ctx=await b.new_context(user_agent=random.choice(UA),locale="vi-VN",viewport={"width":1440,"height":1000}); await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page=await ctx.new_page()
        for start in starts:
            try: await page.goto(start,wait_until="domcontentloaded",timeout=50000); await page.wait_for_timeout(3000)
            except Exception as e: print(label,"NAV",e); continue
            stale=0
            for i in range(max_rounds):
                await page.evaluate("window.scrollTo(0,document.body.scrollHeight)"); await page.wait_for_timeout(1300)
                rows=generic_extract(await page.content(),base,pattern,loose); fresh=[r for r in rows if r["url"] not in seen]
                for r in fresh:seen.add(r["url"]);out.append(r)
                print(f"[{label}] round={i} visible={len(rows)} new={len(fresh)} total={len(out)}",flush=True)
                stale=stale+1 if not fresh else 0
                btn=None
                for q in ("a.btn-show-more","button.btn-show-more",".button__show-more-product","[class*='show-more']","text=/Xem thêm|Tải thêm|Hiển thị thêm/i"):
                    try:
                        x=page.locator(q).first
                        if await x.is_visible(timeout=400):btn=x;break
                    except:pass
                if not btn or stale>=2:break
                try:await btn.scroll_into_view_if_needed();await btn.click(timeout=5000);await page.wait_for_timeout(2500)
                except:break
        await b.close()
    return dedup(out)

def gearvn(no_pw):
    base="https://gearvn.com"; rows=shopify(base,["laptop","laptop-gaming-ban-chay"],"GEARVN")
    if rows:return rows
    starts=[base+"/collections/laptop",base+"/collections/laptop-gaming-ban-chay"]
    return ([] if no_pw else asyncio.run(rendered("GEARVN",base,starts,r"gearvn\.com/products/[a-z0-9-]+$",True))) or paged("GEARVN",base,starts,r"gearvn\.com/products/[a-z0-9-]+$",loose=True)
def laptopnew(no_pw):
    base="https://laptopnew.vn"; rows=shopify(base,["laptop-gaming","laptop-van-phong","laptop"],"LAPTOPNEW")
    return rows or paged("LAPTOPNEW",base,[base+"/collections/laptop-gaming",base+"/collections/laptop-van-phong"],r"laptopnew\.vn/(?:products/)?[a-z0-9-]+$",loose=True)
def laptopworld(no_pw):
    b="https://laptopworld.vn"; return paged("LAPTOPWORLD",b,[b+"/laptop-van-phong.html",b+"/laptop-games-do-hoa.html"],r"laptopworld\.vn/(?!laptop-(?:van-phong|games-do-hoa)\.html$)(?:[a-z0-9-]+/)*[a-z0-9-]+(?:\.html)?$",("page",),True)
def laptop88(no_pw):
    b="https://laptop88.vn"; starts=[b+"/may-tinh-xach-tay.html"]; pat=r"laptop88\.vn/(?!may-tinh-xach-tay\.html$|tin-tuc/|khuyen-mai/|thuong-hieu/|nhu-cau/|phu-kien/)[a-z0-9][a-z0-9-/]+(?:\.html)?$"
    return ([] if no_pw else asyncio.run(rendered("LAPTOP88",b,starts,pat,True))) or paged("LAPTOP88",b,starts,pat,("page",),True)

CFG={
"gearvn":gearvn,"laptopnew":laptopnew,"laptopworld":laptopworld,"laptop88":laptop88,
"xgear":lambda n: shopify("https://xgear.net",["laptop"],"XGEAR"),
"tinhocngoisao":lambda n: shopify("https://tinhocngoisao.com",["laptop"],"TINHOCNGOISAO"),
"hangchinhhieu":lambda n: shopify("https://hangchinhhieu.vn",["laptop","laptop-gaming-do-hoa-studio"],"HANGCHINHHIEU"),
"memoryzone":lambda n: shopify("https://memoryzone.com.vn",["laptop"],"MEMORYZONE") or paged("MEMORYZONE","https://memoryzone.com.vn",["https://memoryzone.com.vn/laptop"],r"memoryzone\.com\.vn/(?:products/)?[a-z0-9-]+$",loose=True),
"cellphones":lambda n: ([] if n else asyncio.run(rendered("CELLPHONES","https://cellphones.com.vn",["https://cellphones.com.vn/laptop.html"],r"cellphones\.com\.vn/(?!laptop(?:/|\.html$))[a-z0-9-/]+\.html$",True))) or paged("CELLPHONES","https://cellphones.com.vn",["https://cellphones.com.vn/laptop.html"],r"cellphones\.com\.vn/(?!laptop(?:/|\.html$))[a-z0-9-/]+\.html$",loose=True),
"hoanghamobile":lambda n: paged("HOANGHAMOBILE","https://hoanghamobile.com",["https://hoanghamobile.com/laptop"],r"hoanghamobile\.com/laptop/(?!phan-loai-san-pham/)[a-z0-9-]+$",("p",),True),
"anphatpc":lambda n: paged("ANPHATPC","https://www.anphatpc.com.vn",["https://www.anphatpc.com.vn/laptop.html"],r"anphatpc\.com\.vn/(?!laptop\.html$)[a-z0-9-/]+\.html$",loose=True),
"hacom":lambda n: ([] if n else asyncio.run(rendered("HACOM","https://hacom.vn",["https://hacom.vn/laptop","https://hacom.vn/laptop-gaming-do-hoa"],r"hacom\.vn/laptop-(?!gaming-do-hoa$)[a-z0-9-]+$",True))) or paged("HACOM","https://hacom.vn",["https://hacom.vn/laptop"],r"hacom\.vn/laptop-(?!gaming-do-hoa$)[a-z0-9-]+$",loose=True),
}

def save(rows,path):
    os.makedirs(os.path.dirname(os.path.abspath(path)),exist_ok=True)
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=["site","name","url"]);w.writeheader();w.writerows(rows)
def main():
    global DELAY
    p=argparse.ArgumentParser();p.add_argument("--sites",nargs="+",choices=sorted(CFG));p.add_argument("--output",default="vietnam_laptops.csv");p.add_argument("--delay",type=float,default=1);p.add_argument("--no-playwright",action="store_true");p.add_argument("--json",action="store_true");a=p.parse_args();DELAY=max(.1,a.delay)
    allrows=[]
    for site in a.sites or list(CFG):
        try:rows=dedup(CFG[site](a.no_playwright))
        except Exception as e:print(f"[{site}] FATAL {e}",flush=True);rows=[]
        allrows.extend({"site":site,**r} for r in rows);save(allrows,a.output);print(f"[{site}] SAVED {len(rows)}",flush=True)
    if a.json:
        with open(os.path.splitext(a.output)[0]+".json","w",encoding="utf-8") as f:json.dump(allrows,f,ensure_ascii=False,indent=2)
if __name__=="__main__":main()
