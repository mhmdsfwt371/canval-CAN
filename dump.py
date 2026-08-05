import inspect, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import canval.cli, canval.store
for name, mod in (("CLI","cli"),("STORE","store")):
    print("="*28, name, "="*28)
    print(inspect.getsource(__import__("canval."+mod, fromlist=[mod])))
