#!/usr/bin/env python3
"""Build static Hybrid Wind API snapshots, based on Wind-Kite v0.7.
Python standard library only. Requires live BMKG/Open-Meteo network when run normally.
No hard-coded weather observations; all forecasts must come from APIs.
"""
import argparse
from bisect import bisect_left
import datetime as dt
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parent
UTC=dt.timezone.utc
BMKG='https://api.bmkg.go.id/publik/prakiraan-cuaca'
GFS='https://api.open-meteo.com/v1/forecast'
GEM='https://api.open-meteo.com/v1/gem'
GEM_NATIVE_HEIGHTS=(10,40,80,120)
PUBLISHED_HEIGHTS=(10,20,30,40,50,80,120)
N_LAT,N_LON=9,11
LATS=[-9.25+i/8 for i in range(N_LAT)]
LONS=[114.75+i/10 for i in range(N_LON)]
POINTS=[(a,b) for a in LATS for b in LONS]
OUT_N=41
BBOX=[114.75,-9.25,115.75,-8.25]  # [west,south,east,north]
SPEED_KMH_TO_MS=1/3.6
KT_PER_MS=1.9438444924406


def request_json(url,attempts=3):
    error=None
    for i in range(attempts):
        try:
            req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'WindKite-Hybrid-Prototype/0.1 (data-source-attribution)'})
            with urllib.request.urlopen(req,timeout=35) as r:
                return json.load(r)
        except (urllib.error.URLError,TimeoutError,json.JSONDecodeError) as exc:
            error=exc
            if i+1<attempts: time.sleep(2*(i+1))
    raise RuntimeError(f'Permintaan gagal: {url.split("?")[0]} ({error})')


def wind_to_uv(speed_ms,direction_from_deg):
    r=math.radians(direction_from_deg)
    return -speed_ms*math.sin(r),-speed_ms*math.cos(r)


def uv_to_wind(u,v):
    return (math.hypot(u,v)*KT_PER_MS,(270-math.degrees(math.atan2(v,u)))%360)


def epoch_from_utc(string):
    if not string: raise ValueError('timestamp kosong')
    value=str(string).strip().replace(' ','T')
    if value.endswith('Z'):value=value[:-1]+'+00:00'
    d=dt.datetime.fromisoformat(value)
    if d.tzinfo is None:d=d.replace(tzinfo=UTC)
    return d.astimezone(UTC).strftime('%Y-%m-%dT%H:%MZ')


def parse_bmkg(body,code):
    location=body.get('lokasi') or (body.get('data') or [{}])[0].get('lokasi') or {}
    returned=location.get('adm4')
    if returned and str(returned)!=code:raise ValueError('ADM4 tidak sesuai')
    lat=float(location.get('lat',location.get('latitude','nan')))
    lon=float(location.get('lon',location.get('longitude','nan')))
    if not (-9.5<lat<-7.9 and 114.5<lon<116):raise ValueError('koordinat di luar Bali Selatan')
    dir_map={'N':0,'NNE':22.5,'NE':45,'ENE':67.5,'E':90,'ESE':112.5,'SE':135,'SSE':157.5,'S':180,'SSW':202.5,'SW':225,'WSW':247.5,'W':270,'WNW':292.5,'NW':315,'NNW':337.5}
    rows={}
    for day in body.get('data',[]):
        for group in day.get('cuaca',[]):
            for x in group:
                try:
                    t=epoch_from_utc(x.get('utc_datetime') or x.get('datetime'))
                    speed=float(x['ws']);degree=x.get('wd_deg')
                    if degree is None or degree=='':degree=dir_map.get(str(x.get('wd','')).upper())
                    degree=float(degree)
                    if not 0<=degree<=360 or not 0<=speed<=300:continue
                    rows[t]=wind_to_uv(speed*SPEED_KMH_TO_MS,degree)
                except (KeyError,ValueError,TypeError):continue
    if not rows:raise ValueError('tidak ada angin/waktu valid')
    return {'code':code,'name':str(location.get('desa') or location.get('kelurahan') or code),'lat':lat,'lon':lon,'times':rows}


def fetch_bmkg(codes):
    sites=[];failed=[]
    for i,p in enumerate(codes):
        code=p['code']
        try:
            body=request_json(BMKG+'?'+urllib.parse.urlencode({'adm4':code}),attempts=2)
            sites.append(parse_bmkg(body,code))
        except (ValueError,RuntimeError) as e:failed.append({'adm4':code,'error':str(e)})
        print(f'BMKG {i+1:02}/{len(codes)}: {len(sites)} berhasil, {len(failed)} gagal',flush=True)
        time.sleep(1.1)  # jauh di bawah batas 60 permintaan/menit/IP
    if len(sites)<6 or len(sites)/len(codes)<0.6:
        raise RuntimeError(f'GAGAL QC: {len(sites)}/{len(codes)} ADM4 valid; minimum 6 dan 60%. {failed[:5]}')
    return sites,failed


def fetch_gfs(cell_selection, height_m=10):
    if height_m not in (10,80): raise ValueError("Ketinggian GFS hanya 10 m atau 80 m")
    raw=[]
    for i in range(0,len(POINTS),20):
        batch=POINTS[i:i+20]
        q={
            'latitude':','.join(f'{p[0]:.5f}' for p in batch),
            'longitude':','.join(f'{p[1]:.5f}' for p in batch),
            'hourly':f'wind_speed_{height_m}m,wind_direction_{height_m}m',
            'models':'ncep_gfs_seamless','cell_selection':cell_selection,
            'wind_speed_unit':'ms','timezone':'UTC','forecast_days':'3'
        }
        j=request_json(GFS+'?'+urllib.parse.urlencode(q))
        group=j if isinstance(j,list) else [j]
        if len(group)!=len(batch):raise RuntimeError('Jumlah titik Open-Meteo tidak cocok')
        raw.extend(group)
        print(f'GFS {cell_selection}: {len(raw)}/{len(POINTS)}',flush=True)
    columns=[];times=None
    for r in raw:
        h=r.get('hourly') or {}
        t=h.get('time',[])
        sp=h.get(f'wind_speed_{height_m}m',[])
        wd=h.get(f'wind_direction_{height_m}m',[])
        if not t or len(sp)!=len(t) or len(wd)!=len(t):raise RuntimeError('GFS respons tidak lengkap')
        newtimes=[epoch_from_utc(x) for x in t]
        if times is None:times=newtimes
        if newtimes!=times:raise RuntimeError('Waktu GFS tidak sinkron antartitik')
        columns.append([wind_to_uv(float(s),float(d)) if s is not None and d is not None else (float('nan'),float('nan')) for s,d in zip(sp,wd)])
    return {t:[column[i] for column in columns] for i,t in enumerate(times)}


def clamp(x):return max(0.0,min(1.0,x))


def smooth(a,b,x):
    t=clamp((x-a)/(b-a));return t*t*(3-2*t)


def in_ring(lon,lat,ring):
    inside=False;j=len(ring)-1
    for i in range(len(ring)):
        xi,yi=ring[i];xj,yj=ring[j]
        if ((yi>lat)!=(yj>lat)) and (lon<(xj-xi)*(lat-yi)/(yj-yi)+xi):inside=not inside
        j=i
    return inside


def signed_ring_distance(lon,lat,rings):
    land=any(in_ring(lon,lat,r) for r in rings)
    best=float('inf');cs=math.cos(math.radians(lat))
    for ring in rings:
        for (x1,y1),(x2,y2) in zip(ring,ring[1:]):
            ax=(x1-lon)*111190*cs;ay=(y1-lat)*111190
            bx=(x2-lon)*111190*cs;by=(y2-lat)*111190
            dx=bx-ax;dy=by-ay
            t=clamp(-(ax*dx+ay*dy)/(dx*dx+dy*dy or 1))
            best=min(best,math.hypot(ax+t*dx,ay+t*dy))
    return best if land else -best


def bilinear(field,lat,lon):
    if not (LATS[0]<=lat<=LATS[-1] and LONS[0]<=lon<=LONS[-1]):return None
    iy=min(N_LAT-2,max(0,int((lat-LATS[0])/(LATS[1]-LATS[0]))))
    ix=min(N_LON-2,max(0,int((lon-LONS[0])/(LONS[1]-LONS[0]))))
    ty=clamp((lat-LATS[iy])/(LATS[iy+1]-LATS[iy]));tx=clamp((lon-LONS[ix])/(LONS[ix+1]-LONS[ix]))
    a,b,c,d=(field[iy*N_LON+ix],field[iy*N_LON+ix+1],field[(iy+1)*N_LON+ix],field[(iy+1)*N_LON+ix+1])
    if not all(math.isfinite(v) for pair in [a,b,c,d] for v in pair):return None
    return tuple((1-ty)*((1-tx)*a[k]+tx*b[k])+ty*((1-tx)*c[k]+tx*d[k]) for k in (0,1))


def hull(points):
    a=sorted({(p['lon'],p['lat']) for p in points})
    if len(a)<3:return []
    def cross(o,p,q):return (p[0]-o[0])*(q[1]-o[1])-(p[1]-o[1])*(q[0]-o[0])
    lower=[];upper=[]
    for p in a:
        while len(lower)>=2 and cross(lower[-2],lower[-1],p)<=0:lower.pop()
        lower.append(p)
    for p in a[::-1]:
        while len(upper)>=2 and cross(upper[-2],upper[-1],p)<=0:upper.pop()
        upper.append(p)
    return lower[:-1]+upper[:-1]


def inside_hull(x,y,poly):
    if len(poly)<3:return False
    pos=neg=False
    for a,b in zip(poly,poly[1:]+poly[:1]):
        v=(b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0])
        pos|=v>1e-11;neg|=v< -1e-11
        if pos and neg:return False
    return True


def hull_signed_distance(lon,lat,poly):
    if len(poly)<3:return -1e9
    dist=float('inf');cs=math.cos(math.radians(lat))
    for (x1,y1),(x2,y2) in zip(poly,poly[1:]+poly[:1]):
        ax=(x1-lon)*111190*cs;ay=(y1-lat)*111190
        dx=(x2-x1)*111190*cs;dy=(y2-y1)*111190
        t=clamp(-(ax*dx+ay*dy)/(dx*dx+dy*dy or 1))
        dist=min(dist,math.hypot(ax+t*dx,ay+t*dy))
    return dist if inside_hull(lon,lat,poly) else -dist


def site_uv_at_time(site,time_key,max_interval_hours=3.1):
    """Read or interpolate BMKG U/V in UTC; never extrapolate or bridge missing 3h cycles.

    BMKG ADM4 forecast windows can have different UTC-hour phases. We interpolate
    *vector components*, not direction in degrees, between adjacent valid slots.
    A 6-hour gap (missing 3-hour forecast) is rejected.
    """
    rows=site['times']
    if time_key in rows:return rows[time_key],'exact'
    keys=sorted(rows)
    i=bisect_left(keys,time_key)
    if i==0 or i==len(keys):return None,'outside_forecast'
    before,after=keys[i-1],keys[i]
    fmt='%Y-%m-%dT%H:%MZ'
    left=dt.datetime.strptime(before,fmt).replace(tzinfo=UTC)
    right=dt.datetime.strptime(after,fmt).replace(tzinfo=UTC)
    current=dt.datetime.strptime(time_key,fmt).replace(tzinfo=UTC)
    length=(right-left).total_seconds()
    if not (0<length<=max_interval_hours*3600):return None,'missing_3h_slot'
    alpha=(current-left).total_seconds()/length
    if not 0<alpha<1:return None,'invalid_bracket'
    a,b=rows[before],rows[after]
    if not all(math.isfinite(v) for v in a+b):return None,'invalid_uv'
    return ((1-alpha)*a[0]+alpha*b[0],(1-alpha)*a[1]+alpha*b[1]),'interpolated'


def candidate_valid_times(land,sea,now):
    """Produce 3-hour UTC forecast frames corresponding to 00,03,... WITA."""
    common=sorted(set(land)&set(sea))
    available=[]
    for key in common:
        timestamp=dt.datetime.strptime(key,'%Y-%m-%dT%H:%MZ').replace(tzinfo=UTC)
        if timestamp<now-dt.timedelta(hours=1):continue
        if (timestamp.hour+8)%3!=0:continue
        available.append(key)
    return available


def build_grid(time_key,land,sea,sites,rings,strength,coast_km):
    points=[]
    exact_count=0
    interpolated_count=0
    for site in sites:
        uv,temporal_method=site_uv_at_time(site,time_key)
        if uv is None:continue
        base=bilinear(land,site['lat'],site['lon'])
        if base is None:continue
        points.append({**site,'du':uv[0]-base[0],'dv':uv[1]-base[1]})
        if temporal_method=='exact':exact_count+=1
        else:interpolated_count+=1
    if len(points)<6 or len(points)/len(sites)<0.6:
        raise RuntimeError(f'QC jam tidak mencukupi: {len(points)}/{len(sites)} '
                           f'(exact={exact_count}, temporal_interp={interpolated_count}); '
                           'minimum 6 dan 60% titik')
    boundary=hull(points)
    if len(boundary)<3:raise RuntimeError('titik terlalu mengelompok untuk interpolasi')
    out_u=[];out_v=[];bmkg_weight_sum=0
    for iy in range(OUT_N):
        lat=BBOX[1]+(BBOX[3]-BBOX[1])*iy/(OUT_N-1)
        for ix in range(OUT_N):
            lon=BBOX[0]+(BBOX[2]-BBOX[0])*ix/(OUT_N-1)
            uv_l=bilinear(land,lat,lon);uv_s=bilinear(sea,lat,lon)
            if uv_l is None or uv_s is None:raise RuntimeError('GFS mengandung missing grid')
            coast=signed_ring_distance(lon,lat,rings)
            wland=smooth(-coast_km*1000,coast_km*1000,coast)
            base_u=uv_s[0]*(1-wland)+uv_l[0]*wland
            base_v=uv_s[1]*(1-wland)+uv_l[1]*wland
            sw=su=sv=0.0;nearest=float('inf')
            for site in points:
                dx=(lon-site['lon'])*111190*math.cos(math.radians(lat));dy=(lat-site['lat'])*111190
                d2=dx*dx+dy*dy;nearest=min(nearest,math.sqrt(d2));weight=1/max(d2,250000)
                sw+=weight;su+=weight*site['du'];sv+=weight*site['dv']
            support=smooth(-4000,1500,hull_signed_distance(lon,lat,boundary))*math.exp(-(nearest/18000)**4)
            wb=wland*support*strength;bmkg_weight_sum+=wb
            out_u.append(round(base_u+wb*su/sw,4));out_v.append(round(base_v+wb*sv/sw,4))
    return {
        'api_version':'0.1','product':'WindKite experimental hybrid wind 10m',
        'valid_time_utc':time_key,'timezone':'UTC','grid':{
            'bounds_wsen':BBOX,'nx':OUT_N,'ny':OUT_N,'u_ms':out_u,'v_ms':out_v,
            'order':'south-to-north rows; west-to-east columns; index=y*nx+x'},
        'quality':{'bmkg_sites_valid_for_time':len(points),'bmkg_sites_total':len(sites),'bmkg_sites_exact_time':exact_count,'bmkg_sites_temporally_interpolated':interpolated_count,'coastline':'indikatif v0.7','bmkg_mean_grid_weight':round(bmkg_weight_sum/(OUT_N*OUT_N),4)},
        'method':{'baseline':'Open-Meteo ncep_gfs_seamless, cell_selection=land & sea','correction':'IDW GFS-land residual at BMKG ADM4 source sites','coastal_transition_km':coast_km,'bmkg_strength':strength,'time_alignment':'BMKG vector U/V linearly interpolated between adjacent forecast slots <= 3.1 h; no extrapolation'},
        'license_note':'Experimental; bukan prakiraan gridded resmi BMKG; bukan dasar perizinan/keselamatan.',
        'sources':[{'name':'BMKG','url':'https://data.bmkg.go.id/prakiraan-cuaca/'},{'name':'Open-Meteo','url':'https://open-meteo.com/','license':'CC BY 4.0'}]
    }


def fetch_gem(cell_selection):
    """Read *native* GEM Global wind at 10, 40, 80, 120 m AGL.

    Open-Meteo interpolates GEM Global native 3h forecasts to hourly timestamps.
    This is *temporal* interpolation by provider, not an extra observed height.
    """
    raw=[]
    for i in range(0,len(POINTS),20):
        batch=POINTS[i:i+20]
        variables=','.join(x for h in GEM_NATIVE_HEIGHTS for x in (f'wind_speed_{h}m',f'wind_direction_{h}m'))
        query={
            'latitude':','.join(f'{p[0]:.5f}' for p in batch),
            'longitude':','.join(f'{p[1]:.5f}' for p in batch),
            'hourly':variables,'models':'gem_global',
            'cell_selection':cell_selection,'wind_speed_unit':'ms',
            'timezone':'UTC','forecast_days':'3'
        }
        body=request_json(GEM+'?'+urllib.parse.urlencode(query))
        group=body if isinstance(body,list) else [body]
        if len(group)!=len(batch):raise RuntimeError('Jumlah titik GEM tidak sesuai permintaan')
        raw.extend(group)
        print(f'GEM {cell_selection}: {len(raw)}/{len(POINTS)}',flush=True)
    times=None
    columns={h:[] for h in GEM_NATIVE_HEIGHTS}
    for r in raw:
        fields=r.get('hourly') or {}
        t=fields.get('time',[])
        if not t:raise RuntimeError('GEM: timestamp kosong')
        newtimes=[epoch_from_utc(x) for x in t]
        if times is None:times=newtimes
        if newtimes!=times:raise RuntimeError('GEM: jam antartitik tidak sinkron')
        for h in GEM_NATIVE_HEIGHTS:
            sp=fields.get(f'wind_speed_{h}m') or []
            wd=fields.get(f'wind_direction_{h}m') or []
            if len(sp)!=len(times) or len(wd)!=len(times):
                raise RuntimeError(f'GEM: parameter {h} m tidak tersedia untuk semua jam')
            vec=[]
            for speed,direction in zip(sp,wd):
                if speed is None or direction is None:
                    vec.append((float('nan'),float('nan')))
                else:
                    speed=float(speed);direction=float(direction)
                    if not(math.isfinite(speed) and math.isfinite(direction) and 0<=speed<=120 and 0<=direction<=360):
                        vec.append((float('nan'),float('nan')))
                    else:vec.append(wind_to_uv(speed,direction))
            columns[h].append(vec)
    return {t:{h:[columns[h][i][j] for i in range(len(POINTS))] for h in GEM_NATIVE_HEIGHTS}
            for j,t in enumerate(times)}


def log_profile_uv(native_profile,z):
    """Interpolate GEM component profiles in ln(height), no extrapolation."""
    if z in native_profile:return native_profile[z]
    if not 10<=z<=120:raise ValueError('Tinggi di luar profil GEM 10–120 m')
    for lo,hi in zip(GEM_NATIVE_HEIGHTS[:-1],GEM_NATIVE_HEIGHTS[1:]):
        if lo<=z<=hi:
            fraction=math.log(z/lo)/math.log(hi/lo)
            a,b=native_profile[lo],native_profile[hi]
            return tuple((1-fraction)*a[i]+fraction*b[i] for i in (0,1))
    raise ValueError('Tidak ada dua lapisan GEM yang mengapit tinggi')


def residual_height_weight(height_m):
    """Transparent *assumed* taper of the hybrid10-GEM10 bias, not measured shear."""
    if not 10<=height_m<=120:raise ValueError('Tinggi di luar 10-120m')
    return clamp(1-math.log(height_m/10)/math.log(120/10))


def attach_vertical_layers(frame, gem_land, gem_sea, rings, coast_km):
    """Directly USE GEM 10/40/80/120m and transfer its vertical shear to hybrid10.

    40 m is a *native GEM model height*, corrected by a documented residual taper.
    20, 30, 50 m are log-height interpolations of GEM U/V profile.
    Do not silently fill missing GEM layers with GFS historical values.
    """
    g=frame['grid'];n=g['nx'];bb=g['bounds_wsen']
    layers={str(h):{'u_ms':[],'v_ms':[]} for h in PUBLISHED_HEIGHTS}
    for iy in range(n):
        lat=bb[1]+(bb[3]-bb[1])*iy/(n-1)
        for ix in range(n):
            lon=bb[0]+(bb[2]-bb[0])*ix/(n-1)
            index=iy*n+ix
            coast=signed_ring_distance(lon,lat,rings)
            wland=smooth(-coast_km*1000,coast_km*1000,coast)
            native={}
            for h in GEM_NATIVE_HEIGHTS:
                land=bilinear(gem_land[h],lat,lon)
                sea=bilinear(gem_sea[h],lat,lon)
                if land is None or sea is None:
                    raise RuntimeError(f'GEM {h} m: data hilang atau tidak valid pada grid')
                native[h]=(sea[0]*(1-wland)+land[0]*wland,
                           sea[1]*(1-wland)+land[1]*wland)
            hybrid10=(g['u_ms'][index],g['v_ms'][index])
            bias=(hybrid10[0]-native[10][0], hybrid10[1]-native[10][1])
            for h in PUBLISHED_HEIGHTS:
                u,v=log_profile_uv(native,h)
                weight=residual_height_weight(h)
                layers[str(h)]['u_ms'].append(round(u+weight*bias[0],4))
                layers[str(h)]['v_ms'].append(round(v+weight*bias[1],4))
    # Preserve the already-proven hybrid10m anchor EXACTLY (including rounding).
    layers['10']={'u_ms':g['u_ms'][:],'v_ms':g['v_ms'][:]}
    for h in PUBLISHED_HEIGHTS:
        vv=layers[str(h)]['u_ms']+layers[str(h)]['v_ms']
        if len(vv)!=2*n*n or not all(map(math.isfinite,vv)):
            raise RuntimeError(f'QC wind_layers {h} m gagal')
    g['wind_layers']=layers
    frame['api_version']='0.3'
    frame['product']='WindKite experimental hybrid wind with GEM native vertical profile'
    frame['method']['vertical']={
        'model':'GEM Global via Open-Meteo /v1/gem',
        'native_model_levels_m_agl':list(GEM_NATIVE_HEIGHTS),
        'hybrid_anchored_level_m_agl':10,
        'interpolated_levels_m_agl':[20,30,50],
        'method':'GEM model U/V at 10/40/80/120m; log-height interpolation on GEM components; taper of hybrid10-GEM10 vector bias with height',
        'bias_taper':'w(z)=max(0,1-ln(z/10)/ln(120/10)); chosen research assumption, not calibrated',
        'note':'40/80/120m fields are native GEM model outputs before hybrid bias correction; published 40/80m are GEM-based adjusted fields, not observations. GEM global 3h native output is made hourly by Open-Meteo.',
        'limitation':'Not a high-resolution vertical observation; needs validation at Bali coastal sites.'
    }
    frame['quality']['gem_native_height_data_complete']=True
    return frame


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--strength',type=float,default=1.0)
    ap.add_argument('--coast-km',type=float,default=3.0)
    ap.add_argument('--output',default=str(ROOT/'public/v1'))
    args=ap.parse_args()
    if not 0<=args.strength<=1 or not 1<=args.coast_km<=8:ap.error('strength harus 0–1; coast-km 1–8')
    codes=json.loads((ROOT/'adm4_bali_selatan.json').read_text(encoding='utf-8'))
    rings=json.loads((ROOT/'geo/coast_indikatif_v07.json').read_text())['rings']
    sites,failed=fetch_bmkg(codes)
    land=fetch_gfs('land');sea=fetch_gfs('sea')
    gem_land=fetch_gem('land');gem_sea=fetch_gem('sea')
    now=dt.datetime.now(UTC)
    common=[t for t in candidate_valid_times(land,sea,now) if t in gem_land and t in gem_sea]
    print(f'Sinkronisasi: {len(common)} kandidat waktu GFS per 3 jam WITA, BMKG {len(sites)} lokasi', flush=True)
    out=Path(args.output);(out/'grids').mkdir(parents=True,exist_ok=True)
    available=[];errors=[]
    for t in common:
        try:
            obj=build_grid(t,land[t],sea[t],sites,rings,args.strength,args.coast_km)
            obj=attach_vertical_layers(obj,gem_land[t],gem_sea[t],rings,args.coast_km)
            stamp=t.replace('-','').replace(':','')+'.json'
            (out/'grids'/stamp).write_text(json.dumps(obj,separators=(',',':'))+'\n',encoding='utf-8')
            available.append({'valid_time_utc':t,'file':'grids/'+stamp,'bmkg_sites':obj['quality']['bmkg_sites_valid_for_time'],
                              'bmkg_sites_temporally_interpolated':obj['quality']['bmkg_sites_temporally_interpolated']})
        except (RuntimeError,ValueError,OverflowError,ZeroDivisionError) as e:errors.append({'time':t,'error':str(e)})
    if not available:
        raise RuntimeError('TIDAK DIPUBLIKASIKAN: tidak ada waktu cocok & valid! '+
                           str(errors[:8])+
                           '; periksa jarak waktu BMKG dan cakupan titik serta time zone UTC')
    current=dt.datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    latest={'api_version':'0.3','generated_at_utc':current,'latest':available[0],
            'available_times':len(available),'bmkg_sites_valid':len(sites),
            'configured_sites':len(codes),'failed_adm4':failed,'failed_times':errors,
            'warnings':['Eksperimental; bukan data gridded BMKG resmi.','Profil vertikal GEM (native 10/40/80/120m) terkoreksi dan 20/30/50m terinterpolasi; belum tervalidasi.','Data cuaca bukan izin atau keputusan keselamatan kegiatan; otoritas berwenang menilai perizinan.'],
            'attribution':{'BMKG':'https://data.bmkg.go.id/prakiraan-cuaca/','Open-Meteo':'https://open-meteo.com/ (CC BY 4.0)'}}
    (out/'latest.json').write_text(json.dumps(latest,indent=2,ensure_ascii=False)+'\n')
    (out/'times.json').write_text(json.dumps({'generated_at_utc':current,'times':available},indent=2)+'\n')
    (out/'metadata.json').write_text(json.dumps({'api_version':'0.3','product':'WindKite Hybrid Wind API prototype',
        'domain_wsen':BBOX,'grid_shape':[OUT_N,OUT_N],
        'wind_heights_m_agl':list(PUBLISHED_HEIGHTS),
        'wind_heights_estimated_m_agl':[20,30,50],
        'wind_heights_native_gem_m_agl':list(GEM_NATIVE_HEIGHTS),
        'model_vertical':'gem_global',
        'method_vertical':'GEM U/V vertical profile, log-level interpolation plus tapered hybrid10 residual correction',
        'documentation':'Read latest.json for available time and then GET file from times.json. Endpoints are static JSON on GitHub Pages.',
        'model':'ncep_gfs_seamless + gem_global','method':'Hybrid GFS sea/land with BMKG U/V residual IDW, coastal blending',
        'source_attribution':latest['attribution'],'legal_disclaimer':latest['warnings']},indent=2,ensure_ascii=False)+'\n')
    # Preserve public home page when supplied by the repository.
    home=out.parent/'index.html'
    if not home.exists():
        home.write_text('<!doctype html><meta charset="utf-8"><title>WindKite Hybrid Wind API</title><h1>WindKite Hybrid Wind API</h1><p>Eksperimental. Bukan produk prakiraan resmi BMKG.</p><p><a href="v1/latest.json">Latest</a> · <a href="v1/times.json">Times</a> · <a href="v1/metadata.json">Metadata</a></p><p>Sources: BMKG and Open-Meteo (CC BY 4.0).</p>')
    print(f'SUKSES: {len(available)} grid hybrid dipublikasikan pada {out}; invalid ADM4: {len(failed)}; invalid time: {len(errors)}')

if __name__=='__main__':main()
