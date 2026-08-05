import sqlite3, os, json
c=sqlite3.connect(os.environ["CANVAL_DB"]); c.row_factory=sqlite3.Row
print("=== the estate ===")
for r in c.execute("""SELECT CASE WHEN file_id IS NOT NULL THEN 'file assigned'
                                  ELSE element_name END state,
                             COUNT(DISTINCT imei) devices
                        FROM device_can GROUP BY state ORDER BY devices DESC"""):
    print(f"  {r['devices']:>7}  {r['state']}")
print("\n=== top vehicle models across the fleet ===")
for r in c.execute("""SELECT f.name, COUNT(DISTINCT d.imei) n
                        FROM device_can d JOIN can_files f ON f.file_id=d.file_id
                       WHERE d.file_id IS NOT NULL AND f.name IS NOT NULL
                       GROUP BY f.name ORDER BY n DESC LIMIT 15"""):
    print(f"  {r['n']:>6}  {r['name']}")
