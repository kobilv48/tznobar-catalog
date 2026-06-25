#!/usr/bin/env python3
"""One-off: append the 21 newly identified products to local JSON files
and upsert ONLY these new products to Supabase (no full-file seed)."""
import json
import os
import urllib.request

SUPABASE_URL = "https://rvylntwustjajvvwpbzx.supabase.co"
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

PAGE = 109  # next catalog page after existing max (108)

# (id, image_index, name, category)
NEW = [
    (856, "001", 'משקאות AriZona – מבחר טעמים 340 מ"ל', "משקאות בטעמים"),
    (857, "002", "Liron – שימורים (פלפל חריף קלוי / צלפים / בצלצלי פנינה)", "חמוצים ושימורים"),
    (858, "003", "חטיף פופקורן – מבחר טעמים", "לחמים ונשנושים"),
    (859, "004", "קינמון ציילון אורגני (אבקה 70 ג' / מקלות 50 ג')", "תבלינים"),
    (860, "005", "Vortumnus – שיני שום ועגבניות מיובשות בשמן לפתית", "שמנים ותיבול"),
    (861, "006", "קוסקוס פרידה בינוני 500 גרם", "אורז קטניות ודגנים"),
    (862, "008", 'רטבי Walden Farms – ויניגרט בלסמי / חרדל דבש 355 מ"ל', "רטבים וממרחים"),
    (863, "010", 'רטבי Walden Farms – אלף האיים / ברביקיו דבש 355 מ"ל', "רטבים וממרחים"),
    (864, "011", "GRESOS – תערובות תיבול יווניות (צזיקי / סלט יווני)", "תבלינים"),
    (865, "012", "מיצי Fontana 1 ליטר – מבחר טעמים", "משקאות"),
    (866, "014", "צנובר – תמר מג'הול 400 גרם", "פירות יבשים"),
    (867, "015", "צנובר – אגוזי מלך 200 גרם", "פירות יבשים"),
    (868, "016", "צנובר – צימוק בהיר 200 גרם", "פירות יבשים"),
    (869, "017", "צנובר – אגוזי ברזיל 150 גרם", "פירות יבשים"),
    (870, "018", "צנובר – חמוציות מסוכרות 170 גרם", "פירות יבשים"),
    (871, "019", "צנובר – צימוקים כהים 200 גרם", "פירות יבשים"),
    (872, "020", "צנובר – פיסטוק טבעי מקולף 120 גרם", "פירות יבשים"),
    (873, "021", "צנובר – אגוז קשיו טבעי 170 גרם", "פירות יבשים"),
    (874, "022", "צנובר – משמש 250 גרם", "פירות יבשים"),
    (875, "023", "צנובר – שזיף מגולען 300 גרם", "פירות יבשים"),
    (876, "024", "Holy Nuts – חמאת אגוזי נמר (שקדים ופיסטוק / קשיו וקקאו) 350 ג'", "רטבים וממרחים"),
]

records = [
    {
        "id": pid,
        "name": name,
        "category": cat,
        "image": f"images_clean/new_20260625_{idx}.jpeg",
        "page": PAGE,
    }
    for (pid, idx, name, cat) in NEW
]

# 1) Append to local JSON files (skip ids already present, idempotent)
for fname in ("products.json", "products_clean.json"):
    data = json.load(open(fname, encoding="utf-8"))
    existing = {p["id"] for p in data}
    added = [r for r in records if r["id"] not in existing]
    data.extend(added)
    json.dump(data, open(fname, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"{fname}: appended {len(added)} (total {len(data)})")

# 2) Upsert ONLY the new records to Supabase
body = json.dumps(records).encode("utf-8")
req = urllib.request.Request(
    f"{SUPABASE_URL}/rest/v1/products",
    data=body,
    method="POST",
    headers={
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    },
)
with urllib.request.urlopen(req) as resp:
    out = json.load(resp)
    print(f"Supabase upserted {len(out)} rows (ids {out[0]['id']}..{out[-1]['id']})")
