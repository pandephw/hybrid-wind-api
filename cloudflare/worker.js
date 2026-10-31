// Optional real parameterized API layer. Deploy in Cloudflare Workers (free tier).
// Replace USER below with your GitHub username. GitHub Pages build must already be live.
const DATA_ROOT = 'https://USER.github.io/hybrid-wind-api/v1/';
const CORS = {
  'Access-Control-Allow-Origin':'*',
  'Access-Control-Allow-Methods':'GET, OPTIONS',
  'Access-Control-Allow-Headers':'Content-Type',
  'Cache-Control':'public, max-age=180'
};
const send = (obj,status=200) => new Response(JSON.stringify(obj), {status,headers:{'Content-Type':'application/json; charset=utf-8',...CORS}});
async function upstream(path) {
  // Only call with hard-coded paths or files originating from times.json.
  const res=await fetch(DATA_ROOT+path,{cf:{cacheTtl:180,cacheEverything:true}});
  if(!res.ok)throw Error('Cache grid upstream HTTP '+res.status);
  return res.json();
}
function sample(g,lat,lon){
  const b=g.bounds_wsen, nx=g.nx, ny=g.ny;
  if(lat<b[1]||lat>b[3]||lon<b[0]||lon>b[2])return null;
  const x=(lon-b[0])/(b[2]-b[0])*(nx-1),y=(lat-b[1])/(b[3]-b[1])*(ny-1);
  const ix=Math.min(nx-2,Math.max(0,Math.floor(x))),iy=Math.min(ny-2,Math.max(0,Math.floor(y)));
  const a=x-ix,c=y-iy,idx=iy*nx+ix;
  const bilinear=values=>(1-c)*((1-a)*values[idx]+a*values[idx+1])+c*((1-a)*values[idx+nx]+a*values[idx+nx+1]);
  const u=bilinear(g.u_ms),v=bilinear(g.v_ms);
  if(!Number.isFinite(u)||!Number.isFinite(v))return null;
  return {latitude:lat,longitude:lon,u_ms:+u.toFixed(3),v_ms:+v.toFixed(3),
    wind_speed_kt:+(Math.hypot(u,v)*1.9438444924406).toFixed(2),
    wind_from_deg:+(((270-Math.atan2(v,u)*180/Math.PI)%360+360)%360).toFixed(1)};
}
export default {
  async fetch(request){
    if(request.method==='OPTIONS')return new Response(null,{status:204,headers:CORS});
    if(request.method!=='GET')return send({error:'GET only'},405);
    const url=new URL(request.url),path=url.pathname;
    try {
      if(path==='/v1/health'){
        const latest=await upstream('latest.json');
        const ageHours=(Date.now()-Date.parse(latest.generated_at_utc))/3600000;
        return send({status:ageHours<=18?'ok':'stale',generated_at_utc:latest.generated_at_utc,age_hours:+ageHours.toFixed(1),latest_time_utc:latest.latest.valid_time_utc});
      }
      if(path==='/v1/latest')return send(await upstream('latest.json'));
      if(path==='/v1/times')return send(await upstream('times.json'));
      if(path==='/v1/metadata')return send(await upstream('metadata.json'));
      if(path==='/v1/field'||path==='/v1/point'){
        const index=await upstream('times.json');
        const target=url.searchParams.get('time');
        const record=target?index.times.find(t=>t.valid_time_utc===target):index.times[0];
        if(!record)return send({error:'time not available; consult /v1/times'},404);
        if(!/^grids\/[0-9TZ]+\.json$/.test(record.file))return send({error:'invalid upstream file'},502);
        const grid=await upstream(record.file);
        if(path==='/v1/field')return send(grid);
        const lat=Number(url.searchParams.get('lat')),lon=Number(url.searchParams.get('lon'));
        if(!url.searchParams.has('lat')||!url.searchParams.has('lon')||!Number.isFinite(lat)||!Number.isFinite(lon))return send({error:'lat and lon are required'},400);
        const pt=sample(grid.grid,lat,lon);
        if(!pt)return send({error:'outside supported bounds'},422);
        return send({...pt,valid_time_utc:grid.valid_time_utc,method:'interpolation of experimental hybrid U/V grid',
          warning:'Not an official BMKG gridded forecast or permission/safety status.',sources:grid.sources});
      }
      return send({message:'WindKite Hybrid Wind API — prototype',endpoints:['/v1/health','/v1/latest','/v1/times','/v1/metadata','/v1/field?time=YYYY-MM-DDTHH:MMZ','/v1/point?lat=-8.75&lon=115.17']},200);
    }catch(err){return send({error:'Upstream unavailable; check GitHub Pages deployment',detail:String(err.message)},503)}
  }
};
