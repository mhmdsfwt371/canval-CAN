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
    """{key: (catId, groupId, elemId)} - walk once per template."""
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

def read(uid,cid,gid,eid):
    return gj(f"/api/external/v3/settingsOverrides/{uid}/categories/{cid}/elementGroups/{gid}/elements/{eid}")

with connect(s.db_path) as conn:
    sample=[r["imei"] for r in conn.execute("SELECT imei FROM device_can WHERE file_id IS NULL LIMIT 40")]
    known={r[0] for r in conn.execute("SELECT file_id FROM can_files")}
print(f"catalogue: {len(known)} file ids\n")

FUNC=None; cache={}; rows=[]
for i,uid in enumerate(sample,1):
    tid=(gj(f"/api/external/v3/settingsOverrides/{uid}/overrides") or {}).get("templateId")
    if tid not in cache: cache[tid]=can_map(uid)
    rec={"imei":uid[-6:],"tpl":tid}
    for k,(cid,gid,eid) in cache[tid].items():
        e=read(uid,cid,gid,eid)
        if e is None: rec[k]="FAIL"; continue
        v=str(e.get("value")); mark="*" if e.get("overriden") else ""
        if k.endswith("func"):
            if FUNC is None:
                FUNC={str(o["value"]):o["label"] for o in
                      ((e.get("type") or {}).get("dropdownValidation") or {}).get("options",[])}
            rec[k]=f"{v}{mark} {FUNC.get(v,'?')}"
        else:
            tag="" if v in ("","0","None") else (" OK" if int(v) in known else " NOT-IN-CAT")
            rec[k]=f"{v}{mark}{tag}"
    rows.append(rec); print(f"  {i:>2} {json.dumps(rec,ensure_ascii=False)}")
    time.sleep(0.25)

print("\n=== CAN function options ===", json.dumps(FUNC,ensure_ascii=False))
print("\n=== tally (* = overridden) ===")
for k in ("can1_model","can2_model","can1_func","can2_func"):
    vals=[str(r.get(k)) for r in rows if r.get(k)]
    print(f"  {k}:", collections.Counter(vals).most_common(8))

live=sum(1 for r in rows if any(
    str(r.get(k,"")).split()[0].rstrip("*").isdigit() and int(str(r.get(k,"")).split()[0].rstrip("*"))>0
    and "NOT-IN-CAT" not in str(r.get(k,"")) for k in ("can1_model","can2_model")))
print(f"\ndevices with a real inherited model: {live} of {len(rows)}")
