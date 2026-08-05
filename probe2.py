import json, glob, os, re
from canval.config import Settings
from canval.store import connect
from canval.xdm import XdmClient

s = Settings.from_env(); c = XdmClient(s)

print("=== looking for a local OpenAPI spec ===")
for f in glob.glob("**/*.*", recursive=True):
    if os.path.splitext(f)[1].lower() not in (".json",".yaml",".yml"): continue
    try:
        if os.path.getsize(f) < 20000: continue
        txt = open(f, encoding="utf-8", errors="ignore").read()
    except Exception: continue
    if "openapi" in txt[:3000].lower() or "swagger" in txt[:3000].lower():
        print("SPEC:", f)
        for m in sorted(set(re.findall(r"['\"](/api/[^'\"]*)['\"]", txt))):
            if "setting" in m.lower() or "config" in m.lower() or "can" in m.lower():
                print("    ", m)

with connect(s.db_path) as conn:
    print("\ndevice_can columns:", [r[1] for r in conn.execute("PRAGMA table_info(device_can)")])
    good = conn.execute("SELECT * FROM device_can WHERE file_id IS NOT NULL LIMIT 1").fetchone()
    bad  = conn.execute("SELECT * FROM device_can WHERE file_id IS NULL LIMIT 1").fetchone()
g, b = good["imei"], bad["imei"]
print("good:", dict(good)); print("bad :", dict(bad))

print("\n=== every override on the device WITH a file ===")
for o in c.device_overrides(g):
    print("   ", json.dumps(o)[:200])

tok = c._get_token()
def raw(path):
    try:
        r = c._session.get(s.base_url+path, headers={"Authorization": f"Bearer {tok}"},
                           timeout=s.timeout, allow_redirects=False)
    except Exception as e:
        print(f"  ERR {path} -> {str(e)[:80]}"); return
    ct = r.headers.get("Content-Type","")[:22]
    print(f"  {r.status_code:>3} {len(r.content):>7}b {ct:<22} {path}")
    if r.status_code >= 300 or "json" not in ct:
        print("        ->", (r.text or "")[:140].replace("\n"," "))
    elif len(r.content) < 3000:
        print("        ->", r.text[:240])

CAT = 2543072
CANDS = [
 "/api/external/v3/settingsOverrides/{u}/settings",
 "/api/external/v3/settingsOverrides/{u}/values",
 "/api/external/v3/settingsOverrides/{u}/effective",
 "/api/external/v3/settingsOverrides/{u}/categories",
 "/api/external/v3/settingsOverrides/{u}/categories/{c}",
 "/api/external/v3/settingsOverrides/{u}/categories/{c}/settings",
 "/api/external/v3/settingsOverrides/{u}/overrides?categoryId={c}",
 "/api/external/v3/settingsOverrides/{u}/{c}/settings",
 "/api/external/v4/settingsOverrides/{u}/overrides",
 "/api/external/v3/settings/{u}/overrides",
 "/api/external/v3/devices/{u}/settings",
]
for label, uid in (("WITHOUT file", b), ("WITH file", g)):
    print(f"\n=== raw probe: {label} {uid} ===")
    for p in CANDS: raw(p.format(u=uid, c=CAT))
