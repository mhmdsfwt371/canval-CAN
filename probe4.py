import json
from canval.config import Settings
from canval.xdm import XdmClient
s = Settings.from_env(); c = XdmClient(s); tok = c._get_token()

def gj(path):
    try:
        r = c._session.get(s.base_url+path, headers={"Authorization":f"Bearer {tok}"},
                           timeout=s.timeout, allow_redirects=False)
    except Exception: return None, None
    if "json" not in r.headers.get("Content-Type",""): return None, None
    try: return r.json(), r.status_code
    except Exception: return None, r.status_code

def find_groups(uid):
    root, _ = gj(f"/api/external/v3/settingsOverrides/{uid}")
    out, stack = [], [(x["id"], x["name"]) for x in (root or {}).get("categories", [])]
    seen = set()
    while stack:
        cid, path = stack.pop()
        if cid in seen: continue
        seen.add(cid)
        d, _ = gj(f"/api/external/v3/settingsOverrides/{uid}/categories/{cid}")
        if not isinstance(d, dict): continue
        for sub in d.get("subCategories") or []:
            stack.append((sub["id"], path+" > "+sub["name"]))
        for g in d.get("userElementGroups") or []:
            out.append((g["id"], path+" > "+g["name"], cid))
    return out

SHAPES = [
 "/api/external/v3/settingsOverrides/{u}/elementGroups/{g}",
 "/api/external/v3/settingsOverrides/{u}/userElementGroups/{g}",
 "/api/external/v3/settingsOverrides/{u}/groups/{g}",
 "/api/external/v3/settingsOverrides/{u}/elementGroups/{g}/elements",
 "/api/external/v3/settingsOverrides/{u}/categories/{c}/elementGroups/{g}",
 "/api/external/v3/settingsOverrides/{u}/categories/{c}/groups/{g}",
 "/api/external/v3/settingsOverrides/{u}/elements?groupId={g}",
 "/api/external/v3/settingsOverrides/{u}/overrides?userElementGroupId={g}",
 "/api/external/v3/settingsOverrides/{u}/overrides?elementGroupId={g}",
 "/api/external/v3/settingsOverrides/{u}/overrides?groupId={g}",
 "/api/external/v3/userElementGroups/{g}",
 "/api/external/v3/elementGroups/{g}",
]

for label, uid in (("WITHOUT file","869595060045399"), ("WITH file","869595060826327")):
    print("\n"+"="*70); print(label, uid)
    groups = find_groups(uid)
    cans = [x for x in groups if "can" in x[1].lower()]
    print(f"  {len(groups)} leaf groups, {len(cans)} CAN-related:")
    for gid, path, cid in cans: print(f"    {gid}  {path}   (cat {cid})")
    if not cans: continue
    gid, path, cid = cans[0]
    print(f"\n  probing group {gid}  [{path}]")
    for shp in SHAPES:
        p = shp.format(u=uid, g=gid, c=cid)
        d, st = gj(p)
        if d is None: continue
        print(f"    {st} {p}")
        print("        ", json.dumps(d)[:400])
