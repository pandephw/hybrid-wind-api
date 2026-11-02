#!/usr/bin/env python3
"""Audit read-only BMKG ADM4 and maritime: wind, time, QC.
No forecast field/grid is constructed or published.
Python 3.12 standard library only; designed for GitHub Actions workflow_dispatch.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
BASE_DIR = Path(__file__).resolve().parents[1]
OUT = Path('bmkg_surface_audit_tahap2')
OUT.mkdir(parents=True, exist_ok=True)
ADM4_FILE = BASE_DIR / 'adm4_bali_selatan.json'
MARINE_CODES = ['P.P.02', 'P.P.03', 'P.P.04', 'P.P.06']
UA = 'WindKite-Surface-BMKG-Audit/0.2 (+internal-methodology; read-only)'
DIRECTION = {'N':0, 'NNE':22.5, 'NE':45, 'ENE':67.5, 'E':90, 'ESE':112.5,
             'SE':135, 'SSE':157.5, 'S':180, 'SSW':202.5, 'SW':225, 'WSW':247.5,
             'W':270, 'WNW':292.5, 'NW':315, 'NNW':337.5,
             'UTARA':0, 'TIMUR LAUT':45, 'TIMUR':90, 'TENGGARA':135, 'SELATAN':180,
             'BARAT DAYA':225, 'BARAT':270, 'BARAT LAUT':315,
             'UTARA TIMUR LAUT':22.5, 'TIMUR TIMUR LAUT':67.5,
             'TIMUR TENGGARA':112.5, 'SELATAN TENGGARA':157.5,
             'SELATAN BARAT DAYA':202.5, 'BARAT BARAT DAYA':247.5,
             'BARAT BARAT LAUT':292.5, 'UTARA BARAT LAUT':337.5}


def fetch(url, attempts=2):
    error = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'Accept':'application/json','User-Agent':UA})
            with urllib.request.urlopen(req, timeout=24) as r:
                data = r.read(6_000_001)
                if len(data)>6_000_000: raise ValueError('JSON terlalu besar (>6 MB)')
                return json.loads(data.decode('utf-8-sig'))
        except (ValueError, OSError, urllib.error.URLError) as e:
            error = e
            if i+1<attempts: time.sleep(1.0)
    raise RuntimeError(f'HTTP/JSON gagal: {type(error).__name__}: {str(error)[:160]}')


def timestamp(s):
    if not s: return None
    text = str(s).strip().replace(' UTC','').replace(' ','T')
    if text.endswith('Z'): text=text[:-1]+'+00:00'
    try:
        v=dt.datetime.fromisoformat(text)
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    except (ValueError,TypeError): return None


def iso(t): return t.strftime('%Y-%m-%dT%H:%MZ') if t else None


def angle(raw):
    if isinstance(raw,(int,float)) and math.isfinite(raw) and 0<=raw<=360: return raw % 360
    if raw is None: return None
    s=str(raw).strip().upper()
    if s in DIRECTION: return DIRECTION[s]
    try:
        v=float(s)
        return v % 360 if math.isfinite(v) and 0<=v<=360 else None
    except ValueError: return None


def uv(speed_ms,from_deg):
    r=math.radians(from_deg)
    return (-speed_ms*math.sin(r),-speed_ms*math.cos(r))


def audit_land(site):
    code=site['code']; name=site.get('name',code)
    r={'adm4':code,'name_requested':name,'status':'ERROR','n_valid':0}
    try:
        url='https://api.bmkg.go.id/publik/prakiraan-cuaca?'+urllib.parse.urlencode({'adm4':code})
        obj=fetch(url)
        loc=obj.get('lokasi') or ((obj.get('data') or [{}])[0].get('lokasi') or {})
        returned=loc.get('adm4')
        if returned and str(returned)!=str(code): raise ValueError('ADM4 respons tidak cocok')
        rows={}; invalid=0; analysis_dates=[]
        for day in obj.get('data') or []:
            for entries in day.get('cuaca') or []:
                for x in entries:
                    t=timestamp(x.get('utc_datetime') or x.get('datetime'))
                    try:
                        speed=float(x['ws'])
                    except (KeyError,TypeError,ValueError): speed=float('nan')
                    direction=angle(x.get('wd_deg'))
                    if direction is None: direction=angle(x.get('wd'))
                    if t and math.isfinite(speed) and 0<=speed<=250 and direction is not None:
                        rows[t]=uv(speed/3.6,direction)
                        produced=timestamp(x.get('analysis_date'))
                        if produced:analysis_dates.append(produced)
                    else: invalid+=1
        if not rows: raise ValueError('Tidak ada baris angin valid')
        if loc.get('lat') is not None and loc.get('lon') is not None:
            lat,lon=float(loc['lat']),float(loc['lon'])
            if not (-9.5 <lat<-7.8 and 114.5<lon<116): raise ValueError('Koordinat lokasi di luar Bali Selatan')
        else: lat=lon=None
        r.update(status='OK',name_api=loc.get('desa') or loc.get('kelurahan') or name,
                 latitude=lat,longitude=lon,n_valid=len(rows),n_invalid=invalid,
                 first_time_utc=iso(min(rows)),last_time_utc=iso(max(rows)),
                 analysis_date_utc=iso(max(analysis_dates)) if analysis_dates else None)
        return r, rows
    except Exception as e:
        r['error']=str(e)[:300]
        return r, {}


def audit_sea(code):
    r={'code':code,'status':'ERROR','n_valid':0}
    try:
        obj=fetch(f'https://maritim.bmkg.go.id/marine2026-data/perairan/{code}.json')
        issued=timestamp(obj.get('issued')); begin=timestamp(obj.get('valid_from')); end=timestamp(obj.get('valid_to'))
        if not begin or not end or not begin<end: raise ValueError('Periode valid tidak sah')
        rows={}; unk=set(); bad=0; gust_below=0
        for x in (obj.get('forecast_day1') or [])+(obj.get('forecast_day2-4') or []):
            t=timestamp(x.get('time'))
            a=angle(x.get('wind_from'))
            if a is None and x.get('wind_from') is not None:unk.add(str(x.get('wind_from')))
            try: speed=float(x['wind_speed'])
            except (KeyError,ValueError,TypeError):speed=float('nan')
            try: gust=float(x['wind_gust'])
            except (KeyError,ValueError,TypeError):gust=float('nan')
            if math.isfinite(speed) and math.isfinite(gust) and gust+0.1<speed: gust_below+=1
            if t and begin<=t<end and math.isfinite(speed) and 0<=speed<=150 and a is not None:
                rows[t]=uv(speed*0.514444444444,a)
            else: bad+=1
        if not rows: raise ValueError('Tidak ada baris angin valid')
        r.update(status='OK',name=obj.get('name'),issued_utc=iso(issued),valid_from_utc=iso(begin),valid_to_utc=iso(end),
                 active_at_audit=begin<=NOW<end,n_valid=len(rows),n_invalid=bad,
                 first_time_utc=iso(min(rows)),last_time_utc=iso(max(rows)),
                 unknown_directions=sorted(unk),gust_less_than_wind_speed_rows=gust_below,
                 direction_resolution='kategori mata angin, bukan derajat presisi')
        return r, rows
    except Exception as e:
        r['error']=str(e)[:300]
        return r, {}


def sample_at(rows,t,allow_interpolation):
    if t in rows: return rows[t], 'exact'
    if not allow_interpolation: return None, 'none'
    before=max((x for x in rows if x<t),default=None)
    after=min((x for x in rows if x>t),default=None)
    if before is None or after is None: return None, 'none'
    gap=(after-before).total_seconds()
    # Interpolate only when two neighboring valid BMKG land samples are <= 3h apart.
    if gap<=0 or gap>3*3600: return None, 'none'
    f=(t-before).total_seconds()/gap
    v=rows[before]; w=rows[after]
    return (v[0]+f*(w[0]-v[0]),v[1]+f*(w[1]-v[1])), 'interpolated'


def main():
    if not ADM4_FILE.exists():
        raise SystemExit('Belum ada adm4_bali_selatan.json di root repo. Unggah file konfigurasi yang sudah dipakai Hybrid API lama.')
    sites=json.loads(ADM4_FILE.read_text(encoding='utf-8'))
    land_report=[]; land=[]
    for i,s in enumerate(sites):
        r,rows=audit_land(s);land_report.append(r)
        if rows: land.append(rows)
        print(f'Darat {i+1}/{len(sites)}: {s["code"]} {r["status"]}',flush=True)
        time.sleep(1.05)  # conserve source traffic
    sea_report=[]; sea=[]
    for code in MARINE_CODES:
        r,rows=audit_sea(code);sea_report.append(r)
        if rows and r.get('active_at_audit'): sea.append(rows)
        print(f'Laut {code}: {r["status"]}, active={r.get("active_at_audit")}',flush=True)
        time.sleep(.5)
    candidates=sorted(set().union(*(set(x) for x in sea)) if sea else set())
    # We only audit future to 48h; never generate/publish a combined field.
    candidates=[x for x in candidates if NOW<=x<=NOW+dt.timedelta(hours=48)]
    matched=[]
    for t in candidates:
        a=sum(1 for r in sea if t in r)
        exact=sum(1 for r in land if t in r)
        interpolated=sum(1 for r in land if t not in r and sample_at(r,t,True)[0] is not None)
        land_pass=exact+interpolated>=max(6,math.ceil(.60*len(sites)))
        sea_pass=a>=2
        matched.append({'time_utc':iso(t),'time_wita':(t+dt.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M WITA'),
                        'land_exact':exact,'land_temporal_interp':interpolated,'land_total':exact+interpolated,
                        'sea_exact':a,'candidate_qc':bool(land_pass and sea_pass)})
    flagged=sum(x.get('gust_less_than_wind_speed_rows',0) for x in sea_report)
    report={'audit_utc':iso(NOW),'purpose':'Feasibility and QC only. Not gridded or validated wind product.',
            'config_land_count':len(sites),'land':land_report,'sea':sea_report,'matching_times':matched,
            'qc_rules':{'land_min_sites':max(6,math.ceil(.60*len(sites))), 'sea_min_areas':2,
                        'land_interpolation':'U/V linear only between valid land times with gap <=3 h',
                        'sea_interpolation':'disabled; only exact timestamps',
                        'time_horizon':'next 48 hours from execution',
                        'direction_note':'Marine compass categories only; do not claim precise wind direction.',
                        'no_grid_published':True},
            'counts':{'land_ok':len(land),'sea_active_ok':len(sea),
                      'candidate_qc_times':sum(x['candidate_qc'] for x in matched),
                      'marine_gust_lower_than_speed_rows':flagged}}
    (OUT/'audit_sinkronisasi.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    L=['# Audit Sinkronisasi BMKG Darat–Laut (Tahap 2)', '', f'Eksekusi: **{iso(NOW)}**','',
       f'- Darat: **{len(land)}/{len(sites)}** ADM4 dengan angin valid.',
       f'- Laut: **{len(sea)}/{len(MARINE_CODES)}** perairan aktif dengan angin valid.',
       f'- Waktu calon yang lulus QC dasar (48 jam ke depan): **{report["counts"]["candidate_qc_times"]}**.',
       f'- Anomali gust < kecepatan angin laut (semua waktu yang diperiksa): **{flagged}** baris.',
       '', '## Daftar kecocokan waktu', '',
       '| Waktu WITA | Darat tepat | Darat interpolasi waktu | Laut tepat | QC kandidat |',
       '|---|---:|---:|---:|---|']
    for x in matched[:50]:
        L.append(f'| {x["time_wita"]} | {x["land_exact"]} | {x["land_temporal_interp"]} | {x["sea_exact"]} | {"LULUS" if x["candidate_qc"] else "BELUM"} |')
    L += ['', '## Pembatasan ilmiah',
          '- Lulus QC waktu **tidak sama dengan tervalidasi**: belum membuktikan angin darat dan laut kompatibel secara representativitas, metode, ketinggian, atau akurasi.',
          '- Angin laut diberikan per **poligon wilayah** dengan arah kategori; bukan grid model U/V resolusi tinggi.',
          '- Gust laut lebih kecil dari kecepatan angin adalah sinyal QC; jangan memperbaiki nilainya dengan mengarang.',
          '- Tinggi referensi angin laut belum dikonfirmasi; jangan labeli sebagai 10 m AGL tanpa klarifikasi.',
          '- Tahap ini **tidak mengganti maupun menerbitkan API Hybrid** yang sedang berjalan.',
          '', '## Keputusan selanjutnya',
          '- Jika ada jam QC memadai, susun prototype **BMKG-only berbasis poligon** (panah tiap area); tunda streamline gabungan sampai kualitas spasial diuji.',
          '- Konsultasi pengelola API maritim untuk referensi ketinggian, arti nilai gust, akses grid INAWAVES, dan hak redistribusi.',
          '- Verifikasi perbedaan BMKG darat–laut terhadap observasi AWS WADD pada jam valid yang sama.', '']
    summary='\n'.join(L)
    (OUT/'RINGKASAN_TAHAP2.md').write_text(summary,encoding='utf-8')
    github_file=os.environ.get('GITHUB_STEP_SUMMARY')
    if github_file:
        with open(github_file,'a',encoding='utf-8') as f:f.write(summary)
    print('\n'.join(L[:9]))

if __name__=='__main__':main()
