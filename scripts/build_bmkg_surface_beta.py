#!/usr/bin/env python3
"""Build a NON-GRID, non-operational BMKG-only surface wind beta preview/API.

Input: Stage-3 snapshot produced by scripts/build_bmkg_surface_preview.py.
Uses stdlib only. Does not fetch independent model fields, interpolate spatially,
or certify/validate BMKG downstream forecasts.
"""
import argparse
import datetime as dt
import json
import math
import pathlib
import re
import sys

UTC=dt.timezone.utc
BETA_STATUS='EXPERIMENTAL_PROCESSED_BMKG_SOURCE_NOT_OFFICIAL'
SOURCE_WARNING=(
 'Produk olahan eksperimental: sumber prakiraan BMKG; penggabungan dan visualisasi '
 'belum divalidasi sebagai produk resmi BMKG. Tidak untuk perizinan layang-layang '
 'atau keputusan keselamatan penerbangan. Arah laut = kategori mata angin, '
 'bukan arah presisi. Tinggi referensi angin laut belum terkonfirmasi.'
)

def timestamp(text):
    if not isinstance(text,str): raise ValueError('Invalid time field')
    return dt.datetime.fromisoformat(text.replace('Z','+00:00')).astimezone(UTC)

def to_utc(d): return d.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')

def dump(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def read(path): return json.loads(path.read_text(encoding='utf-8'))

def valid_snapshot(data):
    if data.get('type')!='BMKG_Surface_Prototype_Points_Polygons': raise ValueError('Stage 3 snapshot type unexpected')
    sites=data.get('land_sites',[]); seas=data.get('marine_areas',[]); times=data.get('valid_times',[])
    if len(sites)<17 or len(seas)<4: raise ValueError('Insufficient land/marine sources')
    if not data.get('marine_polygons',{}).get('features'): raise ValueError('Official marine polygons unavailable')
    if not isinstance(times,list) or not times: raise ValueError('No forecast times available')
    codes={s['code'] for s in seas}
    if codes!={'P.P.02','P.P.03','P.P.04','P.P.06'}: raise ValueError('Unexpected marine area set')
    uniq=set()
    for item in times:
        t=item.get('valid_utc')
        if t in uniq: raise ValueError('Duplicate forecast time')
        uniq.add(t);timestamp(t)
        if item.get('marine_exact')!=4: raise ValueError('Marine time not exact')
        if len(item.get('marine',[]))!=4 or len(item.get('land',[]))<17:raise ValueError('QC coverage short')
        if item['land_exact']+item['land_temporal_interp']!=len(item['land']):raise ValueError('Land time QC mismatch')
        for x in item['land']+item['marine']:
            if not all(math.isfinite(float(x[k])) for k in ['u_ms','v_ms','speed_kt']):raise ValueError('Non-finite vector')
            if x.get('from_deg') is not None and not (0<=float(x['from_deg'])<=360):raise ValueError('Invalid direction')
    return sites,seas,times

def build(inp,out,template,keep_past=False):
    data=read(inp)
    sites,seas,times=valid_snapshot(data)
    now=dt.datetime.now(UTC)
    # Strictly future. Past times never advertised as currently usable.
    selected=sorted((x for x in times if keep_past or timestamp(x['valid_utc'])>=now-dt.timedelta(minutes=15)),key=lambda x:x['valid_utc'])
    if not selected:
        raise ValueError('Snapshot contains no current/future forecast times. Re-run Stage 3 fetch; refusing to publish stale forecast')
    out=pathlib.Path(out)
    v1=out/'v1'
    (v1/'vectors').mkdir(parents=True,exist_ok=True)
    src_times=[]
    for item in selected:
        ts=timestamp(item['valid_utc'])
        safe=ts.strftime('%Y%m%dT%H%MZ')
        name=f'vectors/{safe}.json'
        land_by={s['adm4']:s for s in sites}
        marine_by={s['code']:s for s in seas}
        land=[{**x,'name':land_by[x['adm4']]['name'],'lat':land_by[x['adm4']]['lat'],'lon':land_by[x['adm4']]['lon'], 'source':'BMKG_ADM4'} for x in item['land']]
        marine=[{**x,'name':marine_by[x['code']]['name'],'source':'BMKG_MARITIM_PERAIRAN'} for x in item['marine']]
        obj={'product':'WindKite BMKG Surface Vectors Beta','status':BETA_STATUS,
             'valid_time_utc':item['valid_utc'],'valid_time_wita':item['valid_wita'],
             'generated_from_snapshot_utc':data['generated_utc'],
             'spatial_support':'land ADM4 points and marine forecast-area polygons; NOT a model wind grid',
             'sea_direction_precision':'sector_center_approximation',
             'marine_reference_height':'UNVERIFIED',
             'units':{'u_v':'m/s','speed':'knots','direction':'degrees_FROM'},
             'qc':{'land_sites':len(land),'land_exact':item['land_exact'],
                   'land_temporal_interpolated':item['land_temporal_interp'],
                   'marine_areas_exact':len(marine)},
             'land':land,'marine':marine,'warning':SOURCE_WARNING}
        dump(v1/name,obj)
        src_times.append({'valid_time_utc':item['valid_utc'], 'valid_time_wita':item['valid_wita'],
                          'file':name,'qc':obj['qc']})
    last=selected[-1]['valid_utc']
    dump(v1/'metadata.json',{
        'api_version':'0.1-beta','product':'WindKite BMKG Surface Vectors', 'status':BETA_STATUS,
        'kind':'wind_vectors_points_and_marine_polygons_NOT_GRID',
        'build_utc':to_utc(now),'source_snapshot_utc':data['generated_utc'],
        'upstream_sources':data.get('sources',{}),
        'land_site_count':len(sites),'marine_area_count':len(seas),
        'wind_height':{'land':'10 m per project config; confirm API metadata','marine':'not verified'},
        'marine_direction':'Compass categories converted to sector center; not exact degrees',
        'time_method':'Marine exact only; land exact or bracketed U/V linear time interpolation <=3h',
        'spatial_method':'No spatial blending; no gridded wind field; geographic polygons are forecast areas not coastline',
        'forecast_valid_through_utc':last,
        'attribution':'Data prakiraan: BMKG Prakiraan Cuaca & BMKG Maritim; visualisasi/olah eksperimental WindKite',
        'redistribution_note':'Verify permission and institutional authorization before publicly hosting redistributed data',
        'warning':SOURCE_WARNING
    })
    dump(v1/'times.json',{'status':BETA_STATUS,'source_snapshot_utc':data['generated_utc'],
                           'times':src_times,'count':len(src_times)})
    dump(v1/'latest.json',{'status':BETA_STATUS,'source_snapshot_utc':data['generated_utc'],
                          'build_utc':to_utc(now),'first_valid_utc':selected[0]['valid_utc'],
                          'valid_through_utc':last,'latest_vector_file':src_times[0]['file'],
                          'metadata_file':'metadata.json','times_file':'times.json','marine_polygons_file':'areas.geojson',
                          'is_live_data':False,'warning':SOURCE_WARNING})
    dump(v1/'areas.geojson',data['marine_polygons'])
    # Offline-friendly html: inline snapshot avoids file:// fetch restrictions.
    data_for_html={**data,'valid_times':selected,'beta_status':BETA_STATUS,
                   'beta_warning':SOURCE_WARNING,'build_utc':to_utc(now)}
    original=template.read_text(encoding='utf-8')
    if '/*__DATA_JSON__*/' not in original: raise ValueError('Missing data inject marker')
    inline=json.dumps(data_for_html,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    html=original.replace('/*__DATA_JSON__*/',inline)
    (out/'index.html').write_text(html,encoding='utf-8')
    return {'times':len(selected),'land':len(sites),'marine':len(seas),'first':selected[0]['valid_utc'], 'last':last}

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--input',default='bmkg_surface_preview_tahap3/bmkg_surface_preview.json')
    p.add_argument('--output',default='bmkg_surface_beta_tahap4')
    p.add_argument('--template',default='scripts/bmkg_surface_beta_template.html')
    p.add_argument('--keep-past',action='store_true',help='Offline test only; not for publishing')
    a=p.parse_args()
    try:
        result=build(pathlib.Path(a.input),pathlib.Path(a.output),pathlib.Path(a.template),a.keep_past)
        print('BUILD OK',json.dumps(result))
    except Exception as e:
        print('BUILD BLOCKED:',e,file=sys.stderr);sys.exit(2)
