import datetime as dt
from check_bmkg_surface_readiness import check_snapshot

UTC=dt.timezone.utc
now=dt.datetime(2026,11,5,1,0,tzinfo=UTC)
fmt=lambda x:x.strftime('%Y-%m-%dT%H:%MZ')
base={
 'type':'BMKG_Surface_Prototype_Points_Polygons',
 'generated_utc':fmt(now),'valid_times':[],
 'qc':{'valid_land':27,'valid_marine':0,'marine_gust_anomalies':5,
       'marine_source_reports':[
          {'code':c,'status':'OK','active_at_audit':False,
           'issued_utc':'2026-11-01T12:00Z','valid_to_utc':'2026-11-05T00:00Z'}
          for c in ('P.P.02','P.P.03','P.P.04','P.P.06')
       ]},
 'marine_areas':[],'marine_polygons':None,
}
r=check_snapshot(base,now=now)
assert not r['ready'] and r['status']=='WAITING_FOR_VALID_SOURCES'
assert r['marine_active']==0 and len(r['sea_details'])==4
assert r['sea_details'][0]['valid_to_utc']=='2026-11-05T00:00Z'

base['qc']['valid_marine']=4
base['marine_areas']=[{'code':x} for x in ('P.P.02','P.P.03','P.P.04','P.P.06')]
base['marine_polygons']={'features':[{'type':'Feature'}]}
base['valid_times']=[{'valid_utc':fmt(now+dt.timedelta(hours=1))}]
r=check_snapshot(base,now=now)
assert r['ready'] and r['status']=='READY'

base['generated_utc']=fmt(now-dt.timedelta(hours=2))
try:check_snapshot(base,now=now)
except ValueError:pass
else:raise AssertionError('Stale QC snapshot should be refused')
print('3 readiness scenarios passed (sea stale, all valid, snapshot stale)')
