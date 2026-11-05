#!/usr/bin/env python3
"""Stage 4 upstream availability gate; stdlib only.

Reads the Stage 3 snapshot even when its builder signals 'no matching future times'.
Never substitutes stale forecasts or publishes degraded marine winds.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

UTC = dt.timezone.utc
EXPECTED_MARINE = {'P.P.02', 'P.P.03', 'P.P.04', 'P.P.06'}


def parse_time(s):
    if not isinstance(s, str):
        raise ValueError('Timestamp is missing')
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(UTC)


def check_snapshot(obj, now=None):
    now = now or dt.datetime.now(UTC)
    if obj.get('type') != 'BMKG_Surface_Prototype_Points_Polygons':
        raise ValueError('Unexpected Stage 3 payload: cannot validate QC')
    stamp = parse_time(obj.get('generated_utc'))
    if stamp < now-dt.timedelta(minutes=40) or stamp > now+dt.timedelta(minutes=2):
        raise ValueError('Stage 3 snapshot is older than 40 minutes or from future; refusing to reuse it')
    qc = obj.get('qc')
    if not isinstance(qc,dict) or not isinstance(obj.get('valid_times'),list):
        raise ValueError('Missing QC report or valid_times array')
    land = int(qc.get('valid_land',0))
    marine = int(qc.get('valid_marine',0))
    times = obj['valid_times']
    source = qc.get('marine_source_reports') or []
    areas = obj.get('marine_areas') or []
    sea_details = []
    if source:
        sea_details = [{'code':x.get('code'), 'status':x.get('status'),
                        'active_at_audit':x.get('active_at_audit'),
                        'issued_utc':x.get('issued_utc'), 'valid_from_utc':x.get('valid_from_utc'), 'valid_to_utc':x.get('valid_to_utc'),
                        'error':x.get('error')} for x in source]
    else:
        sea_details = [{'code':x.get('code'), 'status':'OK', 'active_at_audit':True,
                        'issued_utc':x.get('issued_utc'),'valid_from_utc':x.get('valid_from_utc'),'valid_to_utc':x.get('valid_to_utc')}
                       for x in areas]
    all_codes={str(x.get('code')) for x in areas}
    # No forecast output is allowed unless every marine area is current.
    if not times:
        status='WAITING_FOR_VALID_SOURCES'
        reason='No future timestamp with enough valid land sites and all 4 current marine areas.'
    elif (land>=17 and marine==4 and all_codes==EXPECTED_MARINE
          and (obj.get('marine_polygons') or {}).get('features')
          and len(times)>0):
        valid_future=sum(1 for x in times if parse_time(x.get('valid_utc'))>=now-dt.timedelta(minutes=15))
        if valid_future>0:
            status='READY'; reason=f'{valid_future} current/future common times available.'
        else:
            status='WAITING_FOR_VALID_SOURCES'; reason='All common forecast times are now past.'
    else:
        status='WAITING_FOR_VALID_SOURCES'
        reason='Time candidates exist, but source coverage/polygons fail readiness checks.'
    return {'status':status,'ready':status=='READY', 'generated_utc':obj['generated_utc'],
            'checked_utc':now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'reason':reason, 'land_valid':land,'marine_active':marine,
            'future_common_times':len(times), 'sea_details':sea_details,
            'marine_gust_anomaly_rows':qc.get('marine_gust_anomalies',None),
            'note':'No old marine forecasts are reused. This is a data availability gate, not forecast validation.'}


def markdown(report):
    lines=['## WindKite BMKG Surface — status sumber terbaru','',
           f'**{report["status"]}** — {report["reason"]}', '',
           f'- Data darat: **{report["land_valid"]}/27** sumber',
           f'- Data laut aktif: **{report["marine_active"]}/4** wilayah',
           f'- Waktu gabungan: **{report["future_common_times"]}**','',
           '| Area laut | Respons | Aktif | Dari (UTC) | Sampai (UTC) |',
           '|---|---|---|---|---|']
    for s in report['sea_details']:
        lines.append(f'| {s.get("code", "?")} | {s.get("status", "?")} | {s.get("active_at_audit", "?")} | {s.get("valid_from_utc") or "—"} | {s.get("valid_to_utc") or "—"} |')
    lines+=['',
        '> Jika data laut belum tersedia, pembuatan beta dilewati. Dashboard dan JSON lama tidak ditimpa. '
        'Workflow dapat selesai tanpa menerbitkan prakiraan baru. Status READY bukan validasi akurasi.',
        '']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',default='bmkg_surface_preview_tahap3/bmkg_surface_preview.json')
    p.add_argument('--output',default='bmkg_surface_preview_tahap3/availability.json')
    a=p.parse_args()
    src=Path(a.input)
    if not src.is_file():
        raise SystemExit('ERROR: Stage 3 snapshot missing; code/network failure, not a normal no-data condition.')
    try:
        report=check_snapshot(json.loads(src.read_text(encoding='utf-8')))
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as ex:
        print(f'ERROR: invalid QC snapshot: {ex}',file=sys.stderr)
        raise SystemExit(2)
    target=Path(a.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary=markdown(report)
    print(summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f: f.write(summary+'\n')
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as f: f.write('ready='+('true' if report['ready'] else 'false')+'\n')
    if not report['ready']:
        print('::warning title=BMKG Maritim not ready::No active matching sea forecast; beta product generation skipped safely.')

if __name__=='__main__':main()
