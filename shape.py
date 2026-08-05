import sqlite3, os
c = sqlite3.connect(os.environ["CANVAL_DB"]); c.row_factory = sqlite3.Row
r = c.execute("""
    SELECT COUNT(*) entries,
           SUM(port_function LIKE '%Sleep%') asleep,
           SUM(inherited = 0) deliberate
      FROM device_can WHERE bus > 0""").fetchone()
d = c.execute("""
    SELECT COUNT(*) n FROM (
      SELECT imei FROM device_can WHERE bus > 0
      GROUP BY imei HAVING COUNT(*) = 2 AND COUNT(DISTINCT file_id) = 1)""").fetchone()
print(f"  bus entries            {r['entries']}")
print(f"  port asleep            {r['asleep']}")
print(f"  deliberately set       {r['deliberate']}")
print(f"  same file on both      {d['n']} devices")
