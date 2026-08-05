import inspect, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import canval.matching, canval.fallback
for m in (canval.matching, canval.fallback):
    print("="*30, m.__name__, "="*30)
    print(inspect.getsource(m))
