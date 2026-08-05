import json
from canval.config import Settings
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s)
rows=c._request("GET","/api/external/v3/canfiles/filter",
                params={"FirstRecord":0,"ItemsPerPage":3}) or {}
res=rows.get("results") or []
print("keys the API returns:", sorted(res[0].keys()) if res else "none")
for r in res[:2]:
    print("\n"+"="*70)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:2500])
