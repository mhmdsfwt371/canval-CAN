import inspect, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import canval.afaqy, canval.monitor
for m in (canval.afaqy, canval.monitor):
    print("="*30, m.__name__, "="*30)
    print(inspect.getsource(m))
