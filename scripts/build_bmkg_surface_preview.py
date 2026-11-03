#!/usr/bin/env python3
"""BMKG darat-laut: read-only point & polygon vector prototype.

Does NOT create an interpolated wind grid or publish a forecast API.
Produces artifact-only HTML/JSON for review on GitHub Actions.
Requires existing root adm4_bali_selatan.json; Python stdlib only.
"""
import datetime as dt
import json
import math
import os
from pathlib import Path
import time
from audit_sinkronisasi_bmkg import (
    audit_land, audit_sea, fetch, sample_at, iso, UTC, MARINE_CODES
)

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.getenv('BMKG_PREVIEW_OUT', 'bmkg_surface_preview_tahap3'))
NOW = dt.datetime.now(UTC)
GEOJSON_URL='https://maritim.bmkg.go.id/marine2026-data/meta/wilmetos.min.geojson'
EARTH_KNOTS=1.9438444924406


def vector_row(u,v,method):
    sp=math.hypot(u,v)
    # meteorological FROM; calm <0.1m/s carries no meaningful direction
    from_deg=(math.degrees(math.atan2(-u,-v))+360)%360 if sp>=0.1 else None
    return {'u_ms':round(u,4),'v_ms':round(v,4),
            'speed_kt':round(sp*EARTH_KNOTS,2),
            'from_deg':round(from_deg,1) if from_deg is not None else None,
            'method':method}


def check_input_land(entries):
    if not isinstance(entries,list) or not entries or len(entries)>100:
        raise ValueError('ADM4 config must be a non-empty array <=100 sites')
    for s in entries:
        if not isinstance(s,dict) or not s.get('code'): raise ValueError('Invalid ADM4 entry')


def load_polygons():
    full=fetch(GEOJSON_URL)
    if full.get('type')!='FeatureCollection': raise ValueError('Malformed marine GeoJSON')
    wanted=set(MARINE_CODES)
    features=[]
    for feature in full.get('features',[]):
        prop=feature.get('properties') or {}
        if prop.get('ID_MAR') in wanted:
            geom=feature.get('geometry') or {}
            if geom.get('type') in ('Polygon','MultiPolygon'):
                features.append(feature)
    codes={f['properties']['ID_MAR'] for f in features}
    if codes!=wanted: raise ValueError(f'Missing official marine polygons: {sorted(wanted-codes)}')
    return {'type':'FeatureCollection','features':features}


def build():
    raw=json.loads((ROOT/'adm4_bali_selatan.json').read_text(encoding='utf-8'))
    check_input_land(raw)
    land=[];land_sets=[];reports=[]
    for i,s in enumerate(raw):
        report,rows=audit_land(s)
        reports.append(report)
        if report.get('status')=='OK' and report.get('latitude') is not None and rows:
            land.append({'adm4':report['adm4'], 'name':report['name_api'],
                         'lat':report['latitude'], 'lon':report['longitude'],
                         'source':'BMKG prakiraan cuaca ADM4'})
            land_sets.append(rows)
        print(f'Darat {i+1}/{len(raw)} {s["code"]}: {report["status"]}',flush=True)
        time.sleep(1.05)
    sea=[];sea_sets=[];sea_reports=[]
    for code in MARINE_CODES:
        report,rows=audit_sea(code)
        sea_reports.append(report)
        if report.get('status')=='OK' and report.get('active_at_audit') and rows:
            sea.append({'code':code,'name':report.get('name') or code,
                        'issued_utc':report.get('issued_utc'),
                        'valid_from_utc':report.get('valid_from_utc'),
                        'valid_to_utc':report.get('valid_to_utc'),
                        'gust_lower_than_speed_rows':report.get('gust_less_than_wind_speed_rows',0),
                        'source':'BMKG Maritim perairan'})
            sea_sets.append(rows)
        print(f'Laut {code}: {report["status"]}',flush=True)
        time.sleep(0.5)
    polys=None;polygon_error=None
    try: polys=load_polygons()
    except Exception as e: polygon_error=str(e)[:300]
    if not polys: print('Perhatian: polygon resmi tidak tersedia:',polygon_error,flush=True)
    # Match at native sea timestamps; no temporal interpolation for sea.
    native=sorted(set().union(*(set(v) for v in sea_sets))) if sea_sets else []
    times=[]
    for t in native:
        if not NOW<=t<=NOW+dt.timedelta(hours=48): continue
        lvals=[];svals=[]
        for site,values in zip(land,land_sets):
            wind,method=sample_at(values,t,True)
            if wind is not None:lvals.append({'adm4':site['adm4'],**vector_row(*wind,method)})
        for site,values in zip(sea,sea_sets):
            if t in values:svals.append({'code':site['code'],**vector_row(*values[t],'exact_categorical')})
        # Strict threshold for map: all four sea areas and at least 17 land points.
        if len(lvals)<max(17,math.ceil(len(raw)*0.60)) or len(svals)<len(MARINE_CODES):
            continue
        times.append({'valid_utc':iso(t),
                      'valid_wita':(t+dt.timedelta(hours=8)).strftime('%d %b %Y, %H:%M WITA'),
                      'land_exact':sum(x['method']=='exact' for x in lvals),
                      'land_temporal_interp':sum(x['method']=='interpolated' for x in lvals),
                      'marine_exact':len(svals),
                      'land':lvals,'marine':svals})
    OUT.mkdir(parents=True,exist_ok=True)
    report={
        'type':'BMKG_Surface_Prototype_Points_Polygons',
        'status':'READ_ONLY_REVIEW_NOT_VALIDATED',
        'generated_utc':iso(NOW),
        'sources':{
           'land':'https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4={adm4}',
           'sea':'https://maritim.bmkg.go.id/marine2026-data/perairan/{area}.json',
           'sea_geojson':GEOJSON_URL},
        'spatial_representativity':'ADM4 forecasts are point samples; marine forecasts represent polygons, not grids',
        'vertical_height':'land 10m user-confirmed; marine reference height UNVERIFIED',
        'units':'u/v m/s, displayed speed knots, meteorological FROM degrees',
        'sea_direction_input':'compass category; degrees are sector-center approximations, not precise observed bearings',
        'time_qc':'future <=48h; sea exact only; land exact or <=3h bracketed UV interpolation',
        'warning':'Do not use for public flight safety or kite permission; not a validated BMKG gridded wind product',
        'land_sites':land,'marine_areas':sea,'marine_polygons':polys,
        'polygon_error':polygon_error,
        'valid_times':times,
        'qc':{'configured_land':len(raw),'valid_land':len(land),'valid_marine':len(sea),
              'usable_times':len(times),'land_errors':[r for r in reports if r.get('status')!='OK'],
              'marine_errors':[r for r in sea_reports if r.get('status')!='OK'],
              'marine_gust_anomalies':sum(r.get('gust_less_than_wind_speed_rows',0) for r in sea_reports)},
    }
    (OUT/'bmkg_surface_preview.json').write_text(json.dumps(report,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    html=(ROOT/'scripts'/'preview_template.html').read_text(encoding='utf-8')
    (OUT/'preview.html').write_text(html,encoding='utf-8')
    overview=[
      '# Tahap 3 — Prototype vektor BMKG darat–laut (artifact pengujian)', '',
      f'Generated UTC: {iso(NOW)}','',
      f'- Darat valid: {len(land)}/{len(raw)}',
      f'- Laut valid: {len(sea)}/{len(MARINE_CODES)}',
      f'- Waktu dapat ditampilkan: {len(times)}',
      f'- Poligon resmi: {"tersedia" if polys else "GAGAL (tidak diganti koordinat buatan)"}',
      f'- Total baris gust laut < speed: {report["qc"]["marine_gust_anomalies"]}', '',
      '## Catatan ilmiah',
      '- Hasil bukan grid/streamline. Panah darat = prakiraan titik ADM4; simbol laut = rata-rata representatif wilayah perairan.',
      '- Arah laut dari kategori mata angin, bukan sudut presisi.',
      '- Tinggi referensi angin laut masih harus dikonfirmasi.',
      '- Sinkronisasi waktu bukan validasi akurasi.',
      '- Belum dipublikasikan pada GitHub Pages; hanya artifact Actions.',
      '- BMKG sebagai sumber data; izin kegiatan menjadi kewenangan otoritas terkait.', ''
    ]
    (OUT/'RINGKASAN_TAHAP3.md').write_text('\n'.join(overview),encoding='utf-8')
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f:f.write('\n'.join(overview))
    print('\n'.join(overview[:9]),flush=True)
    if not times:
        raise SystemExit('No valid common forecast times. Check source feeds and QC; no preview grid invented.')

if __name__=='__main__': build()
