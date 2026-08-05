import json, time, collections
from canval.config import Settings
from canval.store import connect
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s); tok=c._get_token()

def gj(p):
    r=c._session.get(s.base_url+p, headers={"Authorization":f"Bearer {tok}"}, timeout=s.timeout)
    if "json" not in r.headers.get("Content-Type",""): return None
    try: return r.json()
    except Exception: return None

def can_map(uid):
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
            full=path+" > "+g["name"]; key=None
            if "CAN1 > Vehicle model" in full: key="can1_model"
            elif "CAN2 > Vehicle model" in full: key="can2_model"
            elif "CAN1 > CAN function" in full: key="can1_func"
            elif "CAN2 > CAN function" in full: key="can2_func"
            if key and key not in found:
                gd=gj(f"/api/external/v3/settingsOverrides/{uid}/categories/{cid}/elementGroups/{g['id']}")
                els=(gd or {}).get("userElements") or []
                if els: found[key]=(cid,g["id"],els[0]["id"])
    return found

with connect(s.db_path) as conn:
    sample=[r["imei"] for r in conn.execute("SELECT imei FROM device_can WHERE file_id IS NULL LIMIT 40")]
    names={r["file_id"]: (r["name"] or r["raw_model"] or "") for r in
           conn.execute("SELECT file_id,name,raw_model FROM can_files")}
print(f"catalogue: {len(names)} files\n")

def label(v):
    if not str(v).isdigit() or int(v)==0: return f"{v}  (not set)"
    n=names.get(int(v))
    return f"{v}  {n}" if n else f"{v}  <<NOT IN CATALOGUE>>"

FUNC=None; cache={}; rows=[]
for i,uid in enumerate(sample,1):
    tid=(gj(f"/api/external/v3/settingsOverrides/{uid}/overrides") or {}).get("templateId")
    if tid not in cache: cache[tid]=can_map(uid)
    rec={}
    for k,(cid,gid,eid) in cache[tid].items():
        e=gj(f"/api/external/v3/settingsOverrides/{uid}/categories/{cid}/elementGroups/{gid}/elements/{eid}")
        if e is None: rec[k]="FAIL"; continue
        v=str(e.get("value")); mark=" [overridden]" if e.get("overriden") else " [inherited]"
        if k.endswith("func"):
            if FUNC is None:
                FUNC={str(o["value"]):o["label"] for o in
                      ((e.get("type") or {}).get("dropdownValidation") or {}).get("options",[])}
            rec[k]=f"{v}  {FUNC.get(v,'?')}{mark}"
        else:
            rec[k]=label(v)+mark
    rows.append(rec)
    print(f"{i:>2}. {uid}   template {tid}")
    for k in ("can1_func","can1_model","can2_func","can2_model"):
        if k in rec: print(f"      {k:<11} {rec[k]}")
    time.sleep(0.25)

print("\n=== CAN function options ===")
for v,l in (FUNC or {}).items(): print(f"   {v} = {l}")
print("\n=== tally ===")
for k in ("can1_func","can1_model","can2_func","can2_model"):
    print(f"\n {k}")
    for val,n in collections.Counter(r[k] for r in rows if k in r).most_common(10):
        print(f"   {n:>3}  {val}")
