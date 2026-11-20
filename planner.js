/* Shared by the offline demo and the HTTP API. All timetable values are IST. */
function plan(origin, destination, date, time) {
  const {patterns, trips, calendar, exceptions, model} = globalThis.PILOT;
  const weekday = ['sunday','monday','tuesday','wednesday','thursday','friday','saturday'];
  const midnight = Date.parse(date + 'T00:00:00Z');
  if (!Number.isFinite(midnight) || new Date(midnight).toISOString().slice(0,10) !== date ||
      !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) throw Error('Choose a valid date and time.');
  const known = new Set(Object.values(patterns).flatMap(p => p.names));
  if (!known.has(origin) || !known.has(destination) || origin === destination)
    throw Error('Choose two different stops from the pilot.');
  const [h,m] = time.split(':').map(Number);
  const start = h*3600 + m*60;
  const candidates = [];
  for (const shift of [-1,0,1]) {
    const day = new Date(midnight + shift*86400000);
    const key = day.toISOString().slice(0,10).replaceAll('-','');
    const services = new Set(calendar.filter(c => c.start_date <= key && key <= c.end_date &&
      c[weekday[day.getUTCDay()]] === '1').map(c => c.service_id));
    for (const e of exceptions.filter(e => e.date === key))
      if (e.exception_type === '1') services.add(e.service_id); else services.delete(e.service_id);
    for (const t of trips) if (services.has(t.service))
      candidates.push({t,p:patterns[t.p],shift:shift*86400});
  }
  const direct = [], transfers = [], first = new Map();
  function leg(c,i,j) {
    return {c,i,j,dep:c.t.d[i]+c.shift,arr:c.t.a[j]+c.shift};
  }
  for (const c of candidates) {
    c.p.names.forEach((name,i) => {
      const dep = c.t.d[i]+c.shift;
      if (name !== origin || dep < start || dep > start+7200) return;
      for (let j=i+1;j<c.p.ids.length;j++) {
        const part=leg(c,i,j);
        if (c.p.names[j]===destination) { direct.push([part]); break; }
        const key=c.p.ids[j];
        if (!first.has(key)) first.set(key,new Map());
        const routes=first.get(key), old=routes.get(c.p.label);
        if (!old || part.arr<old.arr) routes.set(c.p.label,part);
      }
    });
  }
  for (const c of candidates) {
    c.p.names.forEach((name,j) => {
      if (name!==destination) return;
      for (let i=0;i<j;i++) {
        const second=leg(c,i,j);
        for (const prev of first.get(c.p.ids[i])?.values() || []) {
          if (prev.c.p.label!==c.p.label && second.dep>=prev.arr+180 && second.arr<=start+10800)
            transfers.push([prev,second]);
        }
      }
    });
  }
  function describe({c,i,j,dep,arr}) {
    const segments=[];
    for(let k=i;k<j;k++) {
      const s=c.t.d[k]+c.shift;
      const day=new Date(midnight+s*1000).getUTCDay();
      const typ=day===0 || day===6 ? 'weekend':'weekday';
      const b=Math.floor((s%86400)/1800);
      const [label,mean,days]=model[[c.t.p,k,typ,b].join('|')] || ['Limited data',null,null];
      segments.push({from:c.p.names[k],to:c.p.names[k+1],label,mean,days});
    }
    const busiest=segments.reduce((best,s) => s.mean!==null && (!best || s.mean>best.mean) ? s:best,null);
    return {route:c.p.label,trip:c.t.id,from:c.p.names[i],to:c.p.names[j],dep,arr,segments,busiest,
      label:segments.some(s=>s.mean===null)?'Limited data':busiest.label};
  }
  function best(options,limit) {
    options.sort((a,b)=>a.at(-1).arr-b.at(-1).arr || b[0].dep-a[0].dep);
    const seen=new Set(), results=[];
    for(const path of options) {
      const key=path.map(l=>l.c.p.label).join('/');
      if(seen.has(key)) continue;
      seen.add(key);
      results.push({minutes:Math.ceil((path.at(-1).arr-start)/60),legs:path.map(describe)});
      if(results.length===limit) break;
    }
    return results;
  }
  return {direct:best(direct,6),transfers:best(transfers,3)};
}
if (typeof module!=='undefined') module.exports=plan;
