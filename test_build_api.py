import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import build_api as b

class HybridTests(unittest.TestCase):
    def test_uv_roundtrip(self):
        for d in (0,15,90,135,200,359):
            u,v=b.wind_to_uv(6.2,d)
            sp,dd=b.uv_to_wind(u,v)
            self.assertAlmostEqual(sp,6.2*b.KT_PER_MS,5)
            self.assertLess(abs(((dd-d+180)%360)-180),1e-7)

    def test_bilinear(self):
        field=[(float(lat+lon),float(lat-lon)) for lat in b.LATS for lon in b.LONS]
        ans=b.bilinear(field,-8.8,115.2)
        self.assertAlmostEqual(ans[0],106.4,4)
        self.assertAlmostEqual(ans[1],-124.0,4)

    def test_parse_bmkg(self):
        body={'lokasi':{'adm4':'51.03.01.1001','lat':'-8.73','lon':'115.17','desa':'Tuban'},
              'data':[{'cuaca':[[{'utc_datetime':'2026-11-01 00:00:00','ws':18,'wd_deg':90}]]}]}
        s=b.parse_bmkg(body,'51.03.01.1001')
        self.assertEqual(len(s['times']),1)
        u,v=s['times']['2026-11-01T00:00Z']
        self.assertAlmostEqual(u,-5.0)
        self.assertAlmostEqual(v,0.0)


    def test_temporal_uv_interpolation_between_staggered_3h_forecasts(self):
        base=dt.datetime(2026,11,1,16,tzinfo=dt.timezone.utc)
        # Both sites have different forecast phases: 15/18Z and 14/17Z.
        key=lambda d:d.strftime('%Y-%m-%dT%H:%MZ')
        site1={'times':{key(base-dt.timedelta(hours=1)):(0.0,3.0),
                        key(base+dt.timedelta(hours=2)):(6.0,3.0)}}
        site2={'times':{key(base-dt.timedelta(hours=2)):(0.0,6.0),
                        key(base+dt.timedelta(hours=1)):(3.0,0.0)}}
        (u1,v1),method1=b.site_uv_at_time(site1,key(base))
        (u2,v2),method2=b.site_uv_at_time(site2,key(base))
        self.assertEqual((method1,method2),('interpolated','interpolated'))
        self.assertAlmostEqual(u1,2.0)
        self.assertAlmostEqual(v1,3.0)
        self.assertAlmostEqual(u2,2.0)
        self.assertAlmostEqual(v2,2.0)

    def test_temporal_qc_refuses_extrapolation_or_missing_slots(self):
        base=dt.datetime(2026,11,1,16,tzinfo=dt.timezone.utc)
        key=lambda d:d.strftime('%Y-%m-%dT%H:%MZ')
        site={'times':{key(base):(1.0,1.0),key(base+dt.timedelta(hours=6)):(3.0,3.0)}}
        self.assertEqual(b.site_uv_at_time(site,key(base+dt.timedelta(hours=3)))[1],'missing_3h_slot')
        self.assertEqual(b.site_uv_at_time(site,key(base-dt.timedelta(hours=1)))[1],'outside_forecast')

    def test_vertical_fraction_bounds_and_monotonic(self):
        self.assertAlmostEqual(b.vertical_fraction(10),0)
        self.assertAlmostEqual(b.vertical_fraction(80),1)
        self.assertTrue(0<b.vertical_fraction(20)<b.vertical_fraction(50)<1)
        with self.assertRaises(ValueError): b.vertical_fraction(100)

    def test_vertical_uv_anchors_and_direction_shear(self):
        # Full 41x41 frame; both endpoints are well-defined vectors.
        shape=b.OUT_N*b.OUT_N
        frame={'grid':{'nx':b.OUT_N,'ny':b.OUT_N,'bounds_wsen':b.BBOX,
                       'u_ms':[2.0]*shape,'v_ms':[0.0]*shape},
               'method':{},'quality':{}}
        land80=[(0.,6.)]*len(b.POINTS)
        sea80=[(0.,6.)]*len(b.POINTS)
        rings=json.loads((b.ROOT/'geo/coast_indikatif_v07.json').read_text())['rings']
        result=b.attach_vertical_layers(frame,land80,sea80,rings,3.)
        layers=result['grid']['wind_layers']
        self.assertAlmostEqual(layers['10']['u_ms'][0],2)
        self.assertAlmostEqual(layers['80']['u_ms'][0],0)
        self.assertAlmostEqual(layers['80']['v_ms'][0],6)
        f=b.vertical_fraction(30)
        self.assertAlmostEqual(layers['30']['u_ms'][0],round(2*(1-f),4))
        self.assertAlmostEqual(layers['30']['v_ms'][0],round(6*f,4))

    def test_publish_with_staggered_bmkg_times(self):
        # Regression test for production failure on 31 Oct 2026: successful
        # upstream fetches but no >=60% sites share the exact same timestamp.
        start=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=3)).replace(minute=0,second=0,microsecond=0)
        while (start.hour+8)%3!=0:start+=dt.timedelta(hours=1)
        stamps=[(start+dt.timedelta(hours=3*j)).strftime('%Y-%m-%dT%H:%M') for j in range(2)]
        import urllib.parse
        def fake_request(url,attempts=3):
            if 'bmkg.go.id' in url:
                code=url.split('adm4=')[-1]
                codes=json.loads((b.ROOT/'adm4_bali_selatan.json').read_text())
                i=next(k for k,p in enumerate(codes) if p['code']==code)
                phase=-2 if i%2 else -1
                observations=[{'utc_datetime':(start+dt.timedelta(hours=phase+3*j)).strftime('%Y-%m-%d %H:%M:%S'),
                               'ws':18,'wd_deg':100} for j in range(4)]
                return {'lokasi':{'adm4':code,'lat':-8.94+(i//6)*.06,'lon':115.08+(i%6)*.05,'desa':str(i)},
                        'data':[{'cuaca':[observations]}]}
            args=urllib.parse.parse_qs(url.split('?')[1]);lats=args['latitude'][0].split(',')
            height=80 if 'wind_speed_80m' in args['hourly'][0] else 10
            return [{'hourly':{'time':stamps,f'wind_speed_{height}m':[9.,9.] if height==80 else [6.,6.],
                               f'wind_direction_{height}m':[120.,120.]}} for _ in lats]
        with tempfile.TemporaryDirectory() as tmp, patch.object(b,'request_json',side_effect=fake_request), patch.object(b.time,'sleep',return_value=None):
            with patch('sys.argv',['build_api.py','--output',tmp]):b.main()
            latest=json.loads((Path(tmp)/'latest.json').read_text())
            self.assertEqual(latest['available_times'],2)
            grid=json.loads((Path(tmp)/latest['latest']['file']).read_text())
            self.assertEqual(grid['quality']['bmkg_sites_valid_for_time'],27)
            self.assertEqual(grid['quality']['bmkg_sites_temporally_interpolated'],27)
            self.assertEqual(grid['quality']['bmkg_sites_exact_time'],0)

    def test_publish_with_mock_responses(self):
        start=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=3)).replace(minute=0,second=0,microsecond=0)
        while (start.hour+8)%3 != 0:start+=dt.timedelta(hours=1)
        stamps=[(start+dt.timedelta(hours=3*i)).strftime('%Y-%m-%dT%H:%M') for i in range(2)]
        bmkg_stamps=[(start+dt.timedelta(hours=3*i)).strftime('%Y-%m-%d %H:%M:%S') for i in range(2)]
        seen=[0]
        def fake_request(url,attempts=3):
            if 'bmkg.go.id' in url:
                code=url.split('adm4=')[-1]
                ii=next(i for i,x in enumerate(json.loads((b.ROOT/'adm4_bali_selatan.json').read_text())) if x['code']==code)
                return {'lokasi':{'adm4':code,'lat':-8.94+(ii//6)*.06,'lon':115.08+(ii%6)*.05,'desa':str(ii)},
                        'data':[{'cuaca':[[{'utc_datetime':t,'ws':20+i,'wd_deg':130+i} for i,t in enumerate(bmkg_stamps)]]}]}
            q=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(url.split('?')[1]);lat=q['latitude'][0].split(',')
            choice=q['cell_selection'][0];speed=6 if choice=='land' else 9
            height=80 if 'wind_speed_80m' in q['hourly'][0] else 10
            if height==80:speed+=4
            return [{'hourly':{'time':stamps,f'wind_speed_{height}m':[speed,speed],f'wind_direction_{height}m':[110,110]}} for _ in lat]
        oldsleep=b.time.sleep
        with tempfile.TemporaryDirectory() as tmp, patch.object(b,'request_json',side_effect=fake_request), patch.object(b.time,'sleep',return_value=None):
            with patch('sys.argv',['build_api.py','--output',tmp]):
                b.main()
            base=Path(tmp)
            latest=json.loads((base/'latest.json').read_text())
            self.assertEqual(latest['available_times'],2)
            times=json.loads((base/'times.json').read_text())['times']
            grid=json.loads((base/times[0]['file']).read_text())
            self.assertEqual(len(grid['grid']['u_ms']),41*41)
            self.assertEqual(grid['quality']['bmkg_sites_valid_for_time'],27)
            self.assertTrue(all(math.isfinite(x) for x in grid['grid']['v_ms']))
            self.assertEqual(sorted(grid['grid']['wind_layers']), ['10','20','30','40','50','80'])
            self.assertEqual(grid['grid']['wind_layers']['10']['u_ms'],grid['grid']['u_ms'])

if __name__=='__main__':unittest.main()
