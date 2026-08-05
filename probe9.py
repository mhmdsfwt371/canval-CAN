import time, json
from canval.config import Settings
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s); tok=c._get_token()
BASE=f"/api/external/v3/settingsOverrides/869595060045399"

def hit(p, n=1):
    for i in range(n):
        r=c._session.get(s.base_url+p, headers={"Authorization":f"Bearer {tok}"}, timeout=s.timeout)
        j = "json" in r.headers.get("Content-Type","")
        srv = r.headers.get("Server","") + " " + r.headers.get("x-served-by","") + r.headers.get("X-Powered-By","")
        print(f"  {r.status_code} {'JSON' if j else 'HTML'} {len(r.content):>6}b  {srv.strip()[:40]:<40} {p[-58:]}")
        if j and len(r.content) < 500: print("       ", r.text[:200])
        time.sleep(0.5)

print("=== does the tree still work right now? ===")
hit(BASE)
hit(BASE+"/overrides")
hit(BASE+"/categories/2543123")
hit(BASE+"/categories/2543123/elementGroups/2213898")

print("\n=== the element path, 6 times (looking for one good hit) ===")
hit(BASE+"/elements/11681681", 6)

print("\n=== other element-ish shapes ===")
for p in [BASE+"/categories/2543123/elementGroups/2213898/elements/11681681",
          BASE+"/categories/2543123/elements/11681681",
          BASE+"/userElements/11681681",
          BASE+"/elements?ids=11681681",
          BASE+"/elements",
          "/api/external/v4/settingsOverrides/869595060045399/elements/11681681"]:
    hit(p)
