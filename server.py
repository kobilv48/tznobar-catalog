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
from io import BytesIO
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

PORT = int(os.environ.get('PORT', '8080'))
PRODUCTS_FILE = 'products.json'


def _rtl(text):
    """Naive RTL helper for reportlab text drawing."""
    if not text:
        return ''
    return str(text)[::-1]


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


def generate_catalog_pdf(products, export_all=True, selected_category=None, pdf_mode='fast'):
    """Create a catalog PDF in-memory and return raw bytes."""
    if not products:
        raise ValueError('No products to export')

    font_name = _pick_font_name()
    buf = BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buf, pagesize=A4)
    image_cache = {}

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
    subtitle = f"{len(products)} מוצרים" + (f" | קטגוריה: {selected_category}" if (not export_all and selected_category) else '')
    c.drawCentredString(page_w / 2, page_h - 345, _rtl(subtitle))
    c.drawCentredString(page_w / 2, page_h - 365, datetime.now().strftime('%Y-%m-%d %H:%M'))
    c.showPage()

    # Grid layout tuning
    compact = (pdf_mode == 'fast') or len(products) > 260
    cols = 5 if compact else 4
    margin_x = 18
    top_y = page_h - 70
    gap = 8 if compact else 12
    card_w = (page_w - (2 * margin_x) - (gap * (cols - 1))) / cols
    img_h = 82 if compact else 102
    txt_h = 30 if compact else 38
    card_h = img_h + txt_h + 8
    row_h = card_h + gap
    max_rows = int((top_y - 28) // row_h)
    rows_per_page = max(1, max_rows)
    page_capacity = cols * rows_per_page
    include_images = (pdf_mode == 'quality') or len(products) <= 260

    for category, items in grouped.items():
        for page_idx in range(0, len(items), page_capacity):
            chunk = items[page_idx:page_idx + page_capacity]

            # Page header
            c.setFillColor(colors.HexColor('#2c5530'))
            c.rect(0, page_h - 52, page_w, 52, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont(font_name, 16)
            header = category if page_idx == 0 else f'{category} (המשך)'
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
                                reader = image_cache.get(local_img)
                                if reader is None:
                                    reader = ImageReader(local_img)
                                    image_cache[local_img] = reader
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

                # Product name
                c.setFillColor(colors.HexColor('#1f1f1f'))
                name = (product.get('name') or '').strip()
                if len(name) > 28:
                    name = name[:27] + '…'
                c.drawCentredString(x + card_w / 2, y + 12, _rtl(name))

            c.showPage()

    c.save()
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes

class CatalogHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
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
        self.send_json(404, {'error': 'Not found'})

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
        try:
            content_length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(content_length) if content_length > 0 else b'{}'
            payload = json.loads(raw.decode('utf-8')) if raw else {}

            export_all = bool(payload.get('exportAll', True))
            selected_category = payload.get('selectedCategory')
            pdf_mode = payload.get('pdfMode', 'fast')

            with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                all_products = json.load(f)

            if not isinstance(all_products, list):
                raise ValueError('Invalid products payload')

            if export_all:
                products = all_products
            else:
                products = [p for p in all_products if p.get('category') == selected_category]

            pdf_bytes = generate_catalog_pdf(
                products=products,
                export_all=export_all,
                selected_category=selected_category,
                pdf_mode=pdf_mode,
            )

            filename = 'catalog.pdf' if export_all else 'catalog-category.pdf'
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)
        except Exception as e:
            print(f"PDF generation error: {e}", file=sys.stderr)
            self.send_json(500, {'error': str(e)})

    def log_message(self, format, *args):
        # Only log API calls, not static file requests
        if '/api/' in (args[0] if args else ''):
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
    server = http.server.HTTPServer(('', PORT), CatalogHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
