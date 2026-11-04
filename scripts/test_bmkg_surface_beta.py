#!/usr/bin/env python3
"""Offline sanity checks for BMKG Surface Beta product, not meteorological validation."""
import datetime as dt
import json
import math
from pathlib import Path
import sys

UTC=dt.timezone.utc

def check(path):
    root=Path(path)
    times=json.loads((root/'v1/times.json').read_text(encoding='utf-8'))['times']
    meta=json.loads((root/'v1/metadata.json').read_text(encoding='utf-8'))
    latest=json.loads((root/'v1/latest.json').read_text(encoding='utf-8'))
    polygons=json.loads((root/'v1/areas.geojson').read_text(encoding='utf-8'))
    assert times and len(times)<=48,'Invalid times'
    assert latest['latest_vector_file']==times[0]['file']
    assert meta['kind']=='wind_vectors_points_and_marine_polygons_NOT_GRID'
    assert meta['marine_reference_height'] if 'marine_reference_height' in meta else meta['wind_height']['marine']=='not verified'
    assert len(polygons['features'])==4
    assert 'LICENSE' not in latest.get('status','')
    assert 'Grid' not in meta['kind']
    h=(root/'index.html').read_text(encoding='utf-8')
    assert '/*__DATA_JSON__*/' not in h and 'const DATA=' in h
    assert len(h)>30000
    expected=[x['valid_time_utc'] for x in times]
    assert expected==sorted(set(expected))
    for t in times:
        data=json.loads((root/'v1'/t['file']).read_text(encoding='utf-8'))
        assert data['valid_time_utc']==t['valid_time_utc']
        assert len(data['marine'])==4 and len(data['land'])>=17
        assert data['qc']['land_exact']+data['qc']['land_temporal_interpolated']==len(data['land'])
        for row in data['land']+data['marine']:
            assert all(math.isfinite(float(row[k])) for k in ('u_ms','v_ms','speed_kt'))
            assert abs(math.hypot(row['u_ms'],row['v_ms'])*1.9438444924406-row['speed_kt'])<0.04
    print(f'PASS: {len(times)} times, {len(polygons["features"])} sea area polygons, all vectors consistent. UI inline snapshot found.')

if __name__=='__main__':
    check(sys.argv[1] if len(sys.argv)>1 else 'bmkg_surface_beta_tahap4')
