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

    def test_publish_with_mock_responses(self):
        start=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=3)).replace(minute=0,second=0,microsecond=0)
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
            return [{'hourly':{'time':stamps,'wind_speed_10m':[speed,speed],'wind_direction_10m':[110,110]}} for _ in lat]
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

if __name__=='__main__':unittest.main()
