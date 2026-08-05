import json
from canval.config import Settings
from canval.xdm import XdmClient
s = Settings.from_env(); c = XdmClient(s); tok = c._get_token()

def gj(p):
    try:
        r = c._session.get(s.base_url+p, headers={"Authorization":f"Bearer {tok}"},
                           timeout=s.timeout, allow_redirects=False)
    except Exception: return None
    if "json" not in r.headers.get("Content-Type",""): return None
    try: return r.json()
    except Exception: return None

TARGETS = {
 "869595060045399": [("CAN1 model",2543123,2213898),("CAN2 model",2543125,2213900),
                     ("CAN1 func",2543122,2213897)],
 "869595060826327": [("CAN1 model",2184480,1971280),("CAN2 model",2184482,1971282),
                     ("CAN1 func",2184479,1971279)],
}
ELEM = ["/api/external/v3/settingsOverrides/{u}/categories/{c}/elementGroups/{g}/elements/{e}",
        "/api/external/v3/settingsOverrides/{u}/elements/{e}",
        "/api/external/v3/settingsOverrides/{u}/userElements/{e}",
        "/api/external/v3/settingsOverrides/{u}/categories/{c}/elements/{e}"]

for uid, items in TARGETS.items():
    print("\n"+"="*70); print("device", uid)
    for label, cid, gid in items:
        d = gj(f"/api/external/v3/settingsOverrides/{uid}/categories/{cid}/elementGroups/{gid}")
        print(f"\n  [{label}] cat={cid} grp={gid}")
        print("   ", json.dumps(d)[:600] if d is not None else "no json")
        for el in (d or {}).get("userElements", [])[:3]:
            eid = el["id"]
            print(f"    element {eid} {el.get('name')!r}")
            for shp in ELEM:
                v = gj(shp.format(u=uid, c=cid, g=gid, e=eid))
                if v is not None:
                    print("      ", shp.split("/")[-2]+"/"+shp.split("/")[-1], "->", json.dumps(v)[:300])
