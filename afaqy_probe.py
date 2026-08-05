import base64, json, os, time, requests
tok = os.environ["AFAQY_TOKEN"]
body = tok.split(".")[1]; body += "=" * (-len(body) % 4)
claims = json.loads(base64.urlsafe_b64decode(body))
exp, now = claims.get("exp"), int(time.time())
print("expires :", time.strftime("%Y-%m-%d %H:%M", time.gmtime(exp)),
      "->", "VALID" if exp > now else "EXPIRED", f"({(exp-now)//86400} days left)")
print("subject :", claims.get("sub"))

s = requests.Session(); s.headers["Authorization"] = "Bearer " + tok
for path in ("/api/units", "/units", "/api/v1/units", "/api/unit/list", "/api/user"):
    try:
        r = s.get("https://api.afaqy.pro" + path, timeout=20)
        ct = r.headers.get("Content-Type", "")[:20]
        print(f"  {r.status_code} {ct:<20} {len(r.content):>8}b  {path}")
        if "json" in ct and len(r.content) < 600:
            print("      ", r.text[:300])
    except Exception as e:
        print("  ERR", path, str(e)[:60])
