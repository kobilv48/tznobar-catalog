#!/usr/bin/env python3
"""Simple HTTP server with Google image search proxy endpoint."""

import http.server
import json
import urllib.request
import urllib.parse
import html as html_lib
import re
import os
import sys

PORT = int(os.environ.get('PORT', '8080'))

class CatalogHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # API endpoint: /api/image-search?q=...
        if self.path.startswith('/api/image-search?'):
            self.handle_image_search()
            return
        # Serve static files normally
        super().do_GET()

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
