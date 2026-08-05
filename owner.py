import json, os, requests
tok = os.environ["AFAQY_TOKEN"]
s = requests.Session()
s.headers.update({"Accept":"application/json","Content-Type":"application/json"})
r = s.post("https://api.afaqy.pro/v1/units", params={"token": tok},
           json={"data": json.dumps({
               "filters": {"imei": {"value": ["869595060826327"], "op": "in"}},
               "offset": 0, "limit": 1, "simplify": 1,
               "projection": ["basic","groups","last_update","sensors"]})},
           timeout=60)
u = ((r.json() or {}).get("data") or [{}])[0]
print("top-level keys:", sorted(u.keys()))
for k in ("i","id","n","owner","own","user","account","client","customer",
          "created_by","creator","device_model","groups"):
    if k in u: print(f"  {k} = {json.dumps(u[k], ensure_ascii=False)[:160]}")
