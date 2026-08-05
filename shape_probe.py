import json, sqlite3, os
from canval.config import Settings
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s); tok=c._get_token()
R="/api/external/v3/settingsOverrides"
def g(p):
    r=c._session.get(s.base_url+p, headers={"Authorization":f"Bearer {tok}"}, timeout=s.timeout)
    if "json" not in r.headers.get("Content-Type",""): return None
    try: return r.json()
    except Exception: return None

db=sqlite3.connect(os.environ["CANVAL_DB"]); db.row_factory=sqlite3.Row
rows=db.execute("""SELECT imei, template_id, hardware, config_name FROM device_can
                    WHERE element_name='(no CAN ports in template)'
                    GROUP BY template_id LIMIT 3""").fetchall()

for row in rows:
    uid=row["imei"]
    print("\n"+"="*72)
    print(f"device {uid}  template {row['template_id']}  {row['hardware']}  {row['config_name']}")
    root=g(f"{R}/{uid}") or {}
    hw=next((x["id"] for x in root.get("categories",[]) if x["name"]=="Hardware"), None)
    print(f"  Hardware id = {hw}")
    body=g(f"{R}/{uid}/categories/{hw}") or {}
    print("  RAW Hardware ->", json.dumps(body)[:400])
    for br in body.get("subCategories",[]):
        if br["name"] not in ("CAN1","CAN2"): continue
        raw=g(f"{R}/{uid}/categories/{br['id']}")
        print(f"\n  RAW {br['name']} (cat {br['id']}) ->", json.dumps(raw))
        for sub in (raw or {}).get("subCategories",[]):
            r2=g(f"{R}/{uid}/categories/{sub['id']}")
            print(f"     sub {sub['id']} {sub['name']!r} ->", json.dumps(r2)[:300])
            for gr in (r2 or {}).get("userElementGroups",[]):
                els=g(f"{R}/{uid}/categories/{sub['id']}/elementGroups/{gr['id']}")
                print(f"        group {gr['id']} {gr['name']!r} ->", json.dumps(els)[:300])
        for gr in (raw or {}).get("userElementGroups",[]):
            els=g(f"{R}/{uid}/categories/{br['id']}/elementGroups/{gr['id']}")
            print(f"     group {gr['id']} {gr['name']!r} ->", json.dumps(els)[:300])
