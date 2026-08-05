import json, re
from canval.config import Settings
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s)

hw=c.hardware_versions()
rows=[]
for h in hw:
    hid=h.get("id") or h.get("hardwareId")
    nm=str(h.get("name") or h.get("hardware") or h.get("model") or h)
    if hid is not None: rows.append((hid,nm))
print(f"{len(rows)} hardware entries returned\n")

WANT=re.compile(r"xtcan|stcan|lx\s*-?\s*45", re.I)
keep=[(i,n) for i,n in rows if WANT.search(n)]
skip=[(i,n) for i,n in rows if not WANT.search(n)]

def count(hid):
    try:
        p=c._request("POST","/api/external/v4/devicesSdk/filter",
                     json={"paginator":{"firstRecord":0,"itemsPerPage":1},
                           "filter":{"hardwareId":[hid]}}) or {}
        return (p.get("paginator") or {}).get("recordCount")
    except Exception as e: return f"err {str(e)[:30]}"

print("=== KEEP: XtCAN / StCAN / LX45 ===")
tot=0
for hid,nm in sorted(keep,key=lambda x:x[1].lower()):
    n=count(hid)
    if isinstance(n,int): tot+=n
    print(f"  {hid:>5}  {nm[:46]:<48} {n}")
print(f"  --> {len(keep)} ids, {tot} devices")

print("\n=== skipped ===")
for hid,nm in sorted(skip,key=lambda x:x[1].lower()):
    print(f"  {hid:>5}  {nm[:46]:<48} {count(hid)}")

print("\nIDS = " + json.dumps(sorted(i for i,_ in keep)))
