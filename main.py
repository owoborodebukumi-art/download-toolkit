import requests
import re
import time
import sys
import os
import platform
import subprocess
import shutil
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cf_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# ─── CONFIG ───────────────────────────────────────────────────
UA_DESKTOP = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
UA_MOBILE  = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36'

# ─── OS DETECTION ─────────────────────────────────────────────
def get_base_dir():
    if os.path.exists('/storage/emulated/0'):
        return '/storage/emulated/0/Anon'
    if platform.system() == 'Windows':
        return os.path.join(os.path.expanduser('~'), 'Downloads', 'Anon')
    return os.path.join(os.path.expanduser('~'), 'Downloads', 'Anon')

BASE_DIR = get_base_dir()

# ─── QUALITY SELECTION ────────────────────────────────────────
QUALITY_MAP = {
    '1': ('360p',  'bestvideo[height<=360]+bestaudio/best[height<=360]'),
    '2': ('480p',  'bestvideo[height<=480]+bestaudio/best[height<=480]'),
    '3': ('720p',  'bestvideo[height<=720]+bestaudio/best[height<=720]'),
    '4': ('1080p', 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'),
}
SELECTED_QUALITY = ("480p", "bestvideo[height<=480]+bestaudio/best[height<=480]")  # default
QUALITY_ASKED = False

def ask_quality():
    global SELECTED_QUALITY
    print("\nSelect download quality:")
    print("  1 — 360p  (fastest, smallest)")
    print("  2 — 480p  (balanced)")
    print("  3 — 720p  (good quality)")
    print("  4 — 1080p (best quality, largest)")
    while True:
        choice = input("Enter 1-4 (default 2): ").strip()
        if choice == '':
            choice = '2'
        if choice in QUALITY_MAP:
            SELECTED_QUALITY = QUALITY_MAP[choice]
            print(f"[✓] Quality set to {SELECTED_QUALITY[0]}")
            break
        print("[!] Enter 1, 2, 3 or 4")

# ─── YTDLP CHECK ──────────────────────────────────────────────
def has_ytdlp():
    return shutil.which('yt-dlp') is not None

def install_ytdlp():
    print("[*] Installing yt-dlp...")
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'yt-dlp'], check=True)
        print("[✓] yt-dlp installed")
        return True
    except Exception as e:
        print(f"[!] Failed to install yt-dlp: {e}")
        return False

# ─── SESSION FACTORIES ────────────────────────────────────────
def make_session(mobile=False):
    s = requests.Session()
    s.headers.update({'User-Agent': UA_MOBILE if mobile else UA_DESKTOP})
    return s

def make_cf_session():
    if HAS_CURL_CFFI:
        return cf_requests.Session(impersonate='chrome120')
    return make_session()

# ─── HELPERS ──────────────────────────────────────────────────
def safe_get(session, url, timeout=20, referer=None, retries=3):
    if referer:
        session.headers.update({'Referer': referer})
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            return r
        except Exception as e:
            print(f"  [!] Attempt {attempt+1}/{retries} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2)
    return None

def find_direct_video(text):
    for ext in [r'\.m3u8', r'\.mp4', r'\.mkv']:
        found = re.findall(r'https?://[^\s"\'<>,\\]+' + ext + r'[^\s"\'<>,\\]*', text)
        if found:
            return found[0].rstrip('.,;)')
    return None

def clean_name(slug):
    name = re.sub(r'[-_]+', ' ', slug)
    name = re.sub(r'\s+', ' ', name).strip()
    return name.title()

def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '', name).strip()

def is_streaming_link(url):
    return '.m3u8' in url or 'manifest' in url.lower()

# ─── DOWNLOADER ───────────────────────────────────────────────
def download_direct(url, folder, filename):
    """Download direct mp4/mkv using requests with progress bar."""
    filepath = os.path.join(folder, filename)
    os.makedirs(folder, exist_ok=True)

    try:
        session = make_session()
        session.headers.update({
            'Referer': '/'.join(url.split('/')[:3]) + '/',
            'Accept': '*/*',
        })
        r = session.get(url, stream=True, timeout=30)

        if r.status_code != 200:
            print(f"  [!] HTTP {r.status_code}")
            return False

        content_type = r.headers.get('content-type', '')
        if 'text/html' in content_type:
            print(f"  [!] Got HTML page instead of video file")
            return False

        total = int(r.headers.get('content-length', 0))
        downloaded = 0

        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        mb_done = downloaded / (1024 * 1024)
                        mb_total = total / (1024 * 1024)
                        print(f"\r  [↓] {pct}% — {mb_done:.1f}/{mb_total:.1f} MB", end='', flush=True)
        print()
        size = os.path.getsize(filepath)
        if size < 1024 * 100:  # less than 100KB = probably failed
            os.remove(filepath)
            print(f"  [!] Downloaded file too small, likely failed")
            return False
        print(f"  [✓] Saved: {filepath}")
        return True

    except Exception as e:
        print(f"  [!] Direct download error: {e}")
        return False

def download_with_ytdlp(url, folder, filename):
    global SELECTED_QUALITY, QUALITY_ASKED
    if not QUALITY_ASKED:
        ask_quality()
        QUALITY_ASKED = True
    """Download using yt-dlp with selected quality."""
    if not has_ytdlp():
        if not install_ytdlp():
            print(f"  [!] yt-dlp unavailable")
            return False

    os.makedirs(folder, exist_ok=True)
    base = re.sub(r'\.(mp4|mkv|m3u8)$', '', filename)
    out_template = os.path.join(folder, base + '.%(ext)s')
    quality_label, format_str = SELECTED_QUALITY

    print(f"  [↓] yt-dlp ({quality_label}): {filename}")
    try:
        result = subprocess.run([
            'yt-dlp',
            '-f', format_str,
            '--merge-output-format', 'mp4',
            '-o', out_template,
            '--no-playlist',
            url
        ])
        if result.returncode == 0:
            print(f"  [✓] Done: {filename}")
            return True
        else:
            print(f"  [✗] yt-dlp failed")
            return False
    except Exception as e:
        print(f"  [!] yt-dlp error: {e}")
        return False

def download_file(url, folder, filename):
    """
    Smart downloader:
    - m3u8/streaming → yt-dlp
    - direct mp4/mkv → requests first, yt-dlp as fallback
    """
    if is_streaming_link(url):
        return download_with_ytdlp(url, folder, filename)

    print(f"  [↓] Downloading: {filename}")
    success = download_direct(url, folder, filename)
    if not success:
        print(f"  [!] Falling back to yt-dlp...")
        success = download_with_ytdlp(url, folder, filename)
    return success

# ─── FILE HOST RESOLVERS ──────────────────────────────────────

def resolve_downloadwella(url, session):
    try:
        r = safe_get(session, url, timeout=20)
        if not r:
            return None
        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form')
        if not form:
            return None
        data = {inp.get('name'): inp.get('value', '')
                for inp in form.find_all('input') if inp.get('name')}
        data['method_free'] = 'Free Download'
        r2 = session.post(url, data=data, timeout=20)
        return find_direct_video(r2.text)
    except Exception as e:
        print(f"  [!] Downloadwella: {e}")
        return None

def resolve_loadedfiles(url, session):
    try:
        session.headers.update({'Referer': 'https://9jarocks.net/'})
        r1 = safe_get(session, url)
        if not r1:
            return None
        m1 = re.search(r"var downloadUrl = '(https://loadedfiles\.org/[^']+)'", r1.text)
        if not m1:
            return None
        session.headers.update({'Referer': 'https://loadedfiles.org/'})
        r2 = safe_get(session, m1.group(1))
        if not r2:
            return None
        m2 = re.search(r"var downloadUrl = '(https://loadedfiles\.org/[^']+)'", r2.text)
        if not m2:
            return None
        r3 = session.get(m2.group(1), timeout=20, allow_redirects=False)
        return r3.headers.get('location')
    except Exception as e:
        print(f"  [!] Loadedfiles: {e}")
        return None

def resolve_wildshare(url):
    if not HAS_CURL_CFFI:
        print("  [!] Wildshare requires curl_cffi — install it: pip install curl_cffi")
        return None
    try:
        s = make_cf_session()
        r = s.get(url, timeout=20)
        if not r or r.status_code != 200:
            return None
        pt = re.search(r'pt=([A-Za-z0-9%+=/]+)', r.text)
        if not pt:
            return None
        parts = url.rstrip('/').split('/')
        file_id = next((p for p in reversed(parts) if not p.endswith(('.mkv', '.mp4', '.m3u8'))), parts[-1])
        pt_url = f'https://wildshare.net/{file_id}?{pt.group(0)}'
        r2 = s.get(pt_url, timeout=20, allow_redirects=False)
        return r2.headers.get('location')
    except Exception as e:
        print(f"  [!] Wildshare: {e}")
        return None

def resolve_streamtape(url, session):
    try:
        session.headers.update({'Referer': 'https://watchadsontape.com/'})
        r = safe_get(session, url)
        if not r or r.status_code == 404:
            return None
        for line in r.text.split('\n'):
            if "getElementById('robotlink')" in line and 'substring' in line:
                m = re.search(r"innerHTML\s*=\s*'([^']+)'\s*\+\s*\('([^']+)'\)", line.strip())
                if m:
                    base, raw = m.group(1), m.group(2)
                    for n in re.findall(r'\.substring\((\d+)\)', line):
                        raw = raw[int(n):]
                    get_url = 'https:' + base + raw
                    r2 = session.get(get_url, timeout=20, allow_redirects=False)
                    loc = r2.headers.get('location')
                    if loc:
                        return loc
        v = find_direct_video(r.text)
        if v:
            return v
        print("  [!] Streamtape: no pattern matched")
        return None
    except Exception as e:
        print(f"  [!] Streamtape: {e}")
        return None

def resolve_vidmoly(embed_url, session):
    try:
        session.headers.update({
            'User-Agent': UA_DESKTOP,
            'Referer': 'https://myasiantv9.com.ro/'
        })
        r = safe_get(session, embed_url)
        if not r:
            return None
        m3u8 = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', r.text)
        if m3u8:
            return m3u8[0]
        mp4 = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', r.text)
        if mp4:
            return mp4[0]
        return None
    except Exception as e:
        print(f"  [!] Vidmoly: {e}")
        return None

def resolve_vidbasic(embed_url, session):
    BLOCKED_HOSTS = ['asianload', 'dood', 'streamvid']
    PREFERRED_HOSTS = ['watchadsontape.com', 'streamtape']
    for attempt in range(2):
        try:
            session.headers.update({'User-Agent': UA_DESKTOP, 'Referer': 'https://myasiantv9.com.ro/'})
            r = safe_get(session, embed_url)
            if not r:
                continue
            raw_servers = re.findall(r'data-video="(https?://[^"]+)"', r.text)
            servers = [u for u in raw_servers if not any(h in u for h in BLOCKED_HOSTS)]
            if not servers:
                print(f"  [!] No usable servers found (attempt {attempt+1})")
                time.sleep(3)
                continue
            ordered = sorted(servers, key=lambda u: 0 if any(h in u for h in PREFERRED_HOSTS) else 1)
            for sv_url in ordered:
                print(f"    [>] Trying: {sv_url[:60]}...")
                if 'watchadsontape.com' in sv_url or 'streamtape' in sv_url:
                    result = resolve_streamtape(sv_url, session)
                    if result:
                        return result
                else:
                    try:
                        session.headers.update({'Referer': embed_url})
                        r2 = safe_get(session, sv_url, timeout=15)
                        if r2:
                            v = find_direct_video(r2.text)
                            if v:
                                return v
                    except Exception as e:
                        print(f"    [!] Server error: {e}")
                        continue
            v = find_direct_video(r.text)
            if v:
                return v
        except Exception as e:
            print(f"  [!] Vidbasic attempt {attempt+1}: {e}")
            time.sleep(3)
    return None

def resolve_embed(src, session):
    if 'vidmoly' in src:
        return resolve_vidmoly(src, session)
    elif 'vidbasic' in src:
        return resolve_vidbasic(src, session)
    else:
        print(f"    [>] Unknown embed, trying generic: {src[:60]}...")
        r = safe_get(session, src)
        return find_direct_video(r.text) if r else None

def resolve_drip_waffi(url, session):
    try:
        r = safe_get(session, url, referer='https://dramarain.com/')
        if not r:
            return None
        m = re.search(r'window\.location\.href = "([^"]+)"', r.text)
        if m:
            return m.group(1)
        if 'drip.waffi.cloud' in url:
            return url
        return None
    except Exception as e:
        print(f"  [!] Drip: {e}")
        return None

# ─── SITE EXTRACTORS ──────────────────────────────────────────

def extract_nkiri(url, session):
    print("[*] NKIRI/Thenkiri mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-s\d+.*$', '', slug, flags=re.IGNORECASE)
    name = clean_name(name)
    print(f"[*] Series: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    r = safe_get(session, url)
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    links = list(dict.fromkeys(
        a['href'] for a in soup.find_all('a', href=True)
        if 'downloadwella.com' in a['href']
    ))
    print(f"[*] Found {len(links)} episode(s) — saving to: {folder}")
    for i, ep_url in enumerate(links, 1):
        ep_name = ep_url.split('/')[-1].replace('.html', '')
        print(f"\n[{i}/{len(links)}] {ep_name}")
        direct = resolve_downloadwella(ep_url, session)
        if direct:
            ext = 'mkv' if '.mkv' in direct else 'mp4'
            download_file(direct, folder, safe_filename(f"{ep_name}.{ext}"))
        else:
            print(f"  [✗] Could not extract link")
        time.sleep(1)

def extract_dramakey_com(url, session):
    print("[*] DramaKey.com mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-s\d+.*$', '', slug, flags=re.IGNORECASE)
    name = re.sub(r'-(season|episode|complete).*$', '', name, flags=re.IGNORECASE)
    name = clean_name(name)
    print(f"[*] Series: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    r = safe_get(session, url)
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    links = list(dict.fromkeys(
        a['href'] for a in soup.find_all('a', href=True)
        if 'downloadwella.com' in a['href']
    ))
    print(f"[*] Found {len(links)} episode(s) — saving to: {folder}")
    for i, ep_url in enumerate(links, 1):
        ep_name = ep_url.split('/')[-1].replace('.html', '')
        print(f"\n[{i}/{len(links)}] {ep_name}")
        direct = resolve_downloadwella(ep_url, session)
        if direct:
            ext = 'mkv' if '.mkv' in direct else 'mp4'
            download_file(direct, folder, safe_filename(f"{ep_name}.{ext}"))
        else:
            print(f"  [✗] Could not extract link")
        time.sleep(1)

def extract_9jarocks(url, session):
    print("[*] 9jaRocks mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-id\d+.*$', '', slug)
    name = clean_name(name)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    session.headers.update({'Referer': 'https://9jarocks.net/'})
    r = safe_get(session, url)
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    lf_links = list(dict.fromkeys(
        a['href'] for a in soup.find_all('a', href=True)
        if 'loadedfiles.org' in a['href']
    ))
    print(f"[*] Found {len(lf_links)} file(s) — saving to: {folder}")
    for i, lf_url in enumerate(lf_links, 1):
        fname = lf_url.split('/')[-1][:60]
        print(f"\n[{i}/{len(lf_links)}] {fname}")
        direct = resolve_loadedfiles(lf_url, session)
        if direct:
            ext = 'mkv' if '.mkv' in direct else 'mp4'
            download_file(direct, folder, safe_filename(f"{fname}.{ext}"))
        else:
            print(f"  [✗] Could not extract link")
        time.sleep(1)

def extract_naijaprey(url, session):
    print("[*] NaijaPrey mode")
    slug = url.rstrip('/').split('/')[-1]
    name = clean_name(slug)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    session.headers.update({'Referer': 'https://www.naijaprey.tv/'})
    r = safe_get(session, url)
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    ep_links = list(dict.fromkeys(
        a['href'] for a in soup.find_all('a', href=True)
        if 'vdl.np-downloader.com' in a['href']
    ))
    print(f"[*] Found {len(ep_links)} episode(s) — saving to: {folder}")
    for i, ep_url in enumerate(ep_links, 1):
        ep_name = ep_url.rstrip('/').split('/')[-1]
        print(f"\n[{i}/{len(ep_links)}] {ep_name}")
        try:
            session.headers.update({'Referer': 'https://www.naijaprey.tv/'})
            r2 = safe_get(session, ep_url)
            if not r2:
                continue
            soup2 = BeautifulSoup(r2.text, 'html.parser')
            ws_url = next((a['href'] for a in soup2.find_all('a', href=True)
                          if 'wildshare.net' in a['href']), None)
            if ws_url:
                direct = resolve_wildshare(ws_url)
                if direct:
                    ext = 'mkv' if '.mkv' in direct else 'mp4'
                    download_file(direct, folder, safe_filename(f"{ep_name}.{ext}"))
                else:
                    print(f"  [✗] Wildshare failed")
            else:
                print(f"  [!] No wildshare link found")
        except Exception as e:
            print(f"  [!] Error: {e}")
        time.sleep(2)

def extract_myasiantv(url, session):
    print("[*] MyAsianTV mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-episode-\d+.*$', '', slug)
    name = re.sub(r'-\d{4}.*$', '', name)
    name = clean_name(name)
    print(f"[*] Series: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    domain_match = re.search(r'(https?://[^/]+)', url)
    base_domain = domain_match.group(1) if domain_match else ''
    if 'episode-' in url:
        ep_links = [url]
    else:
        print("[*] Fetching episode list...")
        session.headers.update({'Referer': base_domain + '/'})
        r = safe_get(session, url, timeout=30)
        if not r:
            return
        soup = BeautifulSoup(r.text, 'html.parser')
        show_slug = re.sub(r'-\d{4}.*$', '', slug)
        ep_links = list(dict.fromkeys(
            a['href'] for a in soup.find_all('a', href=True)
            if ('episode-' in a['href'] and base_domain in a['href'] and show_slug in a['href'])
        ))
        if not ep_links:
            print("[!] No episode links found")
            return
        ep_links.sort(key=lambda u: int(m.group(1)) if (m := re.search(r'episode-(\d+)', u)) else 0)
        print(f"[*] Found {len(ep_links)} episode(s) — saving to: {folder}")
    for i, ep_url in enumerate(ep_links, 1):
        ep_name = ep_url.rstrip('/').split('/')[-1]
        print(f"\n[{i}/{len(ep_links)}] {ep_name}")
        session.headers.update({'Referer': base_domain + '/'})
        r = safe_get(session, ep_url, timeout=30)
        if not r:
            print(f"  [✗] Could not fetch episode page")
            continue
        soup = BeautifulSoup(r.text, 'html.parser')
        iframe = soup.find('iframe', src=re.compile(r'vidbasic|vidmoly'))
        if not iframe:
            iframe = soup.find('iframe', src=True)
        if not iframe:
            print(f"  [!] No iframe found")
            continue
        src = iframe.get('src', '')
        if not src.startswith('http'):
            src = 'https:' + src
        direct = resolve_embed(src, session)
        if direct:
            download_file(direct, folder, safe_filename(f"{ep_name}.mp4"))
        else:
            print(f"  [✗] Could not extract video")
        time.sleep(1)

def extract_dramarain(url, session):
    site = 'DramaKey.cc' if 'dramakey.cc' in url else 'DramaRain'
    print(f"[*] {site} mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-(chinese|korean|thai|japanese|drama|tvshows|movies?).*$', '', slug, flags=re.IGNORECASE)
    name = clean_name(name)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    session.headers.update({'Referer': url})
    r = safe_get(session, url)
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    drip_links = [(a.text.strip(), a['href']) for a in soup.find_all('a', href=True)
                  if 'drip.waffi.cloud' in a['href']]
    if drip_links:
        print(f"[*] Found {len(drip_links)} direct link(s) — saving to: {folder}")
        for i, (label, link) in enumerate(drip_links, 1):
            fname = safe_filename(label or f"episode-{i}")
            print(f"\n[{i}/{len(drip_links)}] {fname}")
            download_file(link, folder, f"{fname}.mp4")
        return
    dl_links = [(a.text.strip(), a['href']) for a in soup.find_all('a', href=True)
                if any(x in a['href'] for x in ['dramarain.com/download', 'drip.waffi.cloud'])]
    if dl_links:
        print(f"[*] Found {len(dl_links)} episode(s) — saving to: {folder}")
        for i, (label, dl_url) in enumerate(dl_links, 1):
            fname = safe_filename(label or f"episode-{i}")
            print(f"\n[{i}/{len(dl_links)}] {fname}")
            direct = dl_url if 'drip.waffi.cloud' in dl_url else resolve_drip_waffi(dl_url, session)
            if direct:
                download_file(direct, folder, f"{fname}.mp4")
            else:
                print(f"  [✗] Could not resolve link")
            time.sleep(0.5)
        return
    all_links = [a['href'] for a in soup.find_all('a', href=True)]
    print(f"[!] No download links found. Page has {len(all_links)} total links.")
    print(f"[!] Sample: {all_links[:5]}")

# ─── SITE DETECTION ───────────────────────────────────────────
SITE_MAP = {
    'thenkiri.com':      extract_nkiri,
    'nkiri.com':         extract_nkiri,
    'dramakey.com':      extract_dramakey_com,
    'dramakey.cc':       extract_dramarain,
    'dramarain.com':     extract_dramarain,
    '9jarocks.net':      extract_9jarocks,
    'naijaprey.tv':      extract_naijaprey,
    'myasiantv9.com.ro': extract_myasiantv,
    'myasiantv9.com':    extract_myasiantv,
}

def detect_site(url):
    for domain, extractor in SITE_MAP.items():
        if domain in url:
            return extractor
    return None

# ─── MAIN ─────────────────────────────────────────────────────
def main():
    session = make_session()

    # Non-interactive mode
    if len(sys.argv) >= 2:
        url = sys.argv[1].strip()
        extractor = detect_site(url)
        if not extractor:
            print(f"[!] Unsupported site: {url}")
            sys.exit(1)
        extractor(url, session)
        return

    # Interactive loop
    print("=" * 50)
    print("  DOWNLOAD TOOLKIT")
    print(f"  Saving to: {BASE_DIR}")
    print("=" * 50)
    print("Supported sites:")
    for domain in SITE_MAP:
        print(f"  • {domain}")


    print("\nPaste a link and press Enter | 'exit' to quit")

    while True:
        print("\n> Paste link:")
        try:
            url = input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not url:
            continue
        if url.lower() == 'exit':
            print("Bye!")
            break

        extractor = detect_site(url)
        if not extractor:
            print(f"[!] Unsupported site. Supported: {', '.join(SITE_MAP.keys())}")
            continue

        extractor(url, session)

if __name__ == '__main__':
    main()
