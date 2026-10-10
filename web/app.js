const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=t=>t?new Date(t*1000).toLocaleString():'Pending';
let csrf='', latestState=null;
async function request(path,data){const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)};const r=await fetch(path,options);const body=await r.json();if(!r.ok){if(r.status===401&&path!=='/api/login')showLogin();throw Error(body.error||'Request failed');}return body;}
async function mutate(path,data){await request(path,data);await refresh();}
function showLogin(){csrf='';$('app-shell').hidden=true;$('login-screen').hidden=false;}
async function signedIn(session){csrf=session.csrf;$('current-user').textContent=session.username;$('login-screen').hidden=true;$('app-shell').hidden=false;await refresh();}
$('login-form').onsubmit=async e=>{e.preventDefault();try{const session=await request('/api/login',Object.fromEntries(new FormData(e.target)));e.target.reset();$('login-error').textContent='';await signedIn(session);}catch(error){$('login-error').textContent=error.message;}};
$('logout').onclick=async()=>{try{await request('/api/logout',{});showLogin();}catch(error){$('message').textContent=error.message;}};
const badge=(state,type='')=>`<span class="badge state-${esc(state)}">${esc(state)}</span>${type==='SOFT'&&state!=='PENDING'?' <small>retrying</small>':''}`;
function problemsOf(s){const now=Date.now()/1000;return [...s.devices.map(d=>({...d,target:'host',host_name:d.name,check_name:'Host ping'})),...s.services.map(c=>({...c,target:'service',check_name:c.name}))].filter(c=>!['OK','PENDING'].includes(c.state)).map(c=>({...c,maintenance:c.maintenance_until>now}));}
function ackButton(c){return c.acknowledged?'':`<button data-ack="${c.id}" data-target="${c.target}">Acknowledge</button>`;}
function hostRows(s){const group=$('group-filter').value,query=$('host-search').value.trim().toLowerCase(),state=$('host-state').value;$('devices').innerHTML=s.devices.filter(d=>(!group||d.group_name===group)&&(!state||d.state===state)&&(!query||[d.name,d.ip,d.brand,d.model].join(' ').toLowerCase().includes(query))).map(d=>`<tr><td><strong>${esc(d.name)}</strong></td><td>${esc(d.ip)}</td><td>${esc(d.kind)}<br>${esc(d.brand)} ${esc(d.model)}<br><small>${esc(d.group_name)}</small></td><td>${badge(d.state,d.state_type)}${d.maintenance_until>Date.now()/1000?'<br><small>Maintenance</small>':''}</td><td>${d.latency==null?'—':d.latency+' ms'}</td><td>${esc(d.output)}<br><small>${date(d.checked_at)}</small></td><td class="actions"><button data-edit="${d.id}">Edit</button><button data-maintenance="${d.id}" data-minutes="${d.maintenance_until>Date.now()/1000?0:60}">${d.maintenance_until>Date.now()/1000?'End maintenance':'Maintain 1h'}</button><button data-device="${d.id}">Remove</button></td></tr>`).join('')||'<tr><td colspan="7">No matching hosts. Open Add / Discover devices to add your network.</td></tr>';}
function renderOperations(s){
const issues=problemsOf(s),active=issues.filter(c=>c.state_type==='HARD'&&!c.acknowledged&&!c.maintenance);
$('summary').innerHTML=[['Total hosts',s.devices.length,'neutral'],['Hosts up',s.devices.filter(d=>d.state==='OK').length,'good'],['Hosts down',s.devices.filter(d=>d.state==='CRITICAL').length,'bad'],['Total services',s.services.length,'neutral'],['Service problems',s.services.filter(c=>!['OK','PENDING'].includes(c.state)).length,'warn'],['Active alerts',active.length,'bad']].map(([k,v,t])=>`<div class="stat stat-${t}"><span>${k}</span><strong>${v}</strong><small>Current inventory</small></div>`).join('');
function healthRows(items){return ['OK','WARNING','CRITICAL','UNKNOWN','PENDING'].map(state=>{const count=items.filter(c=>c.state===state).length;return `<div class="health-row"><span class="state-dot state-dot-${state}"></span><span>${state==='OK'?'Healthy':state.charAt(0)+state.slice(1).toLowerCase()}</span><progress class="meter-${state}" value="${count}" max="${Math.max(1,items.length)}" aria-label="${state}: ${count} of ${items.length}"></progress><strong>${count}</strong></div>`;}).join('');}
$('host-health').innerHTML=healthRows(s.devices);$('service-health').innerHTML=healthRows(s.services);$('problem-count').textContent=active.length;
$('engine-summary').textContent=s.engine.status==='running'?'● Monitoring engine connected':s.engine.status==='error'?'● Monitoring engine needs attention':'● Starting monitoring engine';$('engine-log').textContent=s.engine.message;$('engine-banner').classList.toggle('engine-error',s.engine.status==='error');
$('last-update').textContent='Updated '+new Date().toLocaleTimeString();
$('active-problems').innerHTML=active.slice(0,12).map(c=>`<div class="problem-card">${badge(c.state)}<div><strong>${esc(c.host_name)} / ${esc(c.check_name)}</strong><p>${esc(c.output)}</p></div>${ackButton(c)}</div>`).join('')||'<p>No unhandled HARD problems. Check pending or retrying checks below.</p>';
$('problems').innerHTML=issues.map(c=>`<tr><td>${esc(c.host_name)}<br><small>${esc(c.check_name)}</small></td><td>${badge(c.state,c.state_type)}</td><td>${esc(c.output)}</td><td>${c.maintenance?'Maintenance until '+date(c.maintenance_until):c.acknowledged?'Acknowledged: '+esc(c.ack_note):'Unhandled'}</td><td class="actions">${ackButton(c)}</td></tr>`).join('')||'<tr><td colspan="5">No current problems.</td></tr>';
$('services').innerHTML=s.services.map(c=>`<tr><td>${esc(c.host_name)}<br><small>${esc(c.ip)}</small></td><td>${esc(c.name)}<br><small>${esc(c.kind.toUpperCase())} · port ${c.port} · ${c.interval}s</small></td><td>${badge(c.state,c.state_type)}${c.acknowledged?'<br><small>Acknowledged</small>':''}</td><td>${c.attempts}/${c.max_attempts} ${esc(c.state_type)}</td><td>${esc(c.output)}</td><td>${date(c.checked_at)}</td><td class="actions"><button data-service="${c.id}">Remove</button></td></tr>`).join('')||'<tr><td colspan="7">Add service checks below. Each host already has an automatic ping check.</td></tr>';
const group=$('group-filter').value;const groups=[...new Set(s.devices.map(d=>d.group_name))].sort();$('group-filter').innerHTML='<option value="">All groups</option>'+groups.map(g=>`<option value="${esc(g)}">${esc(g)}</option>`).join('');if(groups.includes(group))$('group-filter').value=group;hostRows(s);
}
for(const id of ['group-filter','host-state','host-search'])$(id).addEventListener(id==='host-search'?'input':'change',()=>{if(latestState)hostRows(latestState);});
$('refresh-now').onclick=refresh;
$('menu-toggle').onclick=()=>{const open=$('app-shell').classList.toggle('sidebar-open');$('menu-toggle').setAttribute('aria-expanded',String(open));};
function renderSnmpStatus(){const d=latestState?.devices.find(d=>String(d.id)===$('snmp-host').value);$('snmp-status').textContent=d?.snmp_configured?`Credentials configured: SNMP v${d.snmp_version}. Submit new values to replace them.`:'No credentials configured for this host.';}
$('snmp-host').onchange=renderSnmpStatus;
$('snmp-version').onchange=()=>{const v3=$('snmp-version').value==='3';$('snmp-v2').hidden=v3;$('snmp-v3').hidden=!v3;$('snmp-form').elements.community.required=!v3;for(const name of ['username','auth_password','priv_password'])$('snmp-form').elements[name].required=v3;};
$('service-template').onchange=()=>{const f=$('service-form').elements,t=$('service-template').value;const templates={tcp:['TCP port','tcp',22,''],ssh:['SSH','tcp',22,''],http:['Web HTTP','http',80,''],https:['Web HTTPS','https',443,''],uptime:['SNMP uptime','snmp',161,'1.3.6.1.2.1.1.3.0'],sysname:['SNMP system name','snmp',161,'1.3.6.1.2.1.1.5.0'],snmp:['SNMP metric','snmp',161,'']};const [name,kind,port,oid]=templates[t];f.name.value=name;f.kind.value=kind;f.port.value=port;f.oid.value=oid;f.warning.value='';f.critical.value='';};
$('service-template').onchange();
async function refresh(){if(!csrf)return;try{const s=await request('/api/state');latestState=s;$('health').textContent=s.engine.status==='running'?'Nagios Core connected':'Engine needs attention';
const subnetInput=$('discovery-form').elements.subnet;if(subnetInput.dataset.initialized!=='true'){subnetInput.value=s.discovery_subnet||'';subnetInput.dataset.initialized='true';}
$('discovery-status').textContent=s.discovery_subnet?'Discovery enabled: '+s.discovery_subnet:'Discovery disabled';
const progress=s.discovery_progress;if(progress)$('scan-progress').textContent=`Scan ${progress.status}: ${progress.checked.toLocaleString()} / ${progress.total.toLocaleString()} addresses checked · ${progress.found} responding`+(progress.error?' · '+progress.error:'');
for(const id of ['source','target','service-host','snmp-host']){const prev=$(id).value;$(id).innerHTML=s.devices.map(d=>`<option value="${d.id}">${esc(d.name)} (${esc(d.ip)})</option>`).join('');if(s.devices.some(d=>String(d.id)===prev))$(id).value=prev;}
renderOperations(s);renderSnmpStatus();renderTopology(s);
$('events').innerHTML=s.events.map(e=>`<div class="event">${badge(e.status,e.state_type)} · ${esc(e.name)}${e.service_name?' / '+esc(e.service_name):''} · ${date(e.created_at)}<p>${esc(e.output)}</p></div>`).join('')||'<p>No state changes recorded yet.</p>';
$('links').innerHTML=s.links.map(l=>`<button data-link="${l.id}">Remove link: ${esc(l.label||l.id)}</button>`).join('');
}catch(e){$('health').textContent='Connection lost';$('message').textContent=e.message;}}
function resetDeviceForm(){$('device-form').reset();$('device-form').elements.id.value='';$('device-title').textContent='Add device';$('device-save').textContent='Add device';$('cancel-edit').hidden=true;}
$('cancel-edit').onclick=resetDeviceForm;
for(const [id,path] of [['discovery-form','/api/discovery'],['device-form','/api/devices'],['link-form','/api/links'],['service-form','/api/services'],['snmp-form','/api/snmp']])$(id).onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const data=Object.fromEntries(new FormData(e.target));await mutate(id==='device-form'&&data.id?'/api/update-device':path,data);if(id==='device-form')resetDeviceForm();else if(id==='snmp-form'){for(const name of ['community','username','auth_password','priv_password'])e.target.elements[name].value='';}else if(id!=='discovery-form'){e.target.reset();if(id==='service-form')$('service-template').onchange();}$('message').textContent=id==='discovery-form'?'Discovery settings saved; scan runs in the background.':'Saved';}catch(error){$('message').textContent=error.message;}finally{b.disabled=false;}};
function showView(view){const pages={dashboard:['Operations overview','Live host health, service checks and incidents across your infrastructure.'],problems:['Problem center','Review failed checks, acknowledge incidents and track maintenance.'],services:['Service monitoring','Configure checks, thresholds and SNMP access for your devices.'],topology:['Network topology','Explore saved connections and device reachability.'],manage:['Device management','Discover your subnet or configure hosts in bulk.']};$('page-title').textContent=pages[view][0];$('page-description').textContent=pages[view][1];$('breadcrumb-title').textContent=view==='manage'?'Devices':view.charAt(0).toUpperCase()+view.slice(1);$('app-shell').classList.remove('sidebar-open');$('menu-toggle').setAttribute('aria-expanded','false');for(const name of ['dashboard','problems','services','topology','manage'])$('view-'+name).hidden=name!==view;document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===view)));}
$('bulk-form').onsubmit=async e=>{
 e.preventDefault();const button=e.target.querySelector('button');
 const addresses=[...new Set(e.target.elements.addresses.value.split(/[\s,;]+/).filter(Boolean))];
 if(addresses.length>256){$('message').textContent='Please add at most 256 addresses at a time.';return;}
 if(addresses.some(ip=>!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)||ip.split('.').some(n=>Number(n)>255))){$('message').textContent='Enter valid IPv4 addresses only. Nothing was added.';return;}
 button.disabled=true;let added=0,skipped=0;const failed=[];
 try{
  const state=await request('/api/state');const existing=new Set(state.devices.map(d=>d.ip));
  for(const ip of addresses){
   if(existing.has(ip)){skipped++;continue;}
   $('message').textContent=`Adding devices: ${added+skipped+failed.length+1}/${addresses.length}`;
   const r=await fetch('/api/devices',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({name:ip,ip,kind:'other'})});
   if(r.ok){added++;existing.add(ip);}else{const body=await r.json();failed.push(ip+': '+body.error);}
  }
  await refresh();$('message').textContent=`Added ${added}; already present ${skipped}; failed ${failed.length}.`+(failed.length?' '+failed.join('; '):'');
  if(!failed.length)e.target.reset();
 }catch(error){$('message').textContent=`Added ${added} before stopping: ${error.message}. You can retry; existing devices will be skipped.`;}
 finally{button.disabled=false;}
};
document.addEventListener('click',async e=>{const d=(e.target.closest('button')||e.target).dataset;if(d.view){showView(d.view);return;}try{
if(d.device&&confirm('Remove this host, its services and history?'))await mutate('/api/delete-device',{id:d.device});
if(d.service&&confirm('Remove this service and its history?'))await mutate('/api/delete-service',{id:d.service});
if(d.link)await mutate('/api/delete-link',{id:d.link});
if(d.ack){const note=prompt('Acknowledgement note:');if(note)await mutate('/api/acknowledge',{id:d.ack,target:d.target,note});}
if(d.maintenance)await mutate('/api/maintenance',{id:d.maintenance,minutes:d.minutes});
if(d.edit){const host=latestState.devices.find(h=>String(h.id)===d.edit);const f=$('device-form').elements;for(const name of ['id','name','ip','kind','brand','model','group_name'])f[name].value=host[name];$('device-title').textContent='Edit device';$('device-save').textContent='Save changes';$('cancel-edit').hidden=false;showView('manage');$('device-title').scrollIntoView({block:'center'});}
}catch(error){$('message').textContent=error.message;}});
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
 if(isolated.length){isolatedHeader=`<text x="40" y="${offset}" fill="#64748b" font-size="16">Devices without saved connections</text>`;offset+=25;isolated.forEach((d,i)=>positions.set(d.id,{x:40+(i%4)*230,y:offset+Math.floor(i/4)*125}));offset+=Math.ceil(isolated.length/4)*125;}
 topologyBounds={width:maxWidth,height:Math.max(520,offset+40)};
 const key=JSON.stringify([s.devices.map(d=>[d.id,d.kind]),links.map(l=>[l.source,l.target])]);
 let svg=`<defs><pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#dce5ef"/></pattern></defs><rect x="0" y="0" width="${topologyBounds.width}" height="${topologyBounds.height}" fill="url(#grid)"/>`;
 svg+=links.map(l=>{const a=positions.get(l.source),b=positions.get(l.target);const same=a.y===b.y;const x1=a.x+90,y1=a.y+(same?0:a.y<b.y?88:0),x2=b.x+90,y2=b.y+(same?0:a.y<b.y?0:88);const middle=same?Math.min(y1,y2)-45:(y1+y2)/2;const label=l.label.length>32?l.label.slice(0,31)+'…':l.label;return `<g class="topology-edge"><title>${esc(devices.get(l.source).name)} ↔ ${esc(devices.get(l.target).name)}: ${esc(l.label)}</title><path d="M ${x1} ${y1} C ${x1} ${middle}, ${x2} ${middle}, ${x2} ${y2}" fill="none" stroke="#5b779d" stroke-width="2"/><text x="${(x1+x2)/2}" y="${same?middle:middle-6}" text-anchor="middle" fill="#475569" font-size="11" paint-order="stroke" stroke="#ffffff" stroke-width="5">${esc(label)}</text></g>`;}).join('');
 svg+=isolatedHeader;
 svg+=s.devices.map(d=>{const {x,y}=positions.get(d.id);const color=d.status==='up'?'#48d6bb':d.status==='down'?'#fb7c83':'#efc56b';const name=d.name.length>22?d.name.slice(0,21)+'…':d.name;return `<g class="device-card" data-node="${d.id}" tabindex="0" role="button" aria-label="${esc(d.name)} ${esc(d.status)}"><title>${esc(d.name)} · ${esc(d.ip)} · ${esc(d.brand)} ${esc(d.model)} · ${esc(d.status)}</title><rect x="${x}" y="${y}" width="180" height="88" rx="10" fill="#ffffff" stroke="#d5dfeb"/><rect x="${x}" y="${y+12}" width="3" height="64" rx="2" fill="${color}"/><text x="${x+14}" y="${y+22}" fill="#64748b" font-size="10">${esc(d.kind.toUpperCase())}</text><circle cx="${x+164}" cy="${y+18}" r="4" fill="${color}"/><text x="${x+14}" y="${y+43}" fill="#172b4d" font-size="13" font-weight="600">${esc(name)}</text><text x="${x+14}" y="${y+61}" fill="#64748b" font-size="11">${esc(d.ip)}</text><text x="${x+14}" y="${y+77}" fill="${color}" font-size="10">${esc(d.status.toUpperCase())}${d.latency==null?'':' · '+d.latency+' ms'}</text></g>`;}).join('');
 if(!s.devices.length)svg+='<text x="500" y="260" text-anchor="middle" fill="#64748b">Add or discover devices to build your network map</text>';
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


request('/api/session').then(session=>session.authenticated?signedIn(session):showLogin()).catch(()=>showLogin());
setInterval(refresh,5000);
