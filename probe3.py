import json
from canval.config import Settings
from canval.xdm import XdmClient
s = Settings.from_env(); c = XdmClient(s); tok = c._get_token()

def g(path, show=300):
    try:
        r = c._session.get(s.base_url+path, headers={"Authorization":f"Bearer {tok}"},
                           timeout=s.timeout, allow_redirects=False)
    except Exception as e:
        return None
    if "json" not in r.headers.get("Content-Type",""): return None
    print(f"  {r.status_code} {path}")
    print("      ", r.text[:show])
    try: return r.json()
    except Exception: return None

BAD, GOOD = "869595060045399", "869595060826327"
print("=== swagger on the server ===")
for p in ["/swagger/v1/swagger.json","/swagger/v3/swagger.json","/swagger/v4/swagger.json",
          "/swagger.json","/openapi.json","/api/swagger.json","/api/external/swagger.json"]:
    d = g(p, 160)
    if d and "paths" in d:
        print("   PATHS with settings/can:")
        for k in sorted(d["paths"]):
            if any(w in k.lower() for w in ("setting","template","can","element")): print("     ", k)

print("\n=== template endpoints (44405 = bad, 38928 = good) ===")
for tid in (44405, 38928):
    for p in ["/api/external/v3/settingsTemplates/{t}","/api/external/v3/settingsTemplates/{t}/overrides",
              "/api/external/v3/settingsTemplates/{t}/settings","/api/external/v3/templates/{t}",
              "/api/external/v3/templates/{t}/overrides","/api/external/v3/configurations/{t}",
              "/api/external/v4/settingsTemplates/{t}/overrides","/api/external/v3/settingsOverrides/templates/{t}"]:
        g(p.format(t=tid), 400)

print("\n=== walking the category tree on the device WITHOUT a file ===")
CATS=[2543069,2543070,2543071,2543072,2543073,2543074,2543075,2543076,2543077]
base=f"/api/external/v3/settingsOverrides/{BAD}/categories/"
for cid in CATS:
    d = g(base+str(cid), 400)
    if not isinstance(d, dict): continue
    for sub in (d.get("subCategories") or []):
        sid = sub["id"]
        print(f"   -- sub {sid} {sub.get('name')}")
        for p in [base+str(sid), base+f"{cid}/{sid}", base+f"{cid}/subCategories/{sid}",
                  f"/api/external/v3/settingsOverrides/{BAD}/subCategories/{sid}"]:
            d2 = g(p, 500)
            if isinstance(d2, dict) and d2.get("subCategories"):
                for s3 in d2["subCategories"]:
                    g(base+str(s3["id"]), 500)
