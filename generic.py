import sqlite3, os, re
c=sqlite3.connect(os.environ["CANVAL_DB"]); c.row_factory=sqlite3.Row
makes=[r[0] for r in c.execute("SELECT DISTINCT upper(make) FROM can_files WHERE make IS NOT NULL AND make!=''")]
rows=c.execute("""SELECT d.imei, d.config_name, f.name
                    FROM device_can d JOIN can_files f ON f.file_id=d.file_id
                   WHERE d.file_id IS NOT NULL AND f.name LIKE 'J1939%'""").fetchall()
hits={}
for r in rows:
    cfg=(r["config_name"] or "").upper()
    for m in makes:
        if len(m)>3 and m in cfg:
            hits.setdefault(m, set()).add(r["imei"]); break
print(f"devices on a generic J1939 file: {len({r['imei'] for r in rows})}")
print("of those, the config name points at a make we have files for:\n")
for m,s in sorted(hits.items(), key=lambda x:-len(x[1]))[:20]:
    print(f"  {len(s):>6}  {m}")
print(f"\n  total flagged: {sum(len(s) for s in hits.values())}")
