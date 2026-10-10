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
renderTopology(s);
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
let topologyKey='', topologyBounds={width:1000,height:600}, camera={x:0,y:0,width:1000,height:600};
function setCamera(){ $('topology').setAttribute('viewBox',`${camera.x} ${camera.y} ${camera.width} ${camera.height}`); }
function fitTopology(){camera={x:0,y:0,width:topologyBounds.width,height:topologyBounds.height};setCamera();}
function zoomTopology(factor){const width=Math.max(260,Math.min(topologyBounds.width*3,camera.width*factor));const height=camera.height*width/camera.width;camera={x:camera.x+(camera.width-width)/2,y:camera.y+(camera.height-height)/2,width,height};setCamera();}
function renderTopology(s){
 const devices=new Map(s.devices.map(d=>[d.id,d]));const neighbors=new Map(s.devices.map(d=>[d.id,new Set()]));
 const links=s.links.filter(l=>devices.has(l.source)&&devices.has(l.target));
 links.forEach(l=>{neighbors.get(l.source).add(l.target);neighbors.get(l.target).add(l.source);});
 const rank=d=>({firewall:0,router:1,switch:2,ap:3,server:4,other:5}[d.kind]??5);
 const ordered=[...s.devices].sort((a,b)=>rank(a)-rank(b)||neighbors.get(b.id).size-neighbors.get(a.id).size||a.name.localeCompare(b.name));
 const positions=new Map(),seen=new Set();let offset=60,maxWidth=1000;
 for(const root of ordered){
  if(seen.has(root.id)||!neighbors.get(root.id).size)continue;
  const layers=[[root.id]];seen.add(root.id);
  for(let depth=0;depth<layers.length;depth++){
   const next=[];for(const id of layers[depth])for(const peer of neighbors.get(id))if(!seen.has(peer)){seen.add(peer);next.push(peer);}
   if(next.length)layers.push(next);
  }
  const width=Math.max(1000,...layers.map(layer=>Math.min(6,layer.length)*220+80));maxWidth=Math.max(maxWidth,width);
  for(const layer of layers){
   const columns=Math.min(6,layer.length);
   layer.forEach((id,index)=>positions.set(id,{x:(width-columns*220)/2+(index%columns)*220+20,y:offset+Math.floor(index/columns)*130}));
   offset+=Math.ceil(layer.length/columns)*130+40;
  }
  offset+=40;
 }
 const isolated=ordered.filter(d=>!seen.has(d.id));let isolatedHeader='';
 if(isolated.length){isolatedHeader=`<text x="40" y="${offset}" fill="#91a3bc" font-size="16">Devices without saved connections</text>`;offset+=25;isolated.forEach((d,i)=>positions.set(d.id,{x:40+(i%4)*230,y:offset+Math.floor(i/4)*125}));offset+=Math.ceil(isolated.length/4)*125;}
 topologyBounds={width:maxWidth,height:Math.max(520,offset+40)};
 const key=JSON.stringify([s.devices.map(d=>[d.id,d.kind]),links.map(l=>[l.source,l.target])]);
 let svg=`<defs><pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#26344c"/></pattern></defs><rect x="0" y="0" width="${topologyBounds.width}" height="${topologyBounds.height}" fill="url(#grid)"/>`;
 svg+=links.map(l=>{const a=positions.get(l.source),b=positions.get(l.target);const same=a.y===b.y;const x1=a.x+90,y1=a.y+(same?0:a.y<b.y?88:0),x2=b.x+90,y2=b.y+(same?0:a.y<b.y?0:88);const middle=same?Math.min(y1,y2)-45:(y1+y2)/2;const label=l.label.length>32?l.label.slice(0,31)+'…':l.label;return `<g class="topology-edge"><title>${esc(devices.get(l.source).name)} ↔ ${esc(devices.get(l.target).name)}: ${esc(l.label)}</title><path d="M ${x1} ${y1} C ${x1} ${middle}, ${x2} ${middle}, ${x2} ${y2}" fill="none" stroke="#5b779d" stroke-width="2"/><text x="${(x1+x2)/2}" y="${same?middle:middle-6}" text-anchor="middle" fill="#bdcde0" font-size="11" paint-order="stroke" stroke="#0b1525" stroke-width="5">${esc(label)}</text></g>`;}).join('');
 svg+=isolatedHeader;
 svg+=s.devices.map(d=>{const {x,y}=positions.get(d.id);const color=d.status==='up'?'#48d6bb':d.status==='down'?'#fb7c83':'#efc56b';const name=d.name.length>22?d.name.slice(0,21)+'…':d.name;return `<g class="device-card" data-node="${d.id}" tabindex="0" role="button" aria-label="${esc(d.name)} ${esc(d.status)}"><title>${esc(d.name)} · ${esc(d.ip)} · ${esc(d.brand)} ${esc(d.model)} · ${esc(d.status)}</title><rect x="${x}" y="${y}" width="180" height="88" rx="10" fill="#15263b" stroke="#38516c"/><rect x="${x}" y="${y+12}" width="3" height="64" rx="2" fill="${color}"/><text x="${x+14}" y="${y+22}" fill="#89a2c0" font-size="10">${esc(d.kind.toUpperCase())}</text><circle cx="${x+164}" cy="${y+18}" r="4" fill="${color}"/><text x="${x+14}" y="${y+43}" fill="#e1ecfa" font-size="13" font-weight="600">${esc(name)}</text><text x="${x+14}" y="${y+61}" fill="#9fb3cc" font-size="11">${esc(d.ip)}</text><text x="${x+14}" y="${y+77}" fill="${color}" font-size="10">${esc(d.status.toUpperCase())}${d.latency==null?'':' · '+d.latency+' ms'}</text></g>`;}).join('');
 if(!s.devices.length)svg+='<text x="500" y="260" text-anchor="middle" fill="#91a3bc">Add or discover devices to build your network map</text>';
 $('topology').innerHTML=svg;
 if(key!==topologyKey){topologyKey=key;fitTopology();}else setCamera();
 $('topology-count').textContent=`${s.devices.length} devices · ${links.length} connections`;
 $('topology').onclick=e=>{const node=e.target.closest('[data-node]');if(node){const d=devices.get(Number(node.dataset.node));$('node-details').textContent=`${d.name} · ${d.ip} · ${d.kind} · ${d.brand||'Brand unknown'} ${d.model||''} · ${d.status}`;}};
}
$('zoom-in').onclick=()=>zoomTopology(.75);$('zoom-out').onclick=()=>zoomTopology(1.33);$('zoom-fit').onclick=fitTopology;
let drag=null;
$('topology').onpointerdown=e=>{if(e.target.closest('[data-node]'))return;drag={x:e.clientX,y:e.clientY,camera:{...camera}};$('topology').setPointerCapture(e.pointerId);};
$('topology').onpointermove=e=>{if(!drag)return;const box=$('topology').getBoundingClientRect();const scale=Math.max(drag.camera.width/box.width,drag.camera.height/box.height);camera.x=drag.camera.x-(e.clientX-drag.x)*scale;camera.y=drag.camera.y-(e.clientY-drag.y)*scale;setCamera();};
$('topology').onpointerup=$('topology').onpointercancel=()=>{drag=null;};
$('topology').addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.closest('[data-node]')){e.preventDefault();e.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});

refresh();setInterval(refresh,5000);
