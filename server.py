#!/usr/bin/env python3
"""Simple HTTP server with image search and server-side PDF generation."""

import http.server
import json
import urllib.request
import urllib.parse
import html as html_lib
import re
import os
import sys
import gc
import threading
import concurrent.futures
from datetime import datetime
import tempfile
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image, ImageOps

try:
    from bidi.algorithm import get_display as bidi_get_display
except Exception:
    bidi_get_display = None

PORT = int(os.environ.get('PORT', '8080'))
PRODUCTS_FILE = 'products.json'

# Supabase persistence (optional). When configured, the catalog is read from
# and written to Supabase so edits are permanent across all devices. The
# service_role key is SECRET and must only live in server env vars.
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
# PIN required to add/edit/delete. Defaults to the catalog's access PIN.
EDIT_PIN = os.environ.get('EDIT_PIN', '4423')

SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
PRODUCT_FIELDS = ('id', 'name', 'category', 'image', 'page', 'description')


def supabase_request(method, path, body=None, extra_headers=None):
    """Call the Supabase REST API with the service key. Returns parsed JSON (or [])."""
    if not SUPABASE_ENABLED:
        raise RuntimeError('Supabase is not configured')

    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        'apikey': SUPABASE_SERVICE_KEY,
        'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
        'Content-Type': 'application/json',
    }
    if extra_headers:
        headers.update(extra_headers)

    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode('utf-8', errors='ignore')
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def supabase_list_products():
    """Return all products ordered by id."""
    return supabase_request('GET', 'products?select=*&order=id.asc')


def supabase_next_id():
    """Compute the next product id (max id + 1)."""
    rows = supabase_request('GET', 'products?select=id&order=id.desc&limit=1')
    if rows and isinstance(rows, list):
        try:
            return int(rows[0]['id']) + 1
        except Exception:
            pass
    return 1


def _rtl(text):
    """RTL helper for reportlab text drawing."""
    if not text:
        return ''
    value = str(text)
    if bidi_get_display is not None:
        try:
            return bidi_get_display(value)
        except Exception:
            pass
    # Fallback for environments without python-bidi.
    return value[::-1]


def _pick_font_name():
    """Try to register a Hebrew-capable font, fallback to Helvetica."""
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/dejavu/DejaVuSans.ttf',
        '/Library/Fonts/Arial Unicode.ttf',
        '/Library/Fonts/Arial.ttf',
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('CatalogHebrew', path))
                return 'CatalogHebrew'
            except Exception:
                pass
    return 'Helvetica'


# Cache of compressed JPEG thumbnails, keyed by (path, mtime, max_w_px, max_h_px).
# Persists across requests in the long-running server process, so repeated catalog
# exports reuse already-processed images instead of re-decoding them every time.
_THUMB_CACHE = {}
_THUMB_CACHE_LOCK = threading.Lock()


def _thumb_jpeg_bytes(path, target_w_pt, target_h_pt):
    """Return compressed JPEG bytes for an image, cached by path + mtime + size."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    max_w_px = max(140, int(target_w_pt * 1.7))
    max_h_px = max(140, int(target_h_pt * 1.7))
    key = (path, mtime, max_w_px, max_h_px)
    cached = _THUMB_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with Image.open(path) as img:
            # Ask the JPEG decoder to downscale while decoding so huge source
            # photos never fully expand in RAM (critical on memory-limited hosts).
            try:
                img.draft('RGB', (max_w_px, max_h_px))
            except Exception:
                pass
            img = ImageOps.exif_transpose(img)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.thumbnail((max_w_px, max_h_px), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=58, optimize=True)
            data = buf.getvalue()
    except Exception:
        return None
    with _THUMB_CACHE_LOCK:
        _THUMB_CACHE[key] = data
    return data


def generate_catalog_pdf(products, output_path, export_all=True, selected_category=None, pdf_mode='fast'):
    """Create a catalog PDF on disk to keep RAM usage low."""
    if not products:
        raise ValueError('No products to export')

    font_name = _pick_font_name()
    page_w, page_h = A4
    c = canvas.Canvas(output_path, pagesize=A4)

    # Group products by category
    grouped = {}
    for p in products:
        category = p.get('category') or 'ללא קטגוריה'
        grouped.setdefault(category, []).append(p)

    # Cover page
    c.setFillColor(colors.HexColor('#1f4a27'))
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)
    c.setFillColor(colors.white)
    logo_path = 'logo-white.png'
    if os.path.exists(logo_path):
        try:
            c.drawImage(logo_path, (page_w - 180) / 2, page_h - 280, width=180, height=180, preserveAspectRatio=True, mask='auto')
        except Exception:
            pass
    c.setFont(font_name, 28)
    c.drawCentredString(page_w / 2, page_h - 320, _rtl('קטלוג מוצרים'))
    c.setFont(font_name, 12)
    subtitle = (f"קטגוריה: {selected_category}" if (not export_all and selected_category) else '')
    c.drawCentredString(page_w / 2, page_h - 345, _rtl(subtitle))
    c.drawCentredString(page_w / 2, page_h - 365, datetime.now().strftime('%Y-%m-%d'))
    c.showPage()

    # Grid layout tuning
    compact = (pdf_mode == 'fast') or len(products) > 260
    cols = 3
    margin_x = 18
    top_y = page_h - 70
    gap = 8 if compact else 12
    card_w = (page_w - (2 * margin_x) - (gap * (cols - 1))) / cols
    img_h = 150 if compact else 180
    txt_h = 50 if compact else 58
    card_h = img_h + txt_h + 8
    row_h = card_h + gap
    max_rows = int((top_y - 28) // row_h)
    rows_per_page = max(1, max_rows)
    page_capacity = cols * rows_per_page
    # Per request: always include images in the generated PDF.
    include_images = True

    def split_name_lines(name, max_width_pt, font_size, max_lines=4):
        """Word-wrap a name to fit the card width, showing the full name (no truncation)."""
        words = name.split()
        if not words:
            return ['']

        def fits(text):
            return pdfmetrics.stringWidth(_rtl(text), font_name, font_size) <= max_width_pt

        lines = []
        current = ''
        for word in words:
            candidate = f"{current} {word}".strip()
            if fits(candidate) or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)

        return lines[:max_lines]

    def build_image_reader(local_img_path, target_w_pt, target_h_pt):
        """Return a reportlab ImageReader from a cached, compressed thumbnail."""
        data = _thumb_jpeg_bytes(local_img_path, target_w_pt, target_h_pt)
        if not data:
            return None
        return ImageReader(BytesIO(data))

    # Pre-process every image in parallel to warm the thumbnail cache before drawing.
    # PIL releases the GIL during decode/resize/encode, so threads give a real speedup,
    # and the cache makes subsequent exports of the same catalog near-instant.
    if include_images:
        tw, th = card_w - 12, img_h - 4
        seen_paths = set()
        for _items in grouped.values():
            for _product in _items:
                _src = _product.get('image', '')
                if _src and isinstance(_src, str) and not _src.startswith('data:image'):
                    _local = _src.lstrip('/')
                    if _local not in seen_paths and os.path.exists(_local):
                        seen_paths.add(_local)
        if seen_paths:
            # Keep parallelism low so peak memory (each worker decodes one image)
            # stays well within the host's limit. On constrained hosts the CPU is
            # tiny anyway, so more threads wouldn't help but would spike RAM.
            workers = min(2, (os.cpu_count() or 1))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(lambda p: _thumb_jpeg_bytes(p, tw, th), seen_paths))

    for category, items in grouped.items():
        for page_idx in range(0, len(items), page_capacity):
            chunk = items[page_idx:page_idx + page_capacity]

            # Page header
            c.setFillColor(colors.HexColor('#2c5530'))
            c.rect(0, page_h - 52, page_w, 52, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont(font_name, 16)
            header = category if page_idx == 0 else f'המשך - {category}'
            c.drawCentredString(page_w / 2, page_h - 32, _rtl(header))

            c.setFillColor(colors.black)
            c.setFont(font_name, 9 if compact else 10)

            for i, product in enumerate(chunk):
                row = i // cols
                col = i % cols
                x = margin_x + col * (card_w + gap)
                y = top_y - (row + 1) * row_h

                # Card background
                c.setFillColor(colors.white)
                c.roundRect(x, y, card_w, card_h, 5, fill=1, stroke=0)
                c.setStrokeColor(colors.HexColor('#e0e0e0'))
                c.roundRect(x, y, card_w, card_h, 5, fill=0, stroke=1)

                # Image area
                c.setFillColor(colors.HexColor('#f6f6f6'))
                c.rect(x + 4, y + txt_h + 4, card_w - 8, img_h, fill=1, stroke=0)

                if include_images:
                    img_src = product.get('image', '')
                    if img_src and isinstance(img_src, str) and not img_src.startswith('data:image'):
                        local_img = img_src.lstrip('/')
                        if os.path.exists(local_img):
                            try:
                                reader = build_image_reader(local_img, card_w - 12, img_h - 4)
                                if reader is not None:
                                    c.drawImage(
                                        reader,
                                        x + 6,
                                        y + txt_h + 6,
                                        card_w - 12,
                                        img_h - 4,
                                        preserveAspectRatio=True,
                                        anchor='c',
                                        mask='auto'
                                    )
                            except Exception:
                                pass

                # Product name (full name, word-wrapped to the card width)
                c.setFillColor(colors.HexColor('#1f1f1f'))
                name = (product.get('name') or '').strip()
                name_font = 8.2 if compact else 9.2
                c.setFont(font_name, name_font)
                lines = split_name_lines(name, card_w - 10, name_font, max_lines=4)
                line_gap = 9.0 if compact else 10.0
                n = len(lines)
                start_y = y + txt_h / 2 + ((n - 1) * line_gap) / 2 - line_gap / 2 + 2
                for idx, ln in enumerate(lines):
                    c.drawCentredString(x + card_w / 2, start_y - idx * line_gap, _rtl(ln))

            c.showPage()

    c.save()
    # Reclaim decode/render buffers promptly so steady-state RAM stays low on
    # memory-limited hosts (free tier is 512MB).
    gc.collect()

class CatalogHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # API endpoint: /api/products (catalog data, persistent when Supabase is on)
        if self.path.split('?', 1)[0] == '/api/products':
            self.handle_get_products()
            return
        # API endpoint: /api/image-search?q=...
        if self.path.startswith('/api/image-search?'):
            self.handle_image_search()
            return
        # API endpoint: /api/generate-pdf
        if self.path == '/api/generate-pdf':
            self.send_json(405, {'error': 'Use POST for /api/generate-pdf'})
            return
        # Serve static files normally
        super().do_GET()

    def do_POST(self):
        if self.path == '/api/generate-pdf':
            self.handle_generate_pdf()
            return
        if self.path.split('?', 1)[0] == '/api/products':
            self.handle_create_product()
            return
        self.send_json(404, {'error': 'Not found'})

    def do_PUT(self):
        if self.path.split('?', 1)[0].startswith('/api/products/'):
            self.handle_update_product()
            return
        self.send_json(404, {'error': 'Not found'})

    def do_DELETE(self):
        if self.path.split('?', 1)[0].startswith('/api/products/'):
            self.handle_delete_product()
            return
        self.send_json(404, {'error': 'Not found'})

    # ---------- Products API ----------

    def _read_json_body(self):
        content_length = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            return json.loads(raw.decode('utf-8')) if raw else {}
        except Exception:
            return {}

    def _check_pin(self, payload):
        """Validate the edit PIN from header or body. Returns True if allowed."""
        pin = self.headers.get('X-Edit-Pin') or (payload.get('pin') if isinstance(payload, dict) else None)
        return str(pin) == str(EDIT_PIN)

    def _path_id(self):
        """Extract the numeric id from /api/products/<id>."""
        tail = self.path.split('?', 1)[0].rsplit('/', 1)[-1]
        try:
            return int(tail)
        except (TypeError, ValueError):
            return None

    def _clean_product(self, data):
        return {k: data.get(k) for k in PRODUCT_FIELDS if k in data}

    def handle_get_products(self):
        # Prefer Supabase (persistent). Fall back to products.json (read-only).
        if SUPABASE_ENABLED:
            try:
                products = supabase_list_products()
                self.send_json(200, {'products': products, 'source': 'supabase'})
                return
            except Exception as e:
                print(f"Supabase read failed, falling back to file: {e}", file=sys.stderr)
        try:
            with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                products = json.load(f)
            self.send_json(200, {'products': products, 'source': 'file'})
        except Exception as e:
            self.send_json(500, {'error': str(e)})

    def handle_create_product(self):
        payload = self._read_json_body()
        if not self._check_pin(payload):
            self.send_json(403, {'error': 'PIN שגוי'})
            return
        if not SUPABASE_ENABLED:
            self.send_json(503, {'error': 'אחסון מתמיד לא מוגדר בשרת'})
            return
        product = self._clean_product(payload.get('product', payload))
        if not product.get('name'):
            self.send_json(400, {'error': 'חסר שם מוצר'})
            return
        try:
            product['id'] = supabase_next_id()
            created = supabase_request(
                'POST', 'products', body=product,
                extra_headers={'Prefer': 'return=representation'},
            )
            row = created[0] if isinstance(created, list) and created else product
            self.send_json(201, {'product': row})
        except Exception as e:
            print(f"Create product failed: {e}", file=sys.stderr)
            self.send_json(500, {'error': str(e)})

    def handle_update_product(self):
        payload = self._read_json_body()
        if not self._check_pin(payload):
            self.send_json(403, {'error': 'PIN שגוי'})
            return
        if not SUPABASE_ENABLED:
            self.send_json(503, {'error': 'אחסון מתמיד לא מוגדר בשרת'})
            return
        pid = self._path_id()
        if pid is None:
            self.send_json(400, {'error': 'מזהה מוצר לא תקין'})
            return
        update = self._clean_product(payload.get('product', payload))
        update.pop('id', None)
        update['updated_at'] = datetime.now().isoformat()
        try:
            updated = supabase_request(
                'PATCH', f'products?id=eq.{pid}', body=update,
                extra_headers={'Prefer': 'return=representation'},
            )
            row = updated[0] if isinstance(updated, list) and updated else None
            if row is None:
                self.send_json(404, {'error': 'המוצר לא נמצא'})
                return
            self.send_json(200, {'product': row})
        except Exception as e:
            print(f"Update product failed: {e}", file=sys.stderr)
            self.send_json(500, {'error': str(e)})

    def handle_delete_product(self):
        payload = self._read_json_body()
        if not self._check_pin(payload):
            self.send_json(403, {'error': 'PIN שגוי'})
            return
        if not SUPABASE_ENABLED:
            self.send_json(503, {'error': 'אחסון מתמיד לא מוגדר בשרת'})
            return
        pid = self._path_id()
        if pid is None:
            self.send_json(400, {'error': 'מזהה מוצר לא תקין'})
            return
        try:
            supabase_request('DELETE', f'products?id=eq.{pid}')
            self.send_json(200, {'deleted': pid})
        except Exception as e:
            print(f"Delete product failed: {e}", file=sys.stderr)
            self.send_json(500, {'error': str(e)})

    def handle_image_search(self):
        # Parse query parameter
        query_string = self.path.split('?', 1)[1] if '?' in self.path else ''
        params = urllib.parse.parse_qs(query_string)
        query = params.get('q', [''])[0]

        if not query:
            self.send_json(400, {'error': 'Missing query parameter q'})
            return

        try:
            images = search_google_images(query)
            self.send_json(200, {'images': images})
        except Exception as e:
            print(f"Image search error: {e}", file=sys.stderr)
            self.send_json(500, {'error': str(e)})

    def send_json(self, code, data):
        response = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(response))
        self.end_headers()
        self.wfile.write(response)

    def handle_generate_pdf(self):
        tmp_path = None
        try:
            content_length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(content_length) if content_length > 0 else b'{}'
            payload = json.loads(raw.decode('utf-8')) if raw else {}

            export_all = bool(payload.get('exportAll', True))
            selected_category = payload.get('selectedCategory')
            pdf_mode = payload.get('pdfMode', 'fast')

            # Use products from request if provided (reflects user edits),
            # otherwise fall back to products.json on disk.
            all_products = payload.get('products')
            if not all_products or not isinstance(all_products, list):
                with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                    all_products = json.load(f)

            if not isinstance(all_products, list):
                raise ValueError('Invalid products payload')

            if export_all:
                products = all_products
            else:
                products = [p for p in all_products if p.get('category') == selected_category]

            with tempfile.NamedTemporaryFile(prefix='catalog_', suffix='.pdf', delete=False) as tmp:
                tmp_path = tmp.name

            generate_catalog_pdf(
                products=products,
                output_path=tmp_path,
                export_all=export_all,
                selected_category=selected_category,
                pdf_mode=pdf_mode,
            )

            filename = 'catalog.pdf' if export_all else 'catalog-category.pdf'
            file_size = os.path.getsize(tmp_path)
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(file_size))
            self.end_headers()

            with open(tmp_path, 'rb') as pdf_file:
                while True:
                    chunk = pdf_file.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            print(f"PDF generation error: {e}", file=sys.stderr)
            self.send_json(500, {'error': str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def log_message(self, format, *args):
        # Only log API calls, not static file requests.
        first = args[0] if args else ''
        if isinstance(first, str) and '/api/' in first:
            super().log_message(format, *args)


def search_google_images(query):
    """Search Bing Images and return direct image URLs."""
    encoded = urllib.parse.quote(query)
    url = f"https://www.bing.com/images/search?q={encoded}&first=1"

    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html',
    })

    with urllib.request.urlopen(req, timeout=10) as resp:
        page = resp.read().decode('utf-8', errors='ignore')

    images = []
    # Bing encodes image metadata in m="" attributes as HTML entities
    m_data = re.findall(r'm="(\{[^"]*\})"', page)
    for m_raw in m_data:
        try:
            decoded = html_lib.unescape(m_raw)
            data = json.loads(decoded)
            murl = data.get('murl', '')
            if murl and murl.startswith('http'):
                images.append(murl)
                if len(images) >= 8:
                    break
        except Exception:
            pass

    return images


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"Serving catalog on port {PORT}")
    print(f"Local URL: http://localhost:{PORT}")
    print(f"Image search API: /api/image-search?q=...")
    # Threading server so a slow request (e.g. PDF generation) doesn't block
    # other requests — including Render's health check, which otherwise times
    # out and triggers a "server failure" alert.
    server = http.server.ThreadingHTTPServer(('', PORT), CatalogHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
