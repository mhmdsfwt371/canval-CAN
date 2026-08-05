import sqlite3, os
c=sqlite3.connect(os.environ["CANVAL_DB"]); c.row_factory=sqlite3.Row
print("distinct element_name values stored:")
for r in c.execute("SELECT element_name, COUNT(*) n, SUM(file_id IS NOT NULL) hit FROM device_can GROUP BY element_name ORDER BY n DESC"):
    print(f"   {r['element_name']!r:<28} rows {r['n']:>6}   with file {r['hit']}")
print("\ndevices holding more than one row:",
      c.execute("SELECT COUNT(*) FROM (SELECT imei FROM device_can GROUP BY imei HAVING COUNT(*)>1)").fetchone()[0])
print("the two-bus device we inspected:")
for r in c.execute("SELECT * FROM device_can WHERE imei='869595060826327'"):
    print("  ", dict(r))
