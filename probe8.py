import json
from canval.config import Settings
from canval.xdm import XdmClient
s=Settings.from_env(); c=XdmClient(s); tok=c._get_token()
P="/api/external/v3/settingsOverrides/869595060045399/elements/11681681"

for label, kw in (("no-redirect", dict(allow_redirects=False)),
                  ("follow",      dict(allow_redirects=True))):
    r=c._session.get(s.base_url+P, headers={"Authorization":f"Bearer {tok}"},
                     timeout=s.timeout, **kw)
    print(f"\n[{label}] {r.status_code} {r.headers.get('Content-Type','')[:30]} {len(r.content)}b")
    print("  final url:", r.url)
    if r.history: print("  hops:", [(h.status_code, h.headers.get('Location','')[:70]) for h in r.history])
    print("  body:", r.text[:220].replace("\n"," "))
