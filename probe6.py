import json, collections
from canval.config import Settings
from canval.store import connect
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s); tok=c._get_token()

def gj(p):
    try:
        r=c._session.get(s.base_url+p, headers={"Authorization":f"Bearer {tok}"},
                         timeout=s.timeout, allow_redirects=False)
    except Exception: return None
    if "json" not in r.headers.get("Content-Type",""): return None
    try: return r.json()
    except Exception: return None

print("=== full CAN function options ===")
d=gj("/api/external/v3/settingsOverrides/869595060045399/elements/11681680")
print(json.dumps((d or {}).get("type",{}),indent=1)[:1200])

print("\n=== catalogue tables ===")
with connect(s.db_path) as conn:
    tabs=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print(tabs)
    for t in tabs:
        cols=[r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        print(f"  {t}: {cols}")
        if "id" in cols:
            for v in (3702,2293,3733):
                row=conn.execute(f"SELECT * FROM {t} WHERE id=?", (v,)).fetchone()
                if row: print(f"     id {v} -> {dict(row)}")
    sample=[r["imei"] for r in conn.execute(
        "SELECT imei FROM device_can WHERE file_id IS NULL LIMIT 30")]

def tree_ids(uid):
    root=gj(f"/api/external/v3/settingsOverrides/{uid}")
    stack=[(x["id"],x["name"]) for x in (root or {}).get("categories",[])]
    found={}; seen=set()
    while stack:
        cid,path=stack.pop()
        if cid in seen: continue
        seen.add(cid)
        d=gj(f"/api/external/v3/settingsOverrides/{uid}/categories/{cid}")
        if not isinstance(d,dict): continue
        for sub in d.get("subCategories") or []: stack.append((sub["id"],path+" > "+sub["name"]))
        for g in d.get("userElementGroups") or []:
            full=path+" > "+g["name"]
            key=None
            if "CAN1 > Vehicle model" in full: key="can1_model"
            elif "CAN2 > Vehicle model" in full: key="can2_model"
            elif "CAN1 > CAN function" in full: key="can1_func"
            elif "CAN2 > CAN function" in full: key="can2_func"
            if key and key not in found:
                gd=gj(f"/api/external/v3/settingsOverrides/{uid}/categories/{cid}/elementGroups/{g['id']}")
                els=(gd or {}).get("userElements") or []
                if els: found[key]=els[0]["id"]
    return found

print(f"\n=== sampling {len(sample)} unresolved devices ===")
cache={}; rows=[]
for i,uid in enumerate(sample,1):
    ov=gj(f"/api/external/v3/settingsOverrides/{uid}/overrides") or {}
    tid=ov.get("templateId")
    if tid not in cache:
        cache[tid]=tree_ids(uid)
        print(f"  [tree] template {tid} -> {cache[tid]}")
    ids=cache[tid]; rec={"imei":uid,"tpl":tid}
    for k,eid in ids.items():
        e=gj(f"/api/external/v3/settingsOverrides/{uid}/elements/{eid}") or {}
        rec[k]=(e.get("value"), e.get("overriden"))
    rows.append(rec); print(f"  {i:>2} {json.dumps(rec)}")

print("\n=== tally ===")
print("templates seen:", len(cache))
for k in ("can1_model","can2_model","can1_func","can2_func"):
    print(f"  {k}:", collections.Counter(str(r.get(k)) for r in rows).most_common(6))
