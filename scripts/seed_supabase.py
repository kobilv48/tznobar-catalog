#!/usr/bin/env python3
"""Seed the Supabase `products` table from products.json (run once).

Usage:
    export SUPABASE_URL="https://xxxx.supabase.co"
    export SUPABASE_SERVICE_KEY="eyJ...service_role..."
    python3 scripts/seed_supabase.py

The service_role key is SECRET. Never commit it or paste it anywhere public.
"""

import json
import os
import sys
import urllib.request
import urllib.error

PRODUCTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'products.json')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
BATCH_SIZE = 200


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    if not SUPABASE_URL or not SERVICE_KEY:
        fail("Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables first.")

    with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
        products = json.load(f)

    if not isinstance(products, list) or not products:
        fail("products.json is empty or invalid.")

    rows = []
    for p in products:
        rows.append({
            'id': p.get('id'),
            'name': p.get('name', ''),
            'category': p.get('category'),
            'image': p.get('image'),
            'page': p.get('page'),
            'description': p.get('description'),
        })

    endpoint = f"{SUPABASE_URL}/rest/v1/products"
    headers = {
        'apikey': SERVICE_KEY,
        'Authorization': f'Bearer {SERVICE_KEY}',
        'Content-Type': 'application/json',
        # Upsert so re-running is safe.
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }

    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        data = json.dumps(batch, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(endpoint, data=data, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
                total += len(batch)
                print(f"Upserted {total}/{len(rows)}")
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='ignore')
            fail(f"HTTP {e.code} on batch starting {i}: {body}")
        except Exception as e:
            fail(f"Request failed on batch starting {i}: {e}")

    print(f"Done. Seeded {total} products into Supabase.")


if __name__ == '__main__':
    main()
