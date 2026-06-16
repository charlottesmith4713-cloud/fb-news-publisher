# -*- coding: utf-8 -*-
"""
نظام نشر الأخبار المستمر - النسخة 25 (GitHub Actions Ready)
"""

import os
import sys
import io
import re
import time
import signal
import hashlib
import sqlite3
import logging
import argparse
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, List, Tuple
from urllib.parse import urljoin


# =====================================================
# إعداد Logging
# =====================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger('publisher')

logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('PIL').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)


# =====================================================
# استيراد المكتبات
# =====================================================
try:
    import requests
    import feedparser
    from bs4 import BeautifulSoup
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
except ImportError as e:
    logger.error(f"❌ مكتبة أساسية مفقودة: {e}")
    sys.exit(1)

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _HAS_RTL = True
except ImportError as e:
    logger.warning(f"⚠️ مكتبات RTL مفقودة: {e}")
    _HAS_RTL = False


try:
    from PIL import features as _pil_features
    _HAS_RAQM = _pil_features.check('raqm')
except Exception:
    _HAS_RAQM = False


def ar(text):
    if not text:
        return ""
    # إذا libraqm متاح، يعالج العربية تلقائياً (لا نسوي reshape/bidi لتفادي العكس المزدوج)
    if _HAS_RAQM:
        return str(text)
    # غير ذلك، نطبّق reshape + bidi يدوياً
    if not _HAS_RTL:
        return str(text)
    try:
        reshaped = arabic_reshaper.reshape(str(text))
        return get_display(reshaped)
    except Exception:
        return str(text)


# =====================================================
# Cache الخطوط
# =====================================================
FONT_PATHS = (
    "/usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Bold.ttf",
    "/usr/share/fonts/truetype/amiri/Amiri-Bold.ttf",
    "/usr/share/fonts/truetype/amiri/AmiriQuran-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabicUI-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

_font_cache = {}
_resolved_font_path = None
_font_path_searched = False


def _resolve_font_path():
    global _resolved_font_path, _font_path_searched
    if _font_path_searched:
        return _resolved_font_path
    _font_path_searched = True
    for path in FONT_PATHS:
        if os.path.exists(path):
            _resolved_font_path = path
            logger.info(f"📝 الخط المستخدم: {path}")
            return path
    logger.warning("⚠️ لم يُعثر على خط عربي")
    return None


def get_font(size):
    if size in _font_cache:
        return _font_cache[size]
    path = _resolve_font_path()
    try:
        font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except (OSError, IOError):
        font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def _load_token(env_name):
    val = os.environ.get(env_name, '').strip()
    if not val:
        return ''
    if '|' in val:
        parts = val.split('|', 1)
        return parts[1].strip() if len(parts) > 1 else ''
    return val


# =====================================================
# الأرقام والشهور العربية
# =====================================================
ARABIC_NUMERALS = str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩')
ARABIC_MONTHS = {
    1: 'يناير', 2: 'فبراير', 3: 'مارس', 4: 'أبريل',
    5: 'مايو', 6: 'يونيو', 7: 'يوليو', 8: 'أغسطس',
    9: 'سبتمبر', 10: 'أكتوبر', 11: 'نوفمبر', 12: 'ديسمبر',
}


def format_arabic_date(dt=None):
    if dt is None:
        dt = datetime.now()
    day = str(dt.day).translate(ARABIC_NUMERALS)
    month = ARABIC_MONTHS.get(dt.month, '')
    year = str(dt.year).translate(ARABIC_NUMERALS)
    return f"{day} {month} {year}"


# =====================================================
# إعدادات الصفحات
# =====================================================
PAGES_CONFIG = {
    'salssal': {
        'page_id': '1104346172760947',
        'name': 'صلصال',
        'logo_letter': 'ص',
        'token': _load_token('PAGE_SALSSAL'),
        'bar_grad_start': (210, 175, 100),
        'bar_grad_end':   (140, 100,  45),
        'bar_text':       (255, 255, 255),
        'border1':        (220, 185, 110),
        'border2':        (160, 120,  55),
        'glow':           (255, 240, 180),
        'overlay_color':  ( 20,  10,   0),
        'deco_color':     (255, 220, 120),
        'accent':         (200,  50,  50),
    },
    'chai': {
        'page_id': '1078693568663658',
        'name': 'چاي سادة',
        'logo_letter': 'چ',
        'token': _load_token('PAGE_CHAI'),
        'bar_grad_start': ( 35,  22,   8),
        'bar_grad_end':   ( 15,   8,   2),
        'bar_text':       (212, 175,  55),
        'border1':        (212, 175,  55),
        'border2':        (140, 105,  25),
        'glow':           (255, 215,  80),
        'overlay_color':  ( 10,   5,   0),
        'deco_color':     (212, 175,  55),
        'accent':         (220,  40,  40),
    },
    'taboga': {
        'page_id': '1063874040148711',
        'name': 'طابوگة',
        'logo_letter': 'ط',
        'token': _load_token('PAGE_TABOGA'),
        'bar_grad_start': (  5,   5,   5),
        'bar_grad_end':   (  0,   0,   0),
        'bar_text':       (212, 175,  55),
        'border1':        (212, 175,  55),
        'border2':        (140, 105,  25),
        'glow':           (255, 215,  80),
        'overlay_color':  (  0,   0,   0),
        'deco_color':     (212, 175,  55),
        'accent':         (220,  40,  40),
    },
    'tein': {
        'page_id': '1094102397116855',
        'name': 'طين',
        'logo_letter': 'ط',
        'token': _load_token('PAGE_TEIN'),
        'bar_grad_start': (230, 215, 185),
        'bar_grad_end':   (200, 180, 145),
        'bar_text':       ( 90,  55,  20),
        'border1':        (180, 145,  90),
        'border2':        (120,  90,  45),
        'glow':           (200, 160,  80),
        'overlay_color':  ( 60,  35,  10),
        'deco_color':     (150, 110,  55),
        'accent':         (180,  40,  40),
    },
}


# =====================================================
# مصادر الأخبار
# =====================================================
NEWS_SOURCES = (
    'https://www.alsumaria.tv/rss',
    'https://www.shafaq.com/ar/rss.xml',
    'https://www.rudaw.net/arabic/rss',
    'https://www.ina.iq/rss.xml',
    'https://www.mawazin.net/rss',
    'https://www.ina.iq/rss_feed.xml',
    'http://non14.net/services/rss',
    'https://kitabat.com/feed/',
    'https://aliraqnews.com/feed/',
    'https://almasalah.com/rss/',
    'https://www.sotaliraq.com/feed/',
    'https://www.iraq-businessnews.com/feed/',
    'https://www.aljazeera.net/rss/all.xml',
    'https://feeds.bbci.co.uk/arabic/rss.xml',
    'https://arabic.rt.com/rss/',
)

IRAQ_KEYWORDS = (
    'العراق', 'عراق', 'بغداد', 'البصرة', 'الموصل', 'أربيل', 'كركوك',
    'السليمانية', 'النجف', 'كربلاء', 'الكاظمي', 'السوداني', 'الحكومة العراقية',
    'البرلمان العراقي', 'الجيش العراقي', 'الحشد الشعبي', 'الكرد', 'العراقي',
    'العراقية', 'العراقيين', 'دينار', 'نفط العراق',
)

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)
REQUEST_HEADERS = {'User-Agent': USER_AGENT}
IMAGE_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    'Referer': 'https://www.google.com/',
}

DB_PATH = os.environ.get('NEWS_DB_PATH', '/tmp/news_cache_v25.db')
MAX_RETRIES_PER_NEWS = 3
MAX_DB_INIT_ATTEMPTS = 5
NEWS_CLEANUP_AFTER_DAYS = 3
FETCH_INTERVAL_CYCLES = 3
CLEANUP_INTERVAL_CYCLES = 120
LOOP_DELAY_SECONDS = 10
REQUEST_TIMEOUT = 8
FB_POST_TIMEOUT = 30
IMAGE_QUALITY = 88

_shutdown_requested = False


def _signal_handler(signum, _frame):
    global _shutdown_requested
    try:
        sig_name = signal.Signals(signum).name
    except (ValueError, AttributeError):
        sig_name = str(signum)
    logger.info(f"⛔ استلام إشارة {sig_name}، الإيقاف اللطيف...")
    _shutdown_requested = True


# =====================================================
# قاعدة البيانات
# =====================================================
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_hash TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                image_url TEXT,
                retry_count INTEGER DEFAULT 0,
                saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                posted INTEGER DEFAULT 0,
                posted_at TIMESTAMP
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_posted ON news(posted, retry_count)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_saved_at ON news(saved_at)')
        conn.commit()
        conn.close()
        logger.info(f"✅ قاعدة البيانات جاهزة: {DB_PATH}")
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في init_db: {e}")
        return False


def _title_hash(title):
    if not title:
        return ''
    normalized = re.sub(r'[^\w\s\u0600-\u06FF]', ' ', title)
    normalized = re.sub(r'\s+', ' ', normalized).strip().lower()
    if not normalized:
        return ''
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()


def save_news(title, url, image_url=''):
    if not title or len(title) < 10:
        return False
    h = _title_hash(title)
    if not h:
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'INSERT OR IGNORE INTO news (title_hash, title, url, image_url) VALUES (?, ?, ?, ?)',
            (h, title, url, image_url)
        )
        inserted = c.rowcount > 0
        conn.commit()
        conn.close()
        return inserted
    except sqlite3.Error:
        return False


def get_unposted():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            SELECT id, title, url, image_url, retry_count
            FROM news
            WHERE posted=0 AND retry_count < ?
            ORDER BY id ASC
            LIMIT 1
        ''', (MAX_RETRIES_PER_NEWS,))
        row = c.fetchone()
        conn.close()
        if row:
            return {
                'id': row[0],
                'title': row[1],
                'url': row[2] or '',
                'image_url': row[3] or '',
                'retry_count': row[4],
            }
    except sqlite3.Error:
        pass
    return None


def mark_posted(news_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            'UPDATE news SET posted=1, posted_at=CURRENT_TIMESTAMP WHERE id=?',
            (news_id,)
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def increment_retry(news_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE news SET retry_count = retry_count + 1 WHERE id=?', (news_id,))
        c.execute('SELECT retry_count FROM news WHERE id=?', (news_id,))
        row = c.fetchone()
        conn.commit()
        conn.close()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0


def cleanup_old_news(days=NEWS_CLEANUP_AFTER_DAYS):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "DELETE FROM news WHERE posted=1 AND saved_at < datetime('now', ?)",
            (f'-{days} days',)
        )
        deleted = c.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            logger.info(f"🧹 تم حذف {deleted} خبر قديم")
    except sqlite3.Error:
        pass
# =====================================================
# جلب الأخبار
# =====================================================
def is_iraqi_news(title):
    if not title:
        return False
    title_lower = title.lower()
    for kw in IRAQ_KEYWORDS:
        if kw.lower() in title_lower:
            return True
    return False


def extract_image_from_entry(entry, base_url=''):
    try:
        if hasattr(entry, 'media_content') and entry.media_content:
            url = entry.media_content[0].get('url', '')
            if url:
                return urljoin(base_url, url)
        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            url = entry.media_thumbnail[0].get('url', '')
            if url:
                return urljoin(base_url, url)
        if hasattr(entry, 'enclosures') and entry.enclosures:
            for enc in entry.enclosures:
                if enc.get('type', '').startswith('image/'):
                    url = enc.get('href') or enc.get('url', '')
                    if url:
                        return urljoin(base_url, url)
        content = ''
        if hasattr(entry, 'content') and entry.content:
            content = entry.content[0].get('value', '') if entry.content else ''
        if not content and hasattr(entry, 'summary'):
            content = entry.summary or ''
        if not content and hasattr(entry, 'description'):
            content = entry.description or ''
        if content:
            try:
                soup = BeautifulSoup(content, 'html.parser')
                img = soup.find('img')
                if img and img.get('src'):
                    return urljoin(base_url, img['src'])
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"خطأ في استخراج الصورة: {e}")
    return ''


def fetch_single_source(source_url):
    saved_count = 0
    iraqi_count = 0
    total_count = 0
    try:
        resp = requests.get(source_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if not feed.entries:
            return source_url, 0, 0, 0, 'لا توجد مقالات'
        for entry in feed.entries[:30]:
            total_count += 1
            title = getattr(entry, 'title', '').strip()
            if not title or len(title) < 10:
                continue
            if not is_iraqi_news(title):
                continue
            iraqi_count += 1
            url = getattr(entry, 'link', '')
            image_url = extract_image_from_entry(entry, base_url=source_url)
            if save_news(title, url, image_url):
                saved_count += 1
        return source_url, saved_count, iraqi_count, total_count, 'ok'
    except requests.RequestException as e:
        status = ''
        if hasattr(e, 'response') and e.response is not None:
            status = f'HTTP {e.response.status_code}'
        else:
            status = type(e).__name__
        return source_url, 0, 0, 0, status
    except Exception as e:
        return source_url, 0, 0, 0, f'خطأ: {type(e).__name__}'


def fetch_news_parallel():
    total_saved = 0
    sources_ok = 0
    sources_total = len(NEWS_SOURCES)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_single_source, s): s for s in NEWS_SOURCES}
        for fut in as_completed(futures):
            try:
                source_url, saved, iraqi, total, status = fut.result()
                short = source_url.split('//', 1)[-1].split('/', 1)[0]
                if status == 'ok':
                    sources_ok += 1
                    total_saved += saved
                    if saved > 0:
                        logger.info(f"  ✅ {short:30s} → {saved:3d} جديد ({iraqi}/{total} عراقي)")
                else:
                    logger.debug(f"  ❌ {short:30s} → {status}")
            except Exception as e:
                logger.debug(f"خطأ في معالجة نتيجة جلب: {e}")
    if total_saved > 0:
        logger.info(f"📥 إجمالي: {total_saved} خبر جديد من {sources_ok}/{sources_total} مصدر")
    return total_saved


# =====================================================
# تحميل الصور
# =====================================================
def download_image(image_url):
    if not image_url:
        return None
    try:
        resp = requests.get(image_url, headers=IMAGE_HEADERS, timeout=REQUEST_TIMEOUT, stream=True)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        if img.width < 200 or img.height < 200:
            return None
        return img
    except Exception as e:
        logger.debug(f"فشل تحميل الصورة: {e}")
        return None


# =====================================================
# دوال المساعدة للتصميم
# =====================================================
def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines, current = [], []
    for word in words:
        test = ' '.join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(' '.join(current))
            current = [word]
    if current:
        lines.append(' '.join(current))
    return lines


def fast_gradient(width, height, start_color, end_color, vertical=True):
    if vertical:
        grad = Image.linear_gradient('L').resize((width, height))
    else:
        grad = Image.linear_gradient('L').rotate(90, expand=True).resize((width, height))
    return ImageOps.colorize(grad, start_color, end_color)


def _draw_breaking_news_badge(draw, x, y, font, accent_color):
    text_ar = ar("خبر عاجل")
    padding_x, padding_y = 22, 10
    bbox = draw.textbbox((0, 0), text_ar, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    w = tw + padding_x * 2 + 30
    h = th + padding_y * 2 + 4
    try:
        draw.rounded_rectangle(
            [x, y, x + w, y + h],
            radius=h // 2,
            fill=accent_color,
            outline=(255, 255, 255),
            width=2,
        )
    except AttributeError:
        draw.rectangle(
            [x, y, x + w, y + h],
            fill=accent_color, outline=(255, 255, 255), width=2,
        )
    dot_size = 12
    dot_x = x + w - padding_x - dot_size // 2 - 4
    dot_y = y + h // 2
    draw.ellipse(
        [dot_x - dot_size // 2, dot_y - dot_size // 2,
         dot_x + dot_size // 2, dot_y + dot_size // 2],
        fill=(255, 255, 255),
    )
    inner = 5
    draw.ellipse(
        [dot_x - inner // 2, dot_y - inner // 2,
         dot_x + inner // 2, dot_y + inner // 2],
        fill=accent_color,
    )
    text_x = x + padding_x - 5
    text_y = y + (h - th) // 2 - 4
    draw.text((text_x, text_y), text_ar, font=font, fill=(255, 255, 255))
    return w


def _draw_page_logo(draw, cx, cy, radius, page_config, font):
    p = page_config
    draw.ellipse(
        [cx - radius - 3, cy - radius - 3, cx + radius + 3, cy + radius + 3],
        outline=p['deco_color'],
        width=3,
    )
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=p['bar_grad_start'],
        outline=p['border1'],
        width=2,
    )
    letter_ar = ar(p.get('logo_letter', p['name'][0] if p.get('name') else ''))
    bbox = draw.textbbox((0, 0), letter_ar, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    for dx, dy in [(-1, -1), (1, 1)]:
        draw.text(
            (cx - tw // 2 + dx, cy - th // 2 + dy - 4),
            letter_ar, font=font, fill=p['glow'],
        )
    draw.text(
        (cx - tw // 2, cy - th // 2 - 4),
        letter_ar, font=font, fill=p['bar_text'],
    )


def _draw_calendar_icon(draw, x, y, size, color):
    try:
        draw.rounded_rectangle(
            [x, y + 4, x + size, y + size],
            radius=3, outline=color, width=2,
        )
    except AttributeError:
        draw.rectangle(
            [x, y + 4, x + size, y + size],
            outline=color, width=2,
        )
    draw.line(
        [(x, y + 12), (x + size, y + 12)],
        fill=color, width=2,
    )
    draw.line([(x + 7, y), (x + 7, y + 8)], fill=color, width=2)
    draw.line([(x + size - 7, y), (x + size - 7, y + 8)], fill=color, width=2)


# =====================================================
# إنشاء صورة المنشور
# =====================================================
def create_post_image(title, image_url, page_config):
    try:
        p = page_config
        W, H = 1200, 850
        BAR = 100
        PAD = 22

        canvas = Image.new('RGB', (W, H), (0, 0, 0))

        top_bar = fast_gradient(W, BAR, p['bar_grad_start'], p['bar_grad_end'], vertical=True)
        canvas.paste(top_bar, (0, 0))
        bottom_bar = fast_gradient(W, BAR, p['bar_grad_end'], p['bar_grad_start'], vertical=True)
        canvas.paste(bottom_bar, (0, H - BAR))

        draw = ImageDraw.Draw(canvas)
        draw.rectangle([0, 0, W - 1, H - 1], outline=p['border1'], width=5)
        inner = 14
        draw.rectangle([inner, inner, W - inner, H - inner], outline=p['border2'], width=2)

        ix = PAD
        iy = BAR + PAD // 2
        iw = W - PAD * 2
        ih = H - BAR * 2 - PAD

        news_image = download_image(image_url)
        if news_image:
            news_resized = news_image.resize((iw, ih), Image.LANCZOS)
            news_resized = ImageEnhance.Contrast(news_resized).enhance(1.1)
        else:
            news_resized = fast_gradient(
                iw, ih, p['bar_grad_end'], p['overlay_color'], vertical=True
            )
            bg_draw = ImageDraw.Draw(news_resized)
            dc = p['deco_color']
            for xi in range(0, iw, 80):
                for yi in range(0, ih, 80):
                    bg_draw.ellipse([xi - 2, yi - 2, xi + 2, yi + 2], fill=dc)
            lc = tuple(max(0, c - 20) for c in p['bar_grad_start'])
            for xi in range(-ih, iw, 60):
                bg_draw.line([(xi, 0), (xi + ih, ih)], fill=lc, width=1)
            for xi in range(0, iw + ih, 60):
                bg_draw.line([(xi, 0), (xi - ih, ih)], fill=lc, width=1)

        canvas.paste(news_resized, (ix, iy))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(
            [ix - 2, iy - 2, ix + iw + 2, iy + ih + 2],
            outline=p['border2'], width=2
        )

        overlay = Image.new('RGBA', (iw, ih), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        overlay_h = ih // 2
        for i in range(overlay_h):
            alpha = int(200 * (i / overlay_h))
            ov_draw.line(
                [(0, ih - overlay_h + i), (iw, ih - overlay_h + i)],
                fill=(*p['overlay_color'], alpha)
            )
        canvas_rgba = canvas.convert('RGBA')
        overlay_full = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        overlay_full.paste(overlay, (ix, iy))
        canvas_rgba = Image.alpha_composite(canvas_rgba, overlay_full)
        canvas = canvas_rgba.convert('RGB')
        draw = ImageDraw.Draw(canvas)

        corner_size = 22
        dc = p['deco_color']
        corners = [
            (inner + 4, inner + 4),
            (W - inner - 4 - corner_size, inner + 4),
            (inner + 4, H - inner - 4 - corner_size),
            (W - inner - 4 - corner_size, H - inner - 4 - corner_size),
        ]
        for cx, cy in corners:
            draw.rectangle([cx, cy, cx + corner_size, cy + corner_size], outline=dc, width=2)
            draw.line(
                [(cx + corner_size // 2, cy), (cx + corner_size // 2, cy + corner_size)],
                fill=dc, width=1
            )
            draw.line(
                [(cx, cy + corner_size // 2), (cx + corner_size, cy + corner_size // 2)],
                fill=dc, width=1
            )

        font_badge = get_font(30)
        font_logo = get_font(44)
        badge_y = (BAR - 50) // 2 + 5
        _draw_breaking_news_badge(draw, 40, badge_y, font_badge, p.get('accent', (200, 50, 50)))

        logo_radius = 32
        logo_cx = W - 70
        logo_cy = BAR // 2
        _draw_page_logo(draw, logo_cx, logo_cy, logo_radius, p, font_logo)

        draw.line(
            [(inner + 30, BAR - 1), (W - inner - 30, BAR - 1)],
            fill=p['deco_color'], width=1
        )

        font_title = get_font(40)
        title_ar = ar(title)
        max_title_w = W - PAD * 4
        title_lines = wrap_text(title_ar, font_title, max_title_w, draw)
        if len(title_lines) > 3:
            title_lines = title_lines[:3]
            title_lines[-1] = title_lines[-1][:30] + '...'

        line_height = 56
        total_h = len(title_lines) * line_height
        title_y_start = H - BAR - PAD - total_h - 20

        draw.line(
            [(ix + 100, title_y_start - 15), (W - ix - 100, title_y_start - 15)],
            fill=p['deco_color'], width=2
        )

        title_y = title_y_start
        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=font_title)
            tw = bbox[2] - bbox[0]
            tx = W // 2 - tw // 2
            for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
                draw.text((tx + dx, title_y + dy), line, font=font_title, fill=(0, 0, 0))
            draw.text((tx, title_y), line, font=font_title, fill=(255, 255, 255))
            title_y += line_height

        font_date = get_font(26)
        font_page = get_font(38)

        draw.line(
            [(inner + 30, H - BAR + 1), (W - inner - 30, H - BAR + 1)],
            fill=p['deco_color'], width=1
        )

        date_str = ar(format_arabic_date())
        bbox = draw.textbbox((0, 0), date_str, font=font_date)
        date_h = bbox[3] - bbox[1]

        cal_size = 28
        cal_x = 40
        cal_y = H - BAR // 2 - cal_size // 2
        _draw_calendar_icon(draw, cal_x, cal_y, cal_size, p['bar_text'])

        date_x = cal_x + cal_size + 12
        date_y = H - BAR // 2 - date_h // 2 - 6
        draw.text((date_x, date_y), date_str, font=font_date, fill=p['bar_text'])

        page_name_ar = ar(p['name'])
        bbox = draw.textbbox((0, 0), page_name_ar, font=font_page)
        pn_w = bbox[2] - bbox[0]
        pn_h = bbox[3] - bbox[1]

        pn_x = W - 50 - pn_w
        pn_y = H - BAR // 2 - pn_h // 2 - 6

        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
            draw.text((pn_x + dx, pn_y + dy), page_name_ar,
                      font=font_page, fill=p['glow'])
        draw.text((pn_x, pn_y), page_name_ar,
                  font=font_page, fill=p['bar_text'])

        draw.line(
            [(pn_x - 25, H - BAR // 2), (pn_x - 8, H - BAR // 2)],
            fill=p['deco_color'], width=2
        )

        return canvas

    except Exception as e:
        logger.error(f"خطأ في إنشاء الصورة: {e}")
        logger.debug(traceback.format_exc())
        return None
# =====================================================
# النشر على فيسبوك
# =====================================================
def post_to_facebook(news, page_key):
    try:
        p = PAGES_CONFIG[page_key]
        token = p['token']
        page_id = p['page_id']
        page_name = p['name']

        if not token:
            logger.warning(f"⚠️ {page_name}: لا يوجد توكن")
            return False

        img = create_post_image(news['title'], news['image_url'], p)
        if img is None:
            logger.warning(f"⚠️ فشل إنشاء الصورة لـ {page_name}")
            return False

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=IMAGE_QUALITY, optimize=True)
        buf.seek(0)

        files = {'source': ('post.jpg', buf, 'image/jpeg')}
        data = {
            'caption': news['title'],
            'access_token': token,
        }

        url = f'https://graph.facebook.com/v18.0/{page_id}/photos'
        resp = requests.post(url, files=files, data=data, timeout=FB_POST_TIMEOUT)

        if resp.status_code == 200:
            result = resp.json()
            if 'id' in result:
                return True
            else:
                logger.warning(f"⚠️ {page_name}: رد فيسبوك غير متوقع: {result}")
                return False
        else:
            try:
                err = resp.json().get('error', {})
                logger.warning(
                    f"⚠️ فيسبوك رفض النشر على {page_name}: "
                    f"{resp.status_code} - {resp.text[:300]}"
                )
            except Exception:
                logger.warning(f"⚠️ فيسبوك رفض النشر على {page_name}: {resp.status_code}")
            return False

    except requests.RequestException as e:
        logger.warning(f"⚠️ خطأ شبكة في النشر على {page_key}: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ خطأ غير متوقع في post_to_facebook({page_key}): {e}")
        logger.debug(traceback.format_exc())
        return False


def publish_news(news):
    success_count = 0
    active_pages = [k for k, p in PAGES_CONFIG.items() if p['token']]
    if not active_pages:
        logger.error("❌ لا توجد صفحات بتوكن صالح!")
        return 0

    for page_key in active_pages:
        if _shutdown_requested:
            logger.info("⛔ إيقاف النشر بسبب طلب الإيقاف")
            break
        if post_to_facebook(news, page_key):
            success_count += 1

    return success_count


# =====================================================
# الحلقة الرئيسية
# =====================================================
def main_loop(once=False, max_posts=None):
    cycle = 0
    posts_count = 0

    while not _shutdown_requested:
        cycle += 1
        try:
            if cycle == 1 or cycle % FETCH_INTERVAL_CYCLES == 0:
                fetch_news_parallel()

            if cycle % CLEANUP_INTERVAL_CYCLES == 0:
                cleanup_old_news()

            news = get_unposted()
            if news:
                logger.info(
                    f"📝 نشر: \"{news['title'][:60]}...\" "
                    f"(محاولة {news['retry_count'] + 1}/{MAX_RETRIES_PER_NEWS})"
                )
                success_count = publish_news(news)

                if success_count > 0:
                    mark_posted(news['id'])
                    posts_count += 1
                    active_pages = sum(1 for p in PAGES_CONFIG.values() if p['token'])
                    logger.info(f"  ✅ نُشر على {success_count}/{active_pages} صفحات")
                else:
                    new_retry = increment_retry(news['id'])
                    if new_retry >= MAX_RETRIES_PER_NEWS:
                        logger.warning(f"  ❌ تخلي عن الخبر بعد {new_retry} محاولات")
                        mark_posted(news['id'])
                    else:
                        logger.info(f"  🔁 إعاد لاحقاً (محاولة {new_retry})")

                if max_posts and posts_count >= max_posts:
                    logger.info(f"🎯 وصل الحد الأقصى للمنشورات: {max_posts}")
                    break

            if once and not news:
                logger.info("✅ لا أخبار جديدة - وضع --once، خروج")
                break

            if not _shutdown_requested:
                time.sleep(LOOP_DELAY_SECONDS)

        except KeyboardInterrupt:
            logger.info("⛔ إيقاف يدوي")
            break
        except Exception as e:
            logger.error(f"❌ خطأ في الحلقة الرئيسية: {e}")
            logger.debug(traceback.format_exc())
            time.sleep(LOOP_DELAY_SECONDS)

    logger.info(f"📊 ملخص: {posts_count} منشور | 0 تخطي | {cycle * LOOP_DELAY_SECONDS:.1f}s")


def verify_sources():
    logger.info("🔍 وضع فحص المصادر - لن يتم النشر")
    working = 0
    total_iraqi = 0
    for source_url in NEWS_SOURCES:
        source_url, saved, iraqi, total, status = fetch_single_source(source_url)
        short = source_url.split('//', 1)[-1].split('/', 1)[0]
        if status == 'ok':
            working += 1
            total_iraqi += iraqi
            logger.info(f"  ✅ {short:30s} → {iraqi}/{total} عراقية")
        else:
            logger.info(f"  ❌ {short:30s} → {status}")
    logger.info("=" * 60)
    logger.info(f"📊 النتيجة: {working}/{len(NEWS_SOURCES)} مصدر يعمل | {total_iraqi} خبر عراقي مرشّح")


def show_startup_banner(once=False, verify_only=False):
    logger.info("=" * 60)
    logger.info("🚀 نظام نشر الأخبار - النسخة 25 (GitHub Actions Ready)")
    if verify_only:
        logger.info("📌 وضع: فحص المصادر (--verify-sources)")
    elif once:
        logger.info("📌 وضع: دورة واحدة (--once)")
    else:
        logger.info("📌 وضع: حلقة لا نهائية")
    logger.info(f"📰 المصادر: {len(NEWS_SOURCES)} مصدر إخباري")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='نظام نشر الأخبار العراقية')
    parser.add_argument('--once', action='store_true', help='دورة واحدة فقط ثم خروج')
    parser.add_argument('--max-posts', type=int, default=None, help='الحد الأقصى للمنشورات')
    parser.add_argument('--verify-sources', action='store_true', help='فحص المصادر بدون نشر')
    args = parser.parse_args()

    show_startup_banner(once=args.once, verify_only=args.verify_sources)

    if args.verify_sources:
        verify_sources()
        return

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    db_attempts = 0
    while db_attempts < MAX_DB_INIT_ATTEMPTS:
        if init_db():
            break
        db_attempts += 1
        logger.warning(f"⚠️ فشل تهيئة DB (محاولة {db_attempts}/{MAX_DB_INIT_ATTEMPTS})")
        time.sleep(2)
    else:
        logger.error("❌ فشل تهيئة قاعدة البيانات نهائياً")
        sys.exit(1)

    active_pages = [(k, p['name']) for k, p in PAGES_CONFIG.items() if p['token']]
    logger.info(f"⏰ بدء التشغيل: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📋 صفحات مفعّلة ({len(active_pages)}): " +
                ', '.join(k for k, _ in active_pages))

    if not active_pages:
        logger.error("❌ لا توجد صفحات بتوكن صالح! تحقق من Secrets")
        sys.exit(1)

    max_posts_val = args.max_posts
    if max_posts_val is None and args.once:
        max_posts_val = 10

    if args.once:
        logger.info(f"⏰ وضع الدورة الواحدة - حد أقصى {max_posts_val} منشور")

    main_loop(once=args.once, max_posts=max_posts_val)


if __name__ == '__main__':
    main()
