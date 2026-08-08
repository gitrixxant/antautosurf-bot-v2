#!/usr/bin/env python3
# bot_worker.py - BOT MULTI-ACCOUNT IBRIDO INTELLIGENTE (VERSIONE FINALE)

import os
import time
import sys
import json
import re
import requests
from playwright.sync_api import sync_playwright
from datetime import datetime
import imagehash
from PIL import Image
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIGURAZIONE
# ============================================================

HEADLESS = True
ACCOUNTS_FILE = "accounts.json"
PROXIES_FILE = "proxy_pool.json"

# ============================================================
# CARICA ACCOUNT E PROXY
# ============================================================

def carica_accounts():
    try:
        with open(ACCOUNTS_FILE, "r") as f:
            return json.load(f)
    except:
        print(f"❌ File {ACCOUNTS_FILE} non trovato!")
        return []

def carica_proxy_pool():
    try:
        with open(PROXIES_FILE, "r") as f:
            data = json.load(f)
            return data.get("proxies", [])
    except:
        print(f"❌ File {PROXIES_FILE} non trovato!")
        return []

def parse_proxy(proxy_str):
    try:
        auth, host = proxy_str.split('@')
        user, password = auth.split(':')
        return {
            "server": f"http://{host}",
            "username": user,
            "password": password
        }
    except:
        return None

# ============================================================
# PROXY POOL - TRACCIA PROXY USATI (BLOCCO 24 ORE)
# ============================================================

PROXY_POOL_USATI = {}  # { "ip:port": timestamp_uso }

def proxy_e_bloccato(proxy):
    key = f"{proxy['host']}:{proxy['port']}"
    if key in PROXY_POOL_USATI:
        tempo_trascorso = time.time() - PROXY_POOL_USATI[key]
        if tempo_trascorso < 86400:
            return True
        else:
            del PROXY_POOL_USATI[key]
    return False

def segna_proxy_usato(proxy):
    if isinstance(proxy, dict):
        key = f"{proxy['host']}:{proxy['port']}"
    else:
        try:
            parts = proxy.split('@')
            if len(parts) > 1:
                host_port = parts[1]
            else:
                host_port = proxy
            key = host_port
        except:
            key = proxy
    PROXY_POOL_USATI[key] = time.time()
    print(f"📌 Proxy {key} segnato come usato (bloccato 24h)")

# ============================================================
# PROXY FINDER - DA API PUBBLICHE
# ============================================================

PROXY_SOURCES = [
    "https://free-proxy-list.net/",
    "https://api.proxyscrape.com/?request=displayproxies&proxytype=http",
]

def scarica_proxy_da_url(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            proxies = []
            for line in response.text.splitlines():
                line = line.strip()
                if line and not line.startswith('#') and not line.startswith('<!'):
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) == 2:
                            try:
                                port = int(parts[1])
                                proxies.append({"host": parts[0], "port": port})
                            except:
                                pass
            return proxies
    except:
        pass
    return []

def ottieni_proxy_pubblici():
    all_proxies = []
    for url in PROXY_SOURCES:
        proxies = scarica_proxy_da_url(url)
        if proxies:
            all_proxies.extend(proxies)
    
    unique = []
    seen = set()
    for p in all_proxies:
        key = f"{p['host']}:{p['port']}"
        if key not in seen:
            seen.add(key)
            unique.append(p)
    
    return unique

def verifica_proxy(proxy, timeout=5):
    try:
        host = proxy["host"]
        port = proxy["port"]
        
        proxy_dict = {
            "http": f"http://{host}:{port}",
            "https": f"http://{host}:{port}"
        }
        
        response = requests.get("http://httpbin.org/ip", proxies=proxy_dict, timeout=timeout)
        if response.status_code == 200:
            return proxy, True
    except:
        pass
    return proxy, False

def trova_proxy_pubblico_libero():
    proxy_list = ottieni_proxy_pubblici()
    if not proxy_list:
        return None
    
    for proxy in proxy_list:
        if not proxy_e_bloccato(proxy):
            proxy_ok, ok = verifica_proxy(proxy)
            if ok:
                segna_proxy_usato(proxy_ok)
                return proxy_ok
    
    return None

# ============================================================
# PROXY IBRIDO CON CONTATORE FALLIMENTI (PERSISTENTE)
# ============================================================

FALLIMENTI_PROXY = {}  # { "account": count }

def ottieni_proxy_ibrido(email, proxy_pool, used_proxies):
    """
    Sistema ibrido:
    1. Prova 1 proxy PUBBLICO
    2. Se fallisce → usa proxy PROXYSCRAPE (permanentemente)
    """
    
    fallimenti = FALLIMENTI_PROXY.get(email, 0)
    
    # 🔥 SE HA GIÀ FALLITO 1 VOLTA, USA DIRETTAMENTE PROXYSCRAPE
    if fallimenti >= 1:
        print(f"[{email[:10]}...] ⚠️ Già fallito 1 volta, passo a ProxyScrape...")
        
        if proxy_pool:
            for proxy in proxy_pool:
                if proxy["account"] == email and proxy["proxy"] not in used_proxies:
                    proxy_config = parse_proxy(proxy["proxy"])
                    if proxy_config:
                        print(f"[{email[:10]}...] ✅ Proxy ProxyScrape: {proxy['proxy'].split('@')[1]}")
                        segna_proxy_usato(proxy['proxy'])
                        used_proxies.append(proxy['proxy'])
                        FALLIMENTI_PROXY[email] = 0  # Resetta dopo uso
                        return {
                            "type": "proxyscrape",
                            "proxy": proxy["proxy"],
                            "config": proxy_config,
                            "string": proxy["proxy"]
                        }
        
        print(f"[{email[:10]}...] ❌ Nessun proxy ProxyScrape disponibile!")
        return None
    
    # 1. SE FALLIMENTI < 1 → CERCA 1 PROXY PUBBLICO
    print(f"[{email[:10]}...] 🔍 Cerco proxy pubblico (tentativo 1/1)...")
    proxy_pubblico = trova_proxy_pubblico_libero()
    
    if proxy_pubblico:
        print(f"[{email[:10]}...] ✅ Proxy pubblico: {proxy_pubblico['host']}:{proxy_pubblico['port']}")
        return {
            "type": "public",
            "proxy": proxy_pubblico,
            "config": {"server": f"http://{proxy_pubblico['host']}:{proxy_pubblico['port']}"},
            "string": f"{proxy_pubblico['host']}:{proxy_pubblico['port']}"
        }
    else:
        # Se non trova proxy, incrementa e riprova
        FALLIMENTI_PROXY[email] = 1
        print(f"[{email[:10]}...] ⚠️ Nessun proxy pubblico disponibile, passo a ProxyScrape...")
        return None

# ============================================================
# LOGGING
# ============================================================

def log(email, msg):
    prefix = email[:10] if email else "SISTEMA"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{prefix}...] {msg}", flush=True)

# ============================================================
# CARICA DATABASE PHASH
# ============================================================

def carica_database():
    try:
        with open("hash_phash_db.json", "r") as f:
            return json.load(f)
    except:
        return {}

# ============================================================
# RISOLUZIONE CAPTCHA
# ============================================================

def risolvi_captcha(page, email, phash_db):
    html = page.content()
    cap_match = re.search(r'capimg\.php\?id=(\d+)', html)
    if not cap_match:
        return False
    
    cids = [int(x) for x in re.findall(r'cid=(\d+)', html)]
    cids_unici = list(set(cids))
    
    log(email, f"   📌 CID disponibili: {cids_unici}")
    
    try:
        img_element = page.locator('img[src*="capimg.php"]')
        img_data = img_element.screenshot()
        
        img_pil = Image.open(io.BytesIO(img_data))
        phash = imagehash.phash(img_pil)
        phash_str = str(phash)
        log(email, f"   🔑 PHASH: {phash_str}")
        
        for stored_phash, cid in phash_db.items():
            try:
                diff = imagehash.hex_to_hash(phash_str) - imagehash.hex_to_hash(stored_phash)
                if diff <= 10:
                    page.goto(f"https://antautosurf.com/index.php?cid={cid}")
                    time.sleep(2)
                    log(email, f"   ✅ CAPTCHA RISOLTO! CID: {cid}")
                    return True
            except:
                pass
    except:
        pass
    
    for cid in cids_unici:
        page.goto(f"https://antautosurf.com/index.php?cid={cid}")
        time.sleep(2)
        html_test = page.content()
        if "Please Click Similar" not in html_test:
            phash_db[phash_str] = cid
            with open("hash_phash_db.json", "w") as f:
                json.dump(phash_db, f, indent=2)
            log(email, f"   ✅ CAPTCHA RISOLTO! CID: {cid} (nuovo)")
            return True
    
    log(email, f"   ❌ CAPTCHA NON RISOLTO!")
    return False

# ============================================================
# GESTISCI UN SINGOLO ACCOUNT
# ============================================================

def esegui_account(account_data, proxy_pool):
    email = account_data["email"]
    password = account_data["password"]
    
    log(email, "🚀 Avvio account...")
    
    used_proxies = []
    
    # 🔥 OTTIENI PROXY IBRIDO
    proxy_data = ottieni_proxy_ibrido(email, proxy_pool, used_proxies)
    
    if not proxy_data:
        log(email, "❌ Nessun proxy disponibile!")
        # Incrementa il contatore per evitare loop
        FALLIMENTI_PROXY[email] = FALLIMENTI_PROXY.get(email, 0) + 1
        return
    
    proxy_config = proxy_data["config"]
    proxy_str = proxy_data["string"]
    proxy_type = proxy_data["type"]
    
    log(email, f"🌐 Proxy: {proxy_str} ({proxy_type})")
    
    phash_db = carica_database()
    
    # Traccia se il surf ha avuto successo
    surf_successo = False
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            proxy=proxy_config,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
        )
        
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # LOGIN
            log(email, "📧 Login...")
            page.goto("https://antautosurf.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            
            page.fill('input[name="bitcoinwallet"]', email)
            page.click('input[type="submit"][value*="Enter"]')
            time.sleep(3)
            
            html = page.content()
            
            if "Set Login Password" in html:
                log(email, "📝 Nuovo account! Registro...")
                page.fill('input[name="password"]', password)
                page.fill('input[name="passwordb"]', password)
                match = re.search(r'name="confirm2" value="(\d+)"', html)
                if match:
                    confirm2 = match.group(1)
                    page.goto(f"https://antautosurf.com/index.php?password={password}&passwordb={password}&confirm2={confirm2}")
                    time.sleep(3)
                    log(email, "   ✅ Password impostata!")
                    html = page.content()
            
            if "Please enter Password" in html:
                log(email, "🔑 Login con password...")
                page.fill('input[name="password"]', password)
                page.click('input[value="Enter"]')
                time.sleep(3)
            
            log(email, "✅ Login completato!")
            
            # DASHBOARD
            log(email, "📊 Dashboard...")
            page.goto(f"https://antautosurf.com/index.php?bitcoinwallet={email}&ref=", wait_until="networkidle", timeout=30000)
            time.sleep(3)
            html = page.content()
            
            if "Please Click Similar" in html:
                log(email, "⚠️ CAPTCHA RILEVATO!")
                if not risolvi_captcha(page, email, phash_db):
                    log(email, "❌ Captcha non risolto!")
                    # 🔥 Se captcha non risolto e proxy pubblico, incrementa contatore
                    if proxy_type == "public":
                        FALLIMENTI_PROXY[email] = FALLIMENTI_PROXY.get(email, 0) + 1
                        log(email, f"⚠️ Proxy pubblico fallito ({FALLIMENTI_PROXY[email]}/1)")
                    return
            
            balance_match = re.search(r'btoday["\']?\s*[=:]\s*([\d.]+)', html)
            if balance_match:
                log(email, f"💰 Balance: {balance_match.group(1)}")
            
            csrf_match = re.search(r'csrf_token=([a-f0-9]+)', html)
            if not csrf_match:
                log(email, "❌ CSRF non trovato!")
                # 🔥 Se CSRF non trovato e proxy pubblico, incrementa contatore
                if proxy_type == "public":
                    FALLIMENTI_PROXY[email] = FALLIMENTI_PROXY.get(email, 0) + 1
                    log(email, f"⚠️ Proxy pubblico fallito ({FALLIMENTI_PROXY[email]}/1)")
                return
            
            csrf = csrf_match.group(1)
            log(email, f"🎫 CSRF: {csrf[:16]}...")
            
            # SURF
            log(email, "🚀 Avvio surf...")
            
            key = ""
            time_val = 12
            ad_id = ""
            cycle = 0
            MAX_CYCLES = 10
            csrf_invalidi = 0
            MAX_CSRF_INVALIDI = 5
            
            while cycle < MAX_CYCLES:
                cycle += 1
                log(email, f"🔄 CICLO {cycle}")
                
                if ad_id:
                    ad_id_pulito = re.sub(r'<[^>]+>', '', ad_id)
                    ad_id_pulito = re.sub(r'[<>\'"]', '', ad_id_pulito)
                    match = re.search(r'(\d+)', ad_id_pulito)
                    ad_id_pulito = match.group(1) if match else ""
                else:
                    ad_id_pulito = ""
                
                params = {
                    "wallet": email,
                    "key": key,
                    "time": time_val,
                    "ad_id": ad_id_pulito,
                    "isitbad": 0,
                    "csrf_token": csrf
                }
                
                url = "https://antautosurf.com/surf.php?" + "&".join([f"{k}={v}" for k, v in params.items()])
                
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except:
                    continue
                
                page_text = page.content()
                
                if "Invalid CSRF token" in page_text:
                    csrf_invalidi += 1
                    log(email, f"❌ CSRF invalido! ({csrf_invalidi}/{MAX_CSRF_INVALIDI})")
                    
                    if csrf_invalidi >= MAX_CSRF_INVALIDI:
                        log(email, "🔄 Troppi CSRF invalidi!")
                        # 🔥 Se troppi CSRF invalidi e proxy pubblico, incrementa contatore
                        if proxy_type == "public":
                            FALLIMENTI_PROXY[email] = FALLIMENTI_PROXY.get(email, 0) + 1
                            log(email, f"⚠️ Proxy pubblico fallito ({FALLIMENTI_PROXY[email]}/1)")
                        return
                    
                    page.goto(f"https://antautosurf.com/index.php?bitcoinwallet={email}&ref=", wait_until="networkidle", timeout=30000)
                    time.sleep(2)
                    html = page.content()
                    csrf_match = re.search(r'csrf_token=([a-f0-9]+)', html)
                    if csrf_match:
                        csrf = csrf_match.group(1)
                        csrf_invalidi = 0
                        log(email, f"🎫 Nuovo CSRF: {csrf[:16]}...")
                    continue
                else:
                    csrf_invalidi = 0
                
                if "--_--" not in page_text:
                    time.sleep(5)
                    continue
                
                parts = page_text.split("--_--")
                if len(parts) < 4:
                    continue
                
                ad_url = re.sub(r'<[^>]+>', '', parts[0]).strip()
                ad_url = re.sub(r'[<>\'"]', '', ad_url)
                time_val = int(parts[1])
                key = parts[2]
                ad_id = parts[3]
                
                if "connection.php" in ad_url:
                    log(email, "   📂 Test anti-bot...")
                    for i in range(time_val, 0, -1):
                        print(f"[{email[:10]}] ⏳ {i}s", end="\r")
                        time.sleep(1)
                    print("   " * 20, end="\r")
                    continue
                
                # 🔥 ANNUNCIO REALE TROVATO!
                log(email, f"   📢 Annuncio reale! Timer: {time_val}s")
                surf_successo = True
                
                try:
                    new_page = context.new_page()
                    new_page.goto(ad_url, wait_until="domcontentloaded", timeout=10000)
                    time.sleep(1)
                except:
                    pass
                
                for i in range(time_val, 0, -1):
                    print(f"[{email[:10]}] ⏳ {i}s", end="\r")
                    time.sleep(1)
                print("   " * 20, end="\r")
                log(email, f"   ✅ Timer completato!")
                
                try:
                    new_page.close()
                except:
                    pass
                
                if cycle % 3 == 0:
                    page.goto(f"https://antautosurf.com/index.php?bitcoinwallet={email}&ref=", wait_until="networkidle", timeout=30000)
                    time.sleep(2)
                    html = page.content()
                    csrf_match = re.search(r'csrf_token=([a-f0-9]+)', html)
                    if csrf_match:
                        csrf = csrf_match.group(1)
                        log(email, f"   🎫 CSRF aggiornato: {csrf[:16]}...")
            
            log(email, f"✅ Completati {MAX_CYCLES} cicli, passo al prossimo account")
            
            # 🔥 Se ha funzionato con proxy pubblico, resetta il contatore
            if proxy_type == "public" and surf_successo:
                FALLIMENTI_PROXY[email] = 0
                log(email, f"✅ Proxy pubblico funzionante! Contatore resettato")
            
        except Exception as e:
            log(email, f"❌ Errore: {e}")
            # 🔥 Se errore e proxy pubblico, incrementa contatore
            if proxy_type == "public":
                FALLIMENTI_PROXY[email] = FALLIMENTI_PROXY.get(email, 0) + 1
                log(email, f"⚠️ Proxy pubblico fallito ({FALLIMENTI_PROXY[email]}/1)")
        finally:
            browser.close()

# ============================================================
# MAIN
# ============================================================

def main():
    print("="*60)
    print("🚀 BOT MULTI-ACCOUNT IBRIDO INTELLIGENTE")
    print("="*60)
    
    accounts = carica_accounts()
    if not accounts:
        print("❌ Nessun account trovato!")
        return
    
    proxy_pool = carica_proxy_pool()
    if not proxy_pool:
        print("⚠️ Nessun proxy ProxyScrape trovato, uso solo proxy pubblici")
        proxy_pool = []
    
    print(f"📋 Account: {len(accounts)}")
    print(f"📋 Proxy ProxyScrape: {len(proxy_pool)}")
    print(f"🔇 Headless: {HEADLESS}")
    print("="*60)
    print("🔄 1. Prova 1 proxy PUBBLICO")
    print("🔄 2. Se fallisce → usa proxy PROXYSCRAPE (permanentemente)")
    print("="*60)
    
    while True:
        for account in accounts:
            esegui_account(account, proxy_pool)
            time.sleep(5)
            print("─" * 60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Arresto manuale...")
        sys.exit(0)
