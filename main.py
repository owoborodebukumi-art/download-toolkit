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
UA_DESKTOP  = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
UA_MOBILE   = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36'
PLUTO_BASE  = 'https://plutomovies.com'
EP_REGEX    = re.compile(r'([Ss]\d{1,2}[Ee]\d{1,2}|\b[Ee]\d{1,2}\b|\bEpisode\s*\d{1,2}\b)', re.IGNORECASE)

# ─── OS DETECTION ─────────────────────────────────────────────
IS_ANDROID = os.path.exists('/storage/emulated/0')
BASE_DIR   = '/storage/emulated/0/Anon' if IS_ANDROID else os.path.join(os.path.expanduser('~'), 'Downloads', 'Anon')

# ─── TOOL AVAILABILITY (cached at startup) ────────────────────
HAS_ARIA2C = shutil.which('aria2c') is not None
HAS_YTDLP  = shutil.which('yt-dlp') is not None
HAS_FFMPEG = shutil.which('ffmpeg') is not None

# ─── ANDROID SETUP ────────────────────────────────────────────
def setup_android():
    if not IS_ANDROID:
        return
    if shutil.which('termux-wake-lock'):
        try:
            subprocess.Popen(['termux-wake-lock'],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            print("[✓] Wake lock enabled — screen can go off safely")
        except Exception as e:
            print(f"[!] Wake lock failed: {e}")
    else:
        print("[!] termux-wake-lock not found — install with: pkg install termux-api")

    if not os.environ.get('TMUX'):
        if shutil.which('tmux'):
            print("[*] Starting persistent tmux session...")
            try:
                os.execvp('tmux', ['tmux', 'new-session', '-A', '-s', 'download',
                                   sys.executable] + sys.argv)
            except Exception as e:
                print(f"[!] Could not start tmux: {e}")
                print("[!] Continuing without tmux — closing Termux will stop downloads")
        else:
            print("[!] tmux not found — install with: pkg install tmux")
            print("[!] Without tmux, closing Termux will stop downloads")

# ─── QUALITY SELECTION ────────────────────────────────────────
QUALITY_MAP = {
    '1': ('360p',  'bestvideo[height<=360]+bestaudio/best[height<=360]'),
    '2': ('480p',  'bestvideo[height<=480]+bestaudio/best[height<=480]'),
    '3': ('720p',  'bestvideo[height<=720]+bestaudio/best[height<=720]'),
    '4': ('1080p', 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'),
}
SELECTED_QUALITY = ('480p', 'bestvideo[height<=480]+bestaudio/best[height<=480]')
QUALITY_ASKED    = False

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

# ─── TOOL INSTALLERS ──────────────────────────────────────────
def install_aria2c():
    global HAS_ARIA2C
    print("[*] Installing aria2...")
    try:
        if IS_ANDROID:
            env = os.environ.copy()
            env['DEBIAN_FRONTEND'] = 'noninteractive'
            subprocess.run(['pkg', 'install', 'aria2', '-y'], check=True, env=env)
        elif platform.system() == 'Windows':
            print("[!] Install aria2 manually from https://github.com/aria2/aria2/releases")
            return False
        else:
            subprocess.run(['sudo', 'apt', 'install', 'aria2', '-y'], check=True)
        HAS_ARIA2C = True
        print("[✓] aria2 installed")
        return True
    except Exception as e:
        print(f"[!] Failed to install aria2: {e}")
        return False

def install_ytdlp():
    global HAS_YTDLP
    print("[*] Installing yt-dlp...")
    try:
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', 'yt-dlp',
             '--break-system-packages', '-q'],
            check=True
        )
        HAS_YTDLP = True
        print("[✓] yt-dlp installed")
        return True
    except Exception as e:
        print(f"[!] Failed to install yt-dlp: {e}")
        return False

# ─── SESSION FACTORY ──────────────────────────────────────────
def make_session(mobile=False):
    s = requests.Session()
    s.headers.update({'User-Agent': UA_MOBILE if mobile else UA_DESKTOP})
    return s

def make_cf_session():
    if HAS_CURL_CFFI:
        return cf_requests.Session(impersonate='chrome120')
    return None

# ─── HELPERS ──────────────────────────────────────────────────
def safe_get(session, url, timeout=20, referer=None, retries=3):
    """GET with retries. Referer sent per-request without mutating session."""
    for attempt in range(retries):
        try:
            req_headers = dict(session.headers)
            if referer:
                req_headers['Referer'] = referer
            r = session.get(url, timeout=timeout, headers=req_headers)
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
    """Remove invalid chars, collapse spaces, strip trailing dots."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name)
    name = name.strip().rstrip('.')
    return name

def clean_ep_name(raw):
    """Clean episode name from raw HTML text for use as filename."""
    # Remove common noise patterns like (720p), [MKV], – Download, etc.
    name = re.sub(r'\([\w\s]+p\)', '', raw)           # (720p), (1080p)
    name = re.sub(r'\[[\w\s]+\]', '', name)            # [MKV], [MP4]
    name = re.sub(r'download', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-–|]+', ' ', name)                # dashes and pipes
    name = re.sub(r'\s+', ' ', name).strip()
    return name or raw  # fallback to raw if cleaning leaves nothing

def is_streaming_link(url):
    return '.m3u8' in url or 'manifest' in url.lower()

def base_domain(url):
    m = re.search(r'(https?://[^/]+)', url)
    return m.group(1) if m else ''

def resolve_relative_url(base_url, location):
    """Safely resolve a redirect location that may be relative."""
    if not location:
        return None
    if location.startswith('http'):
        return location
    bd = base_domain(base_url)
    if bd:
        return bd + location
    return None

# ─── DOWNLOAD SUMMARY TRACKER ─────────────────────────────────
class DownloadSummary:
    def __init__(self):
        self.success = 0
        self.skipped = 0
        self.failed  = 0

    def report(self):
        total = self.success + self.skipped + self.failed
        if total == 0:
            return
        print(f"\n{'='*50}")
        print(f"  DOWNLOAD COMPLETE")
        print(f"  Total:     {total}")
        print(f"  ✓ Done:    {self.success}")
        if self.skipped:
            print(f"  ✓ Skipped: {self.skipped} (already downloaded)")
        if self.failed:
            print(f"  ✗ Failed:  {self.failed}")
        print(f"{'='*50}")

# ─── DOWNLOADER ───────────────────────────────────────────────
def already_downloaded(folder, filename):
    """Check if file exists and is complete (>10MB)."""
    base = re.sub(r'\.(mp4|mkv|m3u8)$', '', filename)
    for ext in ['mp4', 'mkv', 'webm']:
        filepath = os.path.join(folder, f"{base}.{ext}")
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            if size > 10 * 1024 * 1024:
                return True, filepath
            else:
                print(f"  [!] Incomplete file ({size/1024/1024:.1f}MB) — re-downloading")
                try:
                    os.remove(filepath)
                except Exception as e:
                    print(f"  [!] Could not remove incomplete file: {e}")
                return False, None
    return False, None

def get_referer_for_url(url):
    """Return the correct Referer header for a given download URL."""
    if 'dl.plutomovies.com' in url:
        return 'https://plutomovies.com/'
    if 'vikingfile.com' in url or 'vkng' in url:
        return 'https://vikingfile.com/'
    return base_domain(url) + '/'

def download_with_aria2c(url, folder, filename, summary):
    if not HAS_ARIA2C:
        if not install_aria2c():
            print("[!] aria2c unavailable — falling back to requests")
            return download_with_requests(url, folder, filename, summary)

    os.makedirs(folder, exist_ok=True)
    safe_fname = re.sub(r'[^\w]', '_', filename)[:30]
    session_file = os.path.join(folder, f'.aria2_{safe_fname}.txt')
    filepath = os.path.join(folder, filename)
    referer = get_referer_for_url(url)

    print(f"  [↓] aria2c: {filename}")
    try:
        cmd = [
            'aria2c',
            '-c',
            '--max-tries=0',
            '--retry-wait=10',
            '--timeout=60',
            '--connect-timeout=60',
            '--save-session', session_file,
            '--save-session-interval=30',
            '--file-allocation=none',
            '-x', '4',
            '-s', '4',
            '--user-agent', UA_DESKTOP,
            '--referer', referer,
            '-d', folder,
            '-o', filename,
            url
        ]
        result = subprocess.run(cmd)
        if result.returncode == 0:
            # Validate file after download — aria2c returns 0 even for HTML error pages
            if os.path.exists(filepath):
                size = os.path.getsize(filepath)
                size_mb = size / (1024 * 1024)
                if size < 1024 * 100:  # less than 100KB = HTML error page
                    print(f"  [✗] Downloaded file is only {size_mb:.2f}MB — likely an error page")
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                    summary.failed += 1
                    return False
                print(f"  [✓] Done: {filename} ({size_mb:.1f}MB)")
            else:
                print(f"  [✗] File not found after download")
                summary.failed += 1
                return False
            try:
                if os.path.exists(session_file):
                    os.remove(session_file)
            except Exception:
                pass
            summary.success += 1
            return True
        else:
            print(f"  [✗] aria2c failed (code {result.returncode})")
            summary.failed += 1
            return False
    except Exception as e:
        print(f"  [!] aria2c error: {e}")
        summary.failed += 1
        return False

def download_with_requests(url, folder, filename, summary):
    """Fallback downloader — cleans up partial files on failure."""
    filepath = os.path.join(folder, filename)
    os.makedirs(folder, exist_ok=True)
    try:
        s = make_session()
        r = s.get(url, stream=True, timeout=30,
                  headers={**dict(s.headers), 'Referer': base_domain(url) + '/'})
        if r.status_code != 200:
            print(f"  [!] HTTP {r.status_code}")
            summary.failed += 1
            return False
        content_type = r.headers.get('content-type', '')
        if 'text/html' in content_type:
            print(f"  [!] Got HTML instead of video")
            summary.failed += 1
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
        if not os.path.exists(filepath) or os.path.getsize(filepath) < 1024 * 100:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            print(f"  [!] File too small — likely failed")
            summary.failed += 1
            return False
        print(f"  [✓] Saved: {filepath}")
        summary.success += 1
        return True
    except Exception as e:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
        print(f"  [!] requests error: {e}")
        summary.failed += 1
        return False

def download_with_ytdlp(url, folder, filename, summary):
    global SELECTED_QUALITY, QUALITY_ASKED
    if not QUALITY_ASKED:
        ask_quality()
        QUALITY_ASKED = True

    if not HAS_YTDLP:
        if not install_ytdlp():
            print(f"  [!] yt-dlp unavailable")
            summary.failed += 1
            return False

    if not HAS_FFMPEG:
        print(f"  [!] ffmpeg not found — cannot merge video and audio streams")
        print(f"  [!] Install with: pkg install ffmpeg")
        summary.failed += 1
        return False

    os.makedirs(folder, exist_ok=True)
    base = re.sub(r'\.(mp4|mkv|m3u8)$', '', filename)
    out_template = os.path.join(folder, base + '.%(ext)s')
    quality_label, format_str = SELECTED_QUALITY

    print(f"  [↓] yt-dlp ({quality_label}): {filename}")
    try:
        cmd = [
            'yt-dlp',
            '-f', format_str,
            '--merge-output-format', 'mp4',
            '-o', out_template,
            '--no-playlist',
            '--retries', 'infinite',
            '--fragment-retries', 'infinite',
            '--retry-sleep', '10',
        ]
        if HAS_ARIA2C:
            cmd += [
                '--external-downloader', 'aria2c',
                '--external-downloader-args',
                'aria2c:-x 4 -s 4 -c --max-tries=0 --retry-wait=10 --timeout=60 --connect-timeout=60 --file-allocation=none'
            ]
        cmd.append(url)
        result = subprocess.run(cmd)
        if result.returncode == 0:
            print(f"  [✓] Done: {filename}")
            summary.success += 1
            return True
        else:
            print(f"  [✗] yt-dlp failed")
            summary.failed += 1
            return False
    except Exception as e:
        print(f"  [!] yt-dlp error: {e}")
        summary.failed += 1
        return False

def download_file(url, folder, filename, summary):
    done, _ = already_downloaded(folder, filename)
    if done:
        print(f"  [✓] Already downloaded — skipping")
        summary.skipped += 1
        return True
    if is_streaming_link(url):
        return download_with_ytdlp(url, folder, filename, summary)
    return download_with_aria2c(url, folder, filename, summary)

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
        r1 = safe_get(session, url, referer='https://9jarocks.net/')
        if not r1:
            return None
        m1 = re.search(r"var downloadUrl = '(https://loadedfiles\.org/[^']+)'", r1.text)
        if not m1:
            return None
        r2 = safe_get(session, m1.group(1), referer='https://loadedfiles.org/')
        if not r2:
            return None
        m2 = re.search(r"var downloadUrl = '(https://loadedfiles\.org/[^']+)'", r2.text)
        if not m2:
            return None
        try:
            r3 = session.get(m2.group(1), timeout=20, allow_redirects=False)
            return r3.headers.get('location')
        except Exception as e:
            print(f"  [!] Loadedfiles redirect failed: {e}")
            return None
    except Exception as e:
        print(f"  [!] Loadedfiles: {e}")
        return None

def resolve_wildshare(url):
    if not HAS_CURL_CFFI:
        print("  [!] Wildshare requires curl_cffi")
        print("  [!] Install with: pip install curl_cffi --break-system-packages")
        return None
    try:
        s = make_cf_session()
        if not s:
            print("  [!] Could not create curl_cffi session")
            return None
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
        r = safe_get(session, url, referer='https://watchadsontape.com/')
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
        r = safe_get(session, embed_url, referer='https://myasiantv9.com.ro/')
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
    BLOCKED_HOSTS   = ['asianload', 'dood', 'streamvid']
    PREFERRED_HOSTS = ['watchadsontape.com', 'streamtape']

    for attempt in range(2):
        try:
            r = safe_get(session, embed_url, referer='https://myasiantv9.com.ro/')
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
                        r2 = safe_get(session, sv_url, referer=embed_url, timeout=15)
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

# ─── SHARED DOWNLOADWELLA EXTRACTOR (Fix 6) ───────────────────
def _extract_downloadwella_site(url, session, site_label, name_cleaner):
    """
    Shared extractor for sites using downloadwella.com links.
    Used by nkiri, thenkiri, and dramakey.com — avoids duplicate code.
    """
    print(f"[*] {site_label} mode")
    slug = url.rstrip('/').split('/')[-1]
    name = name_cleaner(slug)
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
    summary = DownloadSummary()
    for i, ep_url in enumerate(links, 1):
        ep_name = ep_url.split('/')[-1].replace('.html', '')
        print(f"\n[{i}/{len(links)}] {ep_name}")
        direct = resolve_downloadwella(ep_url, session)
        if direct:
            ext = 'mkv' if '.mkv' in direct else 'mp4'
            download_file(direct, folder, safe_filename(f"{ep_name}.{ext}"), summary)
        else:
            print(f"  [✗] Could not extract link")
            summary.failed += 1
        time.sleep(1)
    summary.report()

# ─── SITE EXTRACTORS ──────────────────────────────────────────

def extract_nkiri(url, session):
    _extract_downloadwella_site(
        url, session,
        site_label='NKIRI/Thenkiri',
        name_cleaner=lambda s: re.sub(r'-s\d+.*$', '', s, flags=re.IGNORECASE)
    )

def extract_dramakey_com(url, session):
    def cleaner(s):
        s = re.sub(r'-s\d+.*$', '', s, flags=re.IGNORECASE)
        s = re.sub(r'-(season|episode|complete).*$', '', s, flags=re.IGNORECASE)
        return s
    _extract_downloadwella_site(url, session, site_label='DramaKey.com', name_cleaner=cleaner)

def extract_9jarocks(url, session):
    print("[*] 9jaRocks mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-id\d+.*$', '', slug)
    name = clean_name(name)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    r = safe_get(session, url, referer='https://9jarocks.net/')
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    lf_links = list(dict.fromkeys(
        a['href'] for a in soup.find_all('a', href=True)
        if 'loadedfiles.org' in a['href']
    ))
    print(f"[*] Found {len(lf_links)} file(s) — saving to: {folder}")
    summary = DownloadSummary()
    for i, lf_url in enumerate(lf_links, 1):
        fname = lf_url.split('/')[-1][:60]
        print(f"\n[{i}/{len(lf_links)}] {fname}")
        direct = resolve_loadedfiles(lf_url, session)
        if direct:
            ext = 'mkv' if '.mkv' in direct else 'mp4'
            download_file(direct, folder, safe_filename(f"{fname}.{ext}"), summary)
        else:
            print(f"  [✗] Could not extract link")
            summary.failed += 1
        time.sleep(1)
    summary.report()

def extract_naijaprey(url, session):
    print("[*] NaijaPrey mode")
    slug = url.rstrip('/').split('/')[-1]
    name = clean_name(slug)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    r = safe_get(session, url, referer='https://www.naijaprey.tv/')
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    ep_links = list(dict.fromkeys(
        a['href'] for a in soup.find_all('a', href=True)
        if 'vdl.np-downloader.com' in a['href']
    ))
    print(f"[*] Found {len(ep_links)} episode(s) — saving to: {folder}")
    summary = DownloadSummary()
    for i, ep_url in enumerate(ep_links, 1):
        ep_name = ep_url.rstrip('/').split('/')[-1]
        print(f"\n[{i}/{len(ep_links)}] {ep_name}")
        try:
            r2 = safe_get(session, ep_url, referer='https://www.naijaprey.tv/')
            if not r2:
                summary.failed += 1
                continue
            soup2 = BeautifulSoup(r2.text, 'html.parser')
            ws_url = next((a['href'] for a in soup2.find_all('a', href=True)
                          if 'wildshare.net' in a['href']), None)
            if ws_url:
                direct = resolve_wildshare(ws_url)
                if direct:
                    ext = 'mkv' if '.mkv' in direct else 'mp4'
                    download_file(direct, folder, safe_filename(f"{ep_name}.{ext}"), summary)
                else:
                    print(f"  [✗] Wildshare failed")
                    summary.failed += 1
            else:
                print(f"  [!] No wildshare link found")
                summary.failed += 1
        except Exception as e:
            print(f"  [!] Error: {e}")
            summary.failed += 1
        time.sleep(2)
    summary.report()

def extract_myasiantv(url, session):
    print("[*] MyAsianTV mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-episode-\d+.*$', '', slug)
    name = re.sub(r'-\d{4}.*$', '', name)
    name = clean_name(name)
    print(f"[*] Series: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    bd = base_domain(url)
    summary = DownloadSummary()

    if 'episode-' in url:
        ep_links = [url]
        print(f"[*] Saving to: {folder}")
    else:
        print("[*] Fetching episode list...")
        r = safe_get(session, url, referer=bd + '/', timeout=30)
        if not r:
            return
        soup = BeautifulSoup(r.text, 'html.parser')
        show_slug = re.sub(r'-\d{4}.*$', '', slug)
        ep_links = list(dict.fromkeys(
            a['href'] for a in soup.find_all('a', href=True)
            if ('episode-' in a['href'] and bd in a['href'] and show_slug in a['href'])
        ))
        if not ep_links:
            print("[!] No episode links found")
            return
        ep_links.sort(key=lambda u: int(m.group(1)) if (m := re.search(r'episode-(\d+)', u)) else 0)
        print(f"[*] Found {len(ep_links)} episode(s) — saving to: {folder}")

    for i, ep_url in enumerate(ep_links, 1):
        ep_name = ep_url.rstrip('/').split('/')[-1]
        print(f"\n[{i}/{len(ep_links)}] {ep_name}")
        r = safe_get(session, ep_url, referer=bd + '/', timeout=30)
        if not r:
            print(f"  [✗] Could not fetch episode page")
            summary.failed += 1
            continue
        soup = BeautifulSoup(r.text, 'html.parser')
        iframe = soup.find('iframe', src=re.compile(r'vidbasic|vidmoly'))
        if not iframe:
            iframe = soup.find('iframe', src=True)
        if not iframe:
            print(f"  [!] No iframe found")
            summary.failed += 1
            continue
        src = iframe.get('src', '')
        if not src.startswith('http'):
            src = 'https:' + src
        direct = resolve_embed(src, session)
        if direct:
            download_file(direct, folder, safe_filename(f"{ep_name}.mp4"), summary)
        else:
            print(f"  [✗] Could not extract video")
            summary.failed += 1
        time.sleep(1)
    summary.report()

def extract_dramarain(url, session):
    site = 'DramaKey.cc' if 'dramakey.cc' in url else 'DramaRain'
    print(f"[*] {site} mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-(chinese|korean|thai|japanese|drama|tvshows|movies?).*$', '', slug, flags=re.IGNORECASE)
    name = clean_name(name)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    r = safe_get(session, url, referer=base_domain(url))
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')
    summary = DownloadSummary()

    drip_links = [(a.text.strip(), a['href']) for a in soup.find_all('a', href=True)
                  if 'drip.waffi.cloud' in a['href']]
    if drip_links:
        print(f"[*] Found {len(drip_links)} direct link(s) — saving to: {folder}")
        for i, (label, link) in enumerate(drip_links, 1):
            fname = safe_filename(label or f"episode-{i}")
            print(f"\n[{i}/{len(drip_links)}] {fname}")
            download_file(link, folder, f"{fname}.mp4", summary)
        summary.report()
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
                download_file(direct, folder, f"{fname}.mp4", summary)
            else:
                print(f"  [✗] Could not resolve link")
                summary.failed += 1
            time.sleep(0.5)
        summary.report()
        return

    all_links = [a['href'] for a in soup.find_all('a', href=True)]
    print(f"[!] No download links found. Page has {len(all_links)} total links.")
    print(f"[!] Sample: {all_links[:5]}")

# ─── PLUTOMOVIES EXTRACTOR ────────────────────────────────────

def pluto_get_seasons(url, session):
    """
    Get season URLs from PlutoMovies.
    Two structures:
    1. Main series page links to season pages
    2. URL itself IS already a season page with episodes
    """
    r = safe_get(session, url, referer=PLUTO_BASE, timeout=30)
    if not r:
        return []
    soup = BeautifulSoup(r.text, 'html.parser')

    # Detect if this page already has episode links (s04e01 pattern)
    ep_links_on_page = [
        a['href'] for a in soup.find_all('a', href=True)
        if re.search(r'/series/\d+/[^"]+[Ss]\d{2}[Ee]\d{2}', a['href'])
        and '#disqus' not in a['href']
    ]
    if ep_links_on_page:
        slug = url.rstrip('/').split('/')[-1]
        name = slug.replace('-', ' ').title()
        return [{'name': name, 'url': url}]

    # Otherwise find season links on main series page
    seasons = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.text.strip()
        if '-season-' in href.lower() and '#disqus' not in href:
            full_url = href if href.startswith('http') else PLUTO_BASE + href
            name = text if text else href.split('/')[-1].replace('-', ' ').title()
            if not any(s['url'] == full_url for s in seasons):
                seasons.append({'name': name, 'url': full_url})
    return seasons

def pluto_get_episodes(season_url, session):
    """
    Get all episode URLs from a PlutoMovies season page, handling pagination.
    Episode links follow the pattern: /series/XXXXXX/show-name-s04e01
    We match on URL pattern directly instead of link text (text is often empty).
    """
    all_episodes = {}
    current_page = 1
    # Pattern matches PlutoMovies episode URLs: /series/123456/show-sXXeXX
    ep_url_pattern = re.compile(r'/series/\d+/[^"#]+[Ss]\d{2}[Ee]\d{2}', re.IGNORECASE)

    while True:
        page_url = season_url if current_page == 1 else f"{season_url}/page/{current_page}"
        r = safe_get(session, page_url, referer=season_url, timeout=30)
        if not r or r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, 'html.parser')
        found = 0
        for a in soup.find_all('a', href=True):
            href = a['href']
            # Skip non-episode links
            if '#disqus' in href or '/page/' in href:
                continue
            if not ep_url_pattern.search(href):
                continue
            full_url = href if href.startswith('http') else PLUTO_BASE + href
            if full_url not in all_episodes:
                # Get title from img alt or link text, fall back to URL slug
                img = a.find('img')
                alt = img.get('alt', '').strip() if img else ''
                text = a.get_text(strip=True)
                ep_title = alt or text or href.split('/')[-1].replace('-', ' ').title()
                all_episodes[full_url] = ep_title
                found += 1
        if found == 0:
            break
        current_page += 1
        time.sleep(0.5)
    return all_episodes

def pluto_get_download_link(ep_url, session):
    """
    Extract the dl.plutomovies.com link from episode page.
    This link is passed directly to yt-dlp which handles the JS rendering.
    """
    r = safe_get(session, ep_url, referer=PLUTO_BASE, timeout=30)
    if not r:
        return None
    soup = BeautifulSoup(r.text, 'html.parser')

    # Priority 1: dl.plutomovies.com — pass directly to yt-dlp
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'dl.plutomovies.com' in href:
            return href if href.startswith('http') else PLUTO_BASE + href

    # Priority 2: direct video file links
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.endswith(('.mp4', '.mkv', '.avi')):
            return href if href.startswith('http') else PLUTO_BASE + href

    # Last resort: scan page source
    return find_direct_video(r.text)

def extract_plutomovies(url, session):
    print("[*] PlutoMovies mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-\d{4}(-tv)?$', '', slug)
    name = clean_name(name)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    summary = DownloadSummary()

    seasons = pluto_get_seasons(url, session)

    if seasons:
        print(f"[*] Found {len(seasons)} season(s)")
        for season in seasons:
            print(f"\n[*] Season: {season['name']}")
            episodes = pluto_get_episodes(season['url'], session)
            if not episodes:
                print(f"  [!] No episodes found for {season['name']}")
                continue
            print(f"  [*] Found {len(episodes)} episode(s)")
            ep_list = list(episodes.items())
            for i, (ep_url, ep_title) in enumerate(ep_list, 1):
                print(f"\n  [{i}/{len(ep_list)}] {ep_title}")
                direct = pluto_get_download_link(ep_url, session)
                if direct:
                    ext = 'mkv' if 'mkv' in direct.lower() else 'mp4'
                    fname = safe_filename(f"{ep_title}.{ext}")
                    download_file(direct, folder, fname, summary)
                else:
                    print(f"  [✗] No download link found")
                    summary.failed += 1
                time.sleep(1)
    else:
        print("[*] Treating as single movie/episode")
        direct = pluto_get_download_link(url, session)
        if direct:
            ext = 'mkv' if 'mkv' in direct.lower() else 'mp4'
            fname = safe_filename(f"{name}.{ext}")
            download_file(direct, folder, fname, summary)
        else:
            print("[✗] No download link found")
            summary.failed += 1

    summary.report()

# ─── NAIJAVAULT + VIKINGFILE EXTRACTOR ───────────────────────

def resolve_vikingfile(url, session):
    """
    Resolve vikingfile.com URL to direct CDN download link.
    Two-hop redirect chain:
    Hop 1: vikingfile.com/f/{code} → vikingfile.com/d/{code2}/{filename}
    Hop 2: vikingfile.com/d/{code2}/{filename} → lp.vikingfile.com/download/...?md5=...
    Referer must be naijavault.com throughout.
    """
    try:
        session.headers.update({'Referer': 'https://www.naijavault.com/'})
        r1 = session.get(url, timeout=15, allow_redirects=False)
        loc1 = r1.headers.get('location')
        if not loc1:
            print(f"  [!] VikingFile: no redirect on hop 1")
            return None
        r2 = session.get(loc1, timeout=15, allow_redirects=False)
        loc2 = r2.headers.get('location')
        return loc2 if loc2 else loc1
    except Exception as e:
        print(f"  [!] VikingFile resolve error: {e}")
        return None

def extract_viking_url(gw_url, session):
    """Extract VikingFile URL from NaijaVault gateway /dl- page.
    Fix 8: uses safe_get with retries instead of raw session.get."""
    try:
        r = safe_get(session, gw_url, referer='https://www.naijavault.com/',
                     timeout=15, retries=3)
        if not r:
            return None
        m = re.search(r'(https?://vikingfile\.com/[fd]/[^\s"\'<>]+)', r.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"  [!] Gateway error: {e}")
    return None

def extract_naijavault(url, session):
    print("[*] NaijaVault mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-\d{4}.*$', '', slug)
    name = re.sub(r'-season-\d+.*$', '', name, flags=re.IGNORECASE)
    name = clean_name(name)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))

    r = safe_get(session, url, referer='https://www.naijavault.com/', timeout=30)
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')

    # Fix 2: use set for O(1) deduplication instead of O(n²) linear search
    seen_hrefs = set()
    episodes = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if ('/dl-' in href or 'vikingfile.com' in href) and href not in seen_hrefs:
            seen_hrefs.add(href)
            # Fix 10: clean raw episode name before using as filename
            raw_name = a.get_text(strip=True) or f"episode-{len(episodes)+1}"
            ep_name  = clean_ep_name(raw_name) or f"episode-{len(episodes)+1}"
            episodes.append({'name': ep_name, 'href': href})

    if not episodes:
        print("[!] No episode links found")
        return

    print(f"[*] Found {len(episodes)} episode(s) — saving to: {folder}")
    summary = DownloadSummary()

    for i, ep in enumerate(episodes, 1):
        ep_name = safe_filename(ep['name'] or f"episode-{i}")
        print(f"\n[{i}/{len(episodes)}] {ep_name}")

        if '/dl-' in ep['href']:
            viking_url = extract_viking_url(ep['href'], session)
        else:
            viking_url = ep['href']

        if not viking_url:
            print(f"  [✗] Could not find VikingFile URL")
            summary.failed += 1
            continue

        cdn_url = resolve_vikingfile(viking_url, session)
        if not cdn_url:
            print(f"  [✗] Could not resolve CDN link")
            summary.failed += 1
            continue

        print(f"  [✓] CDN link resolved")
        ext = 'mkv' if '.mkv' in cdn_url else 'mp4'
        download_file(cdn_url, folder, safe_filename(f"{ep_name}.{ext}"), summary)
        time.sleep(1)

    summary.report()

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
    'plutomovies.com':   extract_plutomovies,
    'naijavault.com':    extract_naijavault,
}

def detect_site(url):
    for domain, extractor in SITE_MAP.items():
        if domain in url:
            return extractor
    return None

# ─── MAIN ─────────────────────────────────────────────────────
def main():
    setup_android()
    session = make_session()

    if len(sys.argv) >= 2:
        url = sys.argv[1].strip()
        extractor = detect_site(url)
        if not extractor:
            print(f"[!] Unsupported site: {url}")
            sys.exit(1)
        try:
            extractor(url, session)
        except Exception as e:
            print(f"\n[!] Unexpected error: {e}")
            print("[!] Please check the URL and try again")
        return

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
        if not url.startswith('http'):
            print("[!] That doesn't look like a URL. Paste a full link starting with http")
            continue
        extractor = detect_site(url)
        if not extractor:
            print(f"[!] Unsupported site. Supported: {', '.join(SITE_MAP.keys())}")
            continue
        try:
            extractor(url, session)
        except Exception as e:
            print(f"\n[!] Unexpected error: {e}")
            print("[!] Please check the URL and try again")

if __name__ == '__main__':
    main()# ─── PLUTOMOVIES EXTRACTOR ────────────────────────────────────────
from urllib.parse import urljoin as _urljoin

PLUTO_BASE  = 'https://plutomovies.com'
EP_KEYWORDS = ['-e', 'episode', 's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9']
EP_REGEX    = re.compile(r'([Ss]\d{1,2}[Ee]\d{1,2}|[Ee]\d{1,2}|Episode\s*\d{1,2})', re.IGNORECASE)

def resolve_plutomovies_dl(dl_url, session):
    """
    Fetch dl.plutomovies.com page and extract the kissorgrab.com direct link
    from the downloadButton onclick JS.
    Pattern: location.href = 'https://nv1e.kissorgrab.com/dl/{token}'
    """
    try:
        session.headers.update({'Referer': PLUTO_BASE + '/'})
        r = session.get(dl_url, timeout=15)
        if not r:
            return None
        m = re.search(
            r"getElementById\('downloadButton'\)\.onclick\s*=\s*function\(\)\s*\{"
            r"\s*location\.href\s*=\s*'(https://[^']+)'",
            r.text, re.DOTALL
        )
        if m:
            return m.group(1)
        # Fallback: scan for any kissorgrab link
        kg = re.search(r'https?://[^\s<>"]+kissorgrab[^\s<>"]+', r.text)
        if kg:
            return kg.group(0)
        return None
    except Exception as e:
        print(f"  [!] PlutoMovies DL: {e}")
        return None

def extract_plutomovies(url, session):
    print("[*] PlutoMovies mode")
    from urllib.parse import urljoin

    is_movie = '/movie/' in url
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-\d{4}.*$', '', slug).replace('-', ' ').title()
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))
    summary = DownloadSummary()

    session.headers.update({'Referer': PLUTO_BASE + '/'})
    r = safe_get(session, url, timeout=30)
    if not r:
        return

    soup = BeautifulSoup(r.text, 'html.parser')

    # Check if this page has a direct dl.plutomovies.com link
    # (single movie or direct episode URL)
    dl_link = next((a['href'] for a in soup.find_all('a', href=True)
                   if 'dl.plutomovies.com' in a['href']), None)

    if is_movie or dl_link:
        if dl_link:
            print(f"[*] Direct link found — saving to: {folder}")
            direct = resolve_plutomovies_dl(dl_link, session)
            if direct:
                ext = 'mkv' if 'mkv' in direct.lower() else 'mp4'
                fname = safe_filename(f"{name}.{ext}")
                download_file(direct, folder, fname, summary)
            else:
                print("[✗] Could not resolve download link")
                summary.failed += 1
        else:
            print("[✗] No download link found on page")
            summary.failed += 1
        summary.report()
        return

    # Series: find season links (use urljoin for relative paths)
    season_links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/series/' in href and 'season' in href.lower():
            full_url = urljoin(PLUTO_BASE, href)
            if full_url != url and full_url not in season_links:
                season_links.append(full_url)

    # If no season links found, treat this page itself as the season
    if not season_links:
        season_links = [url]

    print(f"[*] Found {len(season_links)} season(s)")

    for season_url in season_links:
        season_name = season_url.rstrip('/').split('/')[-1]
        print(f"\n[*] Season: {season_name}")
        page = 1
        seen_eps = set()

        while True:
            page_url = season_url if page == 1 else f"{season_url}/page/{page}"
            r2 = safe_get(session, page_url, timeout=30)
            if not r2 or r2.status_code == 404:
                break

            soup2 = BeautifulSoup(r2.text, 'html.parser')

            # Find episode links — match on keywords in href
            # Use urljoin to handle relative paths
            ep_links = []
            for a in soup2.find_all('a', href=True):
                href = a['href']
                if '/series/' not in href:
                    continue
                full_url = urljoin(PLUTO_BASE, href)
                if full_url == season_url or full_url in seen_eps:
                    continue
                if not any(x in href.lower() for x in EP_KEYWORDS):
                    continue
                ep_links.append(full_url)

            new_eps = list(dict.fromkeys(ep_links))
            if not new_eps:
                break

            # Reverse to process chronologically (ep1 → ep10)
            new_eps.reverse()
            for ep_url in new_eps:
                seen_eps.add(ep_url)

            print(f"  [*] Page {page}: {len(new_eps)} episode(s)")

            for ep_url in new_eps:
                ep_name = safe_filename(ep_url.rstrip('/').split('/')[-1])
                print(f"\n  [{summary.success + summary.failed + summary.skipped + 1}] {ep_name}")

                r3 = safe_get(session, ep_url, timeout=30)
                if not r3:
                    print(f"  [✗] Could not fetch episode page")
                    summary.failed += 1
                    continue

                soup3 = BeautifulSoup(r3.text, 'html.parser')
                dl_link = next((a['href'] for a in soup3.find_all('a', href=True)
                               if 'dl.plutomovies.com' in a['href']), None)

                if not dl_link:
                    print(f"  [✗] No download link on episode page")
                    summary.failed += 1
                    continue

                direct = resolve_plutomovies_dl(dl_link, session)
                if direct:
                    ext = 'mkv' if 'mkv' in direct.lower() else 'mp4'
                    fname = safe_filename(f"{ep_name}.{ext}")
                    download_file(direct, folder, fname, summary)
                else:
                    print(f"  [✗] Could not resolve download link")
                    summary.failed += 1

                time.sleep(1)

            page += 1
            time.sleep(1)

    summary.report()

# ─── NAIJAVAULT + VIKINGFILE EXTRACTOR ───────────────────────
# ─── NAIJAVAULT + VIKINGFILE EXTRACTOR ───────────────────────

def resolve_vikingfile(url, session):
    """
    Resolve vikingfile.com URL to direct CDN download link.
    Two-hop redirect chain:
    Hop 1: vikingfile.com/f/{code} → vikingfile.com/d/{code2}/{filename}
    Hop 2: vikingfile.com/d/{code2}/{filename} → lp.vikingfile.com/download/...?md5=...
    Referer must be naijavault.com throughout.
    """
    try:
        session.headers.update({'Referer': 'https://www.naijavault.com/'})
        r1 = session.get(url, timeout=15, allow_redirects=False)
        loc1 = r1.headers.get('location')
        if not loc1:
            print(f"  [!] VikingFile: no redirect on hop 1")
            return None
        r2 = session.get(loc1, timeout=15, allow_redirects=False)
        loc2 = r2.headers.get('location')
        return loc2 if loc2 else loc1
    except Exception as e:
        print(f"  [!] VikingFile resolve error: {e}")
        return None

def extract_viking_url(gw_url, session):
    """Extract VikingFile URL from NaijaVault gateway /dl- page.
    Fix 8: uses safe_get with retries instead of raw session.get."""
    try:
        r = safe_get(session, gw_url, referer='https://www.naijavault.com/',
                     timeout=15, retries=3)
        if not r:
            return None
        m = re.search(r'(https?://vikingfile\.com/[fd]/[^\s"\'<>]+)', r.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"  [!] Gateway error: {e}")
    return None

def extract_naijavault(url, session):
    print("[*] NaijaVault mode")
    slug = url.rstrip('/').split('/')[-1]
    name = re.sub(r'-\d{4}.*$', '', slug)
    name = re.sub(r'-season-\d+.*$', '', name, flags=re.IGNORECASE)
    name = clean_name(name)
    print(f"[*] Title: {name}")
    folder = os.path.join(BASE_DIR, safe_filename(name))

    r = safe_get(session, url, referer='https://www.naijavault.com/', timeout=30)
    if not r:
        return
    soup = BeautifulSoup(r.text, 'html.parser')

    # Fix 2: use set for O(1) deduplication instead of O(n²) linear search
    seen_hrefs = set()
    episodes = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if ('/dl-' in href or 'vikingfile.com' in href) and href not in seen_hrefs:
            seen_hrefs.add(href)
            # Fix 10: clean raw episode name before using as filename
            raw_name = a.get_text(strip=True) or f"episode-{len(episodes)+1}"
            ep_name  = clean_ep_name(raw_name) or f"episode-{len(episodes)+1}"
            episodes.append({'name': ep_name, 'href': href})

    if not episodes:
        print("[!] No episode links found")
        return

    print(f"[*] Found {len(episodes)} episode(s) — saving to: {folder}")
    summary = DownloadSummary()

    for i, ep in enumerate(episodes, 1):
        ep_name = safe_filename(ep['name'] or f"episode-{i}")
        print(f"\n[{i}/{len(episodes)}] {ep_name}")

        if '/dl-' in ep['href']:
            viking_url = extract_viking_url(ep['href'], session)
        else:
            viking_url = ep['href']

        if not viking_url:
            print(f"  [✗] Could not find VikingFile URL")
            summary.failed += 1
            continue

        cdn_url = resolve_vikingfile(viking_url, session)
        if not cdn_url:
            print(f"  [✗] Could not resolve CDN link")
            summary.failed += 1
            continue

        print(f"  [✓] CDN link resolved")
        ext = 'mkv' if '.mkv' in cdn_url else 'mp4'
        download_file(cdn_url, folder, safe_filename(f"{ep_name}.{ext}"), summary)
        time.sleep(1)

    summary.report()

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
    'plutomovies.com':   extract_plutomovies,
    'naijavault.com':    extract_naijavault,
}

def detect_site(url):
    for domain, extractor in SITE_MAP.items():
        if domain in url:
            return extractor
    return None

# ─── MAIN ─────────────────────────────────────────────────────
def main():
    setup_android()
    session = make_session()

    if len(sys.argv) >= 2:
        url = sys.argv[1].strip()
        extractor = detect_site(url)
        if not extractor:
            print(f"[!] Unsupported site: {url}")
            sys.exit(1)
        try:
            extractor(url, session)
        except Exception as e:
            print(f"\n[!] Unexpected error: {e}")
            print("[!] Please check the URL and try again")
        return

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
        if not url.startswith('http'):
            print("[!] That doesn't look like a URL. Paste a full link starting with http")
            continue
        extractor = detect_site(url)
        if not extractor:
            print(f"[!] Unsupported site. Supported: {', '.join(SITE_MAP.keys())}")
            continue
        try:
            extractor(url, session)
        except Exception as e:
            print(f"\n[!] Unexpected error: {e}")
            print("[!] Please check the URL and try again")

if __name__ == '__main__':
    main()
