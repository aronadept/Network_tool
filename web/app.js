const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=t=>t?new Date(t*1000).toLocaleString():'Pending';
async function mutate(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const body=await r.json();if(!r.ok)throw Error(body.error);await refresh();}
async function refresh(){try{const r=await fetch('/api/state');if(!r.ok)throw Error('Server unavailable');const s=await r.json();$('health').textContent=s.ping_available?`Polling every ${s.interval}s`:'Ping unavailable — install iputils-ping';
const subnetInput=$('discovery-form').elements.subnet;if(subnetInput.dataset.initialized!=='true'){subnetInput.value=s.discovery_subnet||'';subnetInput.dataset.initialized='true';}
$('discovery-status').textContent=s.discovery_subnet?'Discovery enabled: '+s.discovery_subnet:'Discovery disabled';
const progress=s.discovery_progress;if(progress)$('scan-progress').textContent=`Scan ${progress.status}: ${progress.checked.toLocaleString()} / ${progress.total.toLocaleString()} addresses checked · ${progress.found} responding`+(progress.error?' · '+progress.error:'');
$('summary').innerHTML=[['Total devices',s.devices.length],['Online',s.devices.filter(d=>d.status==='up').length],['Offline',s.devices.filter(d=>d.status==='down').length],['Connections',s.links.length]].map(([k,v])=>`<div class="stat">${k}<strong>${v}</strong></div>`).join('');
$('devices').innerHTML=s.devices.map(d=>`<tr><td>${esc(d.name)}</td><td>${esc(d.ip)}</td><td>${esc(d.kind)}<br>${esc(d.brand)} ${esc(d.model)}</td><td class="${esc(d.status)}">${esc(d.status)}</td><td>${d.latency==null?'—':d.latency+' ms'}</td><td>${date(d.checked_at)}</td><td><button data-device="${d.id}">Remove</button></td></tr>`).join('')||'<tr><td colspan="7">No devices yet. Open Add / Discover devices to scan a subnet or add multiple addresses.</td></tr>';
for(const id of ['source','target']){const prev=$(id).value;$(id).innerHTML=s.devices.map(d=>`<option value="${d.id}">${esc(d.name)}</option>`).join('');if(s.devices.some(d=>String(d.id)===prev))$(id).value=prev;}
$('events').innerHTML=s.events.map(e=>`<div class="event"><span class="${esc(e.status)}">${esc(e.status.toUpperCase())}</span> · ${esc(e.name)} · ${date(e.created_at)}</div>`).join('')||'<p>No status changes recorded yet.</p>';
const positions={};s.devices.forEach((d,i)=>{const a=2*Math.PI*i/Math.max(s.devices.length,1);positions[d.id]=[450+310*Math.cos(a),225+155*Math.sin(a)];});
let svg=s.links.map(l=>{const a=positions[l.source],b=positions[l.target];return `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#435974" stroke-width="2"/><text x="${(a[0]+b[0])/2}" y="${(a[1]+b[1])/2}" fill="#a9b8cb" text-anchor="middle" font-size="11">${esc(l.label)}</text>`;}).join('');
svg+=s.devices.map(d=>{const [x,y]=positions[d.id];const c=d.status==='up'?'#48d6bb':d.status==='down'?'#fb7c83':'#efc56b';return `<g><title>${esc(d.name)} · ${esc(d.ip)} · ${esc(d.status)}</title><circle cx="${x}" cy="${y}" r="23" fill="#15263d" stroke="${c}" stroke-width="2"/><text x="${x}" y="${y+4}" fill="${c}" text-anchor="middle" font-size="11">${esc(d.kind.toUpperCase().slice(0,3))}</text><text x="${x}" y="${y+42}" fill="#dce6f3" text-anchor="middle" font-size="12">${esc(d.name)}</text></g>`;}).join('');$('topology').innerHTML=svg||'<text x="450" y="225" text-anchor="middle" fill="#91a3bc">Your network diagram will appear here</text>';
$('links').innerHTML=s.links.map(l=>`<button data-link="${l.id}">Remove link: ${esc(l.label||l.id)}</button>`).join('');
}catch(e){$('health').textContent='Connection lost';$('message').textContent=e.message;}}
for(const [id,path] of [['discovery-form','/api/discovery'],['device-form','/api/devices'],['link-form','/api/links']])$(id).onsubmit=async e=>{e.preventDefault();try{await mutate(path,Object.fromEntries(new FormData(e.target)));if(id!=='discovery-form')e.target.reset();$('message').textContent=id==='discovery-form'?'Discovery settings saved. Scan starts in the background; next scan runs 5 minutes after completion.':'Saved';}catch(e){$('message').textContent=e.message;}};
function showView(view){
 for(const name of ['dashboard','topology','manage'])$('view-'+name).hidden=name!==view;
 document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));
}
$('bulk-form').onsubmit=async e=>{
 e.preventDefault();const button=e.target.querySelector('button');
 const addresses=[...new Set(e.target.elements.addresses.value.split(/[\s,;]+/).filter(Boolean))];
 if(addresses.length>256){$('message').textContent='Please add at most 256 addresses at a time.';return;}
 if(addresses.some(ip=>!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)||ip.split('.').some(n=>Number(n)>255))){$('message').textContent='Enter valid IPv4 addresses only. Nothing was added.';return;}
 button.disabled=true;let added=0,skipped=0;const failed=[];
 try{
  const stateResponse=await fetch('/api/state');if(!stateResponse.ok)throw Error('Cannot read inventory');
  const state=await stateResponse.json();const existing=new Set(state.devices.map(d=>d.ip));
  for(const ip of addresses){
   if(existing.has(ip)){skipped++;continue;}
   $('message').textContent=`Adding devices: ${added+skipped+failed.length+1}/${addresses.length}`;
   const r=await fetch('/api/devices',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:ip,ip,kind:'other'})});
   if(r.ok){added++;existing.add(ip);}else{const body=await r.json();failed.push(ip+': '+body.error);}
  }
  await refresh();$('message').textContent=`Added ${added}; already present ${skipped}; failed ${failed.length}.`+(failed.length?' '+failed.join('; '):'');
  if(!failed.length)e.target.reset();
 }catch(error){$('message').textContent=`Added ${added} before stopping: ${error.message}. You can retry; existing devices will be skipped.`;}
 finally{button.disabled=false;}
};
document.addEventListener('click',async e=>{const d=e.target.dataset;if(d.view){showView(d.view);return;}try{if(d.device&&confirm('Remove this device and its history?'))await mutate('/api/delete-device',{id:d.device});if(d.link)await mutate('/api/delete-link',{id:d.link});}catch(e){$('message').textContent=e.message;}});
refresh();setInterval(refresh,5000);
