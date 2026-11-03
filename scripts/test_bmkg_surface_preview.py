import math
from datetime import datetime, timezone, timedelta
from build_bmkg_surface_preview import vector_row
from audit_sinkronisasi_bmkg import uv, sample_at, angle

# SE wind FROM (135) gives NW flow (315)
u,v=uv(10.0,135)
r=vector_row(u,v,'exact')
assert abs(r['from_deg']-135)<0.1
assert abs(r['speed_kt']-19.44)<0.05
assert angle('Tenggara')==135
assert angle('Barat Laut')==315

base=datetime(2026,11,3,0,tzinfo=timezone.utc)
rows={base:(0,0),base+timedelta(hours=3):(3,6)}
wind,method=sample_at(rows,base+timedelta(hours=1),True)
assert method=='interpolated' and all(abs(a-b)<1e-8 for a,b in zip(wind,(1,2)))
wind,method=sample_at(rows,base+timedelta(hours=4),True)
assert wind is None
assert vector_row(0,0,'exact')['from_deg'] is None
print('6 tests passed: wind vector, bearing, compass, interpolation, horizon, calm.')
