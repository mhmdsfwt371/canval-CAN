import sqlite3, os, json
c=sqlite3.connect(os.environ.get("CANVAL_DB","canval.db")); c.row_factory=sqlite3.Row
q=lambda s: c.execute(s).fetchone()[0]
print("catalogue rows      ", q("SELECT COUNT(*) FROM can_files"))
print("with a name         ", q("SELECT COUNT(*) FROM can_files WHERE name IS NOT NULL AND name!=''"))
print("with make/model     ", q("SELECT COUNT(*) FROM can_files WHERE make IS NOT NULL AND make!=''"))
print("with years          ", q("SELECT COUNT(*) FROM can_files WHERE year_from IS NOT NULL"))
print("with a bus          ", q("SELECT COUNT(*) FROM can_files WHERE can_bus IS NOT NULL"))
print("with bitrate        ", q("SELECT COUNT(*) FROM can_files WHERE bitrate_kbps IS NOT NULL"))
print("with obd pins       ", q("SELECT COUNT(*) FROM can_files WHERE obd_pins IS NOT NULL AND obd_pins!=''"))
print("with a guide url    ", q("SELECT COUNT(*) FROM can_files WHERE manual_url IS NOT NULL AND manual_url!=''"))
print("with sensors listed ", q("SELECT COUNT(DISTINCT file_id) FROM can_sensors"))
print("failed to parse     ", q("SELECT COUNT(*) FROM can_files WHERE parse_issues IS NOT NULL"))
print("\nrows that parsed badly:")
for r in c.execute("SELECT raw_model, parse_issues FROM can_files WHERE parse_issues IS NOT NULL LIMIT 8"):
    print("   ", r["raw_model"][:70], "->", r["parse_issues"][:50])
