import inspect, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import canval.index
print(inspect.getsource(canval.index))
