import json, os, requests
tok = os.environ["AFAQY_TOKEN"]
s = requests.Session()
s.headers.update({"Accept": "application/json, text/plain, */*",
                  "Content-Type": "application/json"})

def call(path, inner):
    r = s.post("https://api.afaqy.pro" + path, params={"token": tok},
               json={"data": json.dumps(inner)}, timeout=60)
    ct = r.headers.get("Content-Type", "")[:22]
    print(f"\n{r.status_code} {ct} {len(r.content)}b   POST {path}")
    print("  ", r.text[:600])
    return r

call("/v1/units", {"offset": 0, "limit": 3, "simplify": 1,
                   "projection": ["basic"]})
call("/units/list", {"offset": 0, "limit": 3, "simplify": 1})
call("/units", {"offset": 0, "limit": 3, "simplify": 1})
