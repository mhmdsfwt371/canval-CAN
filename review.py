import sqlite3, os, json
c=sqlite3.connect(os.environ["CANVAL_DB"]); c.row_factory=sqlite3.Row
old=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE name LIKE 'device_can_v1%'")]
print("v1 backup table:", old)

from canval.store import coverage_detail
print("\ncoverage:", json.dumps(coverage_detail(c), indent=1))

print("\n=== files that only became visible now ===")
for r in c.execute("""
    SELECT d.file_id, f.name, f.raw_model, COUNT(DISTINCT d.imei) n,
           SUM(d.inherited) inh
      FROM device_can d LEFT JOIN can_files f ON f.file_id=d.file_id
     WHERE d.file_id IS NOT NULL GROUP BY d.file_id
     ORDER BY n DESC LIMIT 15"""):
    print(f"  {r['file_id']:>6} {str(r['name'])[:34]:<36} devices {r['n']:>4}  inherited {r['inh']}")

print("\n=== file ids with no catalogue row (should be 0) ===")
for r in c.execute("""SELECT d.file_id, COUNT(*) n FROM device_can d
     WHERE d.file_id IS NOT NULL AND NOT EXISTS
       (SELECT 1 FROM can_files f WHERE f.file_id=d.file_id)
     GROUP BY d.file_id ORDER BY n DESC LIMIT 10"""):
    print(f"  {r['file_id']}  on {r['n']} bus row(s)")

print("\n=== the 13 with nothing assigned ===")
for r in c.execute("SELECT imei,template_id,config_name FROM device_can WHERE bus=0 LIMIT 13"):
    print(f"  {r['imei']}  tpl {r['template_id']}  {r['config_name']}")

print("\n=== spot-check these 5 in the XDM console ===")
for r in c.execute("""SELECT d.imei,d.bus,d.file_id,d.inherited,d.port_function,
                             f.name,d.config_name
       FROM device_can d LEFT JOIN can_files f ON f.file_id=d.file_id
      WHERE d.imei IN (SELECT imei FROM device_can WHERE bus>0
                       GROUP BY imei HAVING COUNT(*)>1 LIMIT 5)
      ORDER BY d.imei,d.bus"""):
    src = "inherited" if r["inherited"] else "OVERRIDDEN"
    print(f"  {r['imei']} CAN{r['bus']}  {r['file_id']:<6} {str(r['name'])[:28]:<30} {src:<11} port={r['port_function']}")
