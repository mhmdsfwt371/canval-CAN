import json, time, collections
from canval.config import Settings
from canval.store import connect
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s)

def gj(p, quiet=True):
    try:
        return c._request("GET", p)
    except Exception as e:
        if not quiet: print("      ERR", str(e)[:110])
        return None

print("=== sanity: the element that worked before ===")
for attempt in (1,2):
    d = gj("/api/external/v3/settingsOverrides/869595060045399/elements/11681681", quiet=False)
    print(f"  try {attempt}:", json.dumps(d)[:260] if d else "None")
    time.sleep(1)

def can_ids(uid):
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
                if els: found[key]=els[0]["id"]
    return found

with connect(s.db_path) as conn:
    sample=[r["imei"] for r in conn.execute("SELECT imei FROM device_can WHERE file_id IS NULL LIMIT 10")]
    known=set(r[0] for r in conn.execute("SELECT file_id FROM can_files"))
print(f"\ncatalogue holds {len(known)} file ids\n")

cache={}; rows=[]
for i,uid in enumerate(sample,1):
    ov=gj(f"/api/external/v3/settingsOverrides/{uid}/overrides") or {}
    tid=ov.get("templateId")
    if tid not in cache: cache[tid]=can_ids(uid)
    rec={"imei":uid,"tpl":tid}
    for k,eid in cache[tid].items():
        e=gj(f"/api/external/v3/settingsOverrides/{uid}/elements/{eid}")
        if e is None: rec[k]="FETCH-FAILED"; continue
        v=e.get("value")
        tag=""
        if k.endswith("model") and str(v).isdigit() and int(v)>0:
            tag=" IN-CATALOGUE" if int(v) in known else " NOT-IN-CATALOGUE"
        rec[k]=f"{v}{'*' if e.get('overriden') else ''}{tag}"
    rows.append(rec); print(f"  {i:>2} {json.dumps(rec)}")
    time.sleep(0.4)

print("\n=== tally (* = overridden) ===")
for k in ("can1_model","can2_model","can1_func","can2_func"):
    print(f"  {k}:", collections.Counter(str(r.get(k)) for r in rows).most_common(8))
