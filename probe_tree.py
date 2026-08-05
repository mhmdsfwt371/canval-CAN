import json
from canval.config import Settings
from canval.store import connect
from canval.xdm import XdmClient

s = Settings.from_env(); c = XdmClient(s)
with connect(s.db_path) as conn:
    good = conn.execute("SELECT imei,file_id,config_name FROM device_can WHERE file_id IS NOT NULL LIMIT 1").fetchone()
    bad  = conn.execute("SELECT imei,file_id,config_name FROM device_can WHERE file_id IS NULL LIMIT 1").fetchone()

CATS = [2543069,2543070,2543071,2543072,2543073,2543074,2543075,2543076,2543077]
PATTERNS = [
 "/api/external/v3/settingsOverrides/{u}?categoryId={c}",
 "/api/external/v3/settingsOverrides/{u}/{c}",
 "/api/external/v3/settings/{u}?categoryId={c}",
 "/api/external/v3/settings/{u}/categories/{c}",
 "/api/external/v3/settingsOverrides/{u}?categoryIds={c}",
]

def get(path):
    try:
        return c._request("GET", path), None
    except Exception as e:
        return None, str(e)[:90]

uid = bad["imei"]
print("finding a working pattern on", uid)
working = None
for pat in PATTERNS:
    r, err = get(pat.format(u=uid, c=CATS[0]))
    if err:
        print("  X ", pat, "->", err); continue
    body = json.dumps(r)
    print("  OK", pat, "->", body[:180])
    if working is None and '"categories"' not in body:
        working = pat

if not working:
    print("\nno pattern returned settings. stop here and paste this output.")
    raise SystemExit(0)

print("\nusing:", working)
for row, label in ((good, "WITH file"), (bad, "WITHOUT file")):
    print("\n" + "="*62)
    print(label, row["imei"], "file_id=", row["file_id"], "config=", row["config_name"])
    for cid in CATS:
        r, err = get(working.format(u=row["imei"], c=cid))
        if err: continue
        body = json.dumps(r)
        hit = "can" in body.lower()
        print(f"  cat {cid} {'*** CAN ***' if hit else ''} {body[:260]}")
