const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=t=>t?new Date(t*1000).toLocaleString():'Pending';
const fetchingProfiles=new Set();
let csrf='', latestState=null, currentView='dashboard', selectedHostId=null, selectedServiceId=null;
let reportData=null;
async function request(path,data){const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)};const r=await fetch(path,options);const body=await r.json();if(!r.ok){if(r.status===401&&path!=='/api/login')showLogin();throw Error(body.error||'Request failed');}return body;}
async function mutate(path,data){await request(path,data);await refresh();}
function showLogin(){csrf='';latestState=null;reportData=null;selectedHostId=null;selectedServiceId=null;selectedDiscovered.clear();$('login-form').reset();$('discovery-form').elements.subnet.dataset.initialized='false';showView('dashboard');$('app-shell').hidden=true;$('login-screen').hidden=false;}
async function signedIn(session){csrf=session.csrf;$('current-user').textContent=session.username;$('login-screen').hidden=true;$('app-shell').hidden=false;showView('dashboard');await refresh();}
$('login-form').onsubmit=async e=>{e.preventDefault();try{const session=await request('/api/login',Object.fromEntries(new FormData(e.target)));e.target.reset();$('login-error').textContent='';await signedIn(session);}catch(error){$('login-error').textContent=error.message;}};
$('logout').onclick=async()=>{try{await request('/api/logout',{});showLogin();}catch(error){$('message').textContent=error.message;}};
const badge=(state,type='')=>`<span class="badge state-${esc(state)}">${esc(state)}</span>${type==='SOFT'&&state!=='PENDING'?' <small>retrying</small>':''}`;
function problemsOf(s){const now=Date.now()/1000;return [...s.devices.map(d=>({...d,target:'host',host_name:d.name,check_name:'Host ping'})),...s.services.map(c=>({...c,target:'service',check_name:c.name}))].filter(c=>!['OK','PENDING'].includes(c.state)).map(c=>({...c,maintenance:c.maintenance_until>now}));}
function ackButton(c){return c.acknowledged||['OK','PENDING'].includes(c.state)?'':`<button data-ack="${c.id}" data-target="${c.target}">Acknowledge</button>`;}
let hostSort={key:'name',direction:1};
const severity={CRITICAL:0,WARNING:1,UNKNOWN:2,PENDING:3,OK:4};
function compareHosts(a,b){
 const key=hostSort.key;let result;
 if(key==='latency'||key==='checked_at'){
  const av=a[key],bv=b[key];
  if(av==null||bv==null)return av==null?(bv==null?0:1):-1;
  result=Number(av)-Number(bv);
 }else if(key==='ip'){
  const number=ip=>ip.split('.').reduce((sum,part)=>sum*256+Number(part),0);
  result=number(a.ip)-number(b.ip);
 }else if(key==='state')result=(severity[a.state]??5)-(severity[b.state]??5);
 else result=String(a[key]??'').localeCompare(String(b[key]??''),undefined,{numeric:true,sensitivity:'base'});
 return result*hostSort.direction||a.id-b.id;
}
function updateSortHeaders(){document.querySelectorAll('[data-host-sort]').forEach(button=>{const active=button.dataset.hostSort===hostSort.key;button.closest('th').setAttribute('aria-sort',active?(hostSort.direction===1?'ascending':'descending'):'none');button.querySelector('.sort-indicator').textContent=active?(hostSort.direction===1?'↑':'↓'):'↕';});}
document.querySelectorAll('[data-host-sort]').forEach(button=>button.onclick=()=>{const key=button.dataset.hostSort;hostSort={key,direction:key===hostSort.key?-hostSort.direction:1};updateSortHeaders();if(latestState)hostRows(latestState);});
updateSortHeaders();
function hostRows(s){const group=$('group-filter').value,kind=$('host-kind').value,query=$('host-search').value.trim().toLowerCase(),state=$('host-state').value;$('devices').innerHTML=s.devices.filter(d=>(!group||d.group_name===group)&&(!kind||d.kind===kind)&&(!state||d.state===state)&&(!query||[d.name,d.ip,d.kind,d.brand,d.model,d.group_name].join(' ').toLowerCase().includes(query))).sort(compareHosts).map(d=>`<tr><td><button class="host-link" data-host-details="${d.id}">${esc(d.name)}</button></td><td>${esc(d.ip)}</td><td>${esc(d.kind)}<br>${esc(d.brand)} ${esc(d.model)}<br><small>${esc(d.group_name)}</small></td><td>${badge(d.state,d.state_type)}${d.maintenance_until>Date.now()/1000?'<br><small>Maintenance</small>':''}</td><td>${d.latency==null?'—':d.latency+' ms'}</td><td>${esc(d.output)}<br><small>${date(d.checked_at)}</small></td><td class="actions"><button data-edit="${d.id}">Edit</button><button data-maintenance="${d.id}" data-minutes="${d.maintenance_until>Date.now()/1000?0:60}">${d.maintenance_until>Date.now()/1000?'End maintenance':'Maintain 1h'}</button><button data-device="${d.id}">Remove</button></td></tr>`).join('')||'<tr><td colspan="7">No matching hosts. Open Discovery / Inventory to add your network.</td></tr>';$('host-result-count').textContent=`${$('devices').querySelectorAll('[data-host-details]').length} of ${s.devices.length} hosts`;}
function renderOperations(s){
 const issues=problemsOf(s),active=issues.filter(c=>c.state_type==='HARD'&&!c.acknowledged&&!c.maintenance);
 const stats=[['Total hosts',s.devices.length,'neutral','hosts',''],['Hosts up',s.devices.filter(d=>d.state==='OK').length,'good','hosts','OK'],['Hosts down',s.devices.filter(d=>d.state==='CRITICAL').length,'bad','hosts','CRITICAL'],['Total services',s.services.length,'neutral','services',''],['Service problems',s.services.filter(c=>!['OK','PENDING'].includes(c.state)).length,'warn','problems',''],['Unhandled problems',active.length,'bad','problems','']];
 $('summary').innerHTML=stats.map(([label,value,tone,view,state])=>`<button class="stat stat-${tone}" data-status-view="${view}" data-status-state="${state}" data-status-handling="${label==='Unhandled problems'?'unhandled':''}"><span>${label}</span><strong>${value}</strong><small>Open status details →</small></button>`).join('');
 function counts(items,target){return ['OK','WARNING','CRITICAL','UNKNOWN','PENDING'].map(state=>`<button class="status-chip state-${state}" data-status-view="${target}" data-status-state="${state}"><span>${target==='hosts'&&state==='OK'?'UP':target==='hosts'&&state==='CRITICAL'?'DOWN':state}</span><b>${items.filter(c=>c.state===state).length}</b></button>`).join('');}
 $('host-totals').innerHTML=counts(s.devices,'hosts');$('service-totals').innerHTML=counts(s.services,'services');
 function healthRows(items){return ['OK','WARNING','CRITICAL','UNKNOWN','PENDING'].map(state=>{const count=items.filter(c=>c.state===state).length;return `<div class="health-row"><span class="state-dot state-dot-${state}"></span><span>${state==='OK'?'Healthy':state.charAt(0)+state.slice(1).toLowerCase()}</span><progress class="meter-${state}" value="${count}" max="${Math.max(1,items.length)}" aria-label="${state}: ${count} of ${items.length}"></progress><strong>${count}</strong></div>`;}).join('');}
 $('host-health').innerHTML=healthRows(s.devices);$('service-health').innerHTML=healthRows(s.services);$('problem-count').textContent=active.length;
 $('engine-summary').textContent=s.engine.status==='running'?'Monitoring engine connected':s.engine.status==='error'?'Monitoring engine needs attention':'Starting monitoring engine';$('engine-log').textContent=s.engine.message;$('engine-banner').classList.toggle('engine-error',s.engine.status==='error');
 $('app-shell').classList.toggle('engine-connected',s.engine.status==='running');$('health').dataset.state=s.engine.status;
 $('last-update').textContent='Updated '+new Date().toLocaleTimeString();
 const attention=s.devices.filter(d=>d.state!=='OK').sort((a,b)=>(severity[a.state]??5)-(severity[b.state]??5)||a.name.localeCompare(b.name));
 $('dashboard-hosts').innerHTML=attention.slice(0,10).map(d=>`<tr><td>${hostLink(d)}</td><td>${esc(d.ip)}</td><td>${badge(d.state,d.state_type)}</td><td>${esc(d.output)}</td><td>${date(d.checked_at)}</td></tr>`).join('')||`<tr><td colspan="5">${s.devices.length?'All monitored hosts are healthy.':'No hosts monitored yet. Open Discovery / Inventory to add your network.'}</td></tr>`;
 $('active-problems').innerHTML=active.slice(0,12).map(c=>`<div class="problem-card">${badge(c.state)}<div>${hostLink({id:c.device_id||c.id,name:c.host_name})} / ${c.target==='service'?serviceLink(c):'Host ping'}<p>${esc(c.output)}</p></div>${ackButton(c)}</div>`).join('')||'<p>No unhandled HARD problems. Pending checks are listed in Host Status and Service Status.</p>';
 const group=$('group-filter').value,groups=[...new Set(s.devices.map(d=>d.group_name))].sort();$('group-filter').innerHTML='<option value="">All groups</option>'+groups.map(g=>`<option value="${esc(g)}">${esc(g)}</option>`).join('');if(groups.includes(group))$('group-filter').value=group;
 hostRows(s);renderServices(s);renderProblems(s);renderEvents(s);renderGroups(s);
 $('maintenance-hosts').innerHTML=s.devices.filter(d=>d.maintenance_until>Date.now()/1000).map(d=>`<tr><td>${hostLink(d)}</td><td>${esc(d.ip)}</td><td>${badge(d.state)}</td><td>${date(d.maintenance_until)}</td><td><button data-maintenance="${d.id}" data-minutes="0">End maintenance</button></td></tr>`).join('')||'<tr><td colspan="5">No hosts in maintenance. Use a host’s detail page to schedule one hour.</td></tr>';
 $('engine-info').innerHTML=facts([['Status',s.engine.status.toUpperCase()],['Last update',date(s.engine.updated_at)],['Monitored hosts',s.devices.length],['Service checks',s.services.length],['Ping utility',s.ping_available?'Available':'Not installed'],['Host check interval',s.interval+' seconds'],['Check retries','10 seconds'],['Console refresh',$('auto-refresh').checked?'5 seconds':'Paused']]);$('engine-diagnostics').textContent=s.engine.message;
}
const hostLink=d=>`<button class="host-link" data-host-details="${d.id}">${esc(d.name)}</button>`;
const serviceLink=c=>`<button class="host-link" data-service-details="${c.id}">${esc(c.name||c.check_name)}</button>`;
const facts=items=>`<dl class="host-facts">${items.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value??'—')}</dd></div>`).join('')}</dl>`;
let serviceSort={key:'host_name',direction:1};
function renderServices(s){
 const query=$('service-search').value.trim().toLowerCase(),state=$('service-state').value;
 const rows=s.services.filter(c=>(!state||state===c.state)&&(!query||[c.host_name,c.name,c.output,c.ip].join(' ').toLowerCase().includes(query))).sort((a,b)=>{const key=serviceSort.key;let result;if(key==='state')result=(severity[a.state]??5)-(severity[b.state]??5);else if(key==='checked_at'||key==='attempts'){if(a[key]==null||b[key]==null)return a[key]==null?(b[key]==null?0:1):-1;result=a[key]-b[key];}else result=String(a[key]).localeCompare(String(b[key]),undefined,{numeric:true,sensitivity:'base'});return result*serviceSort.direction||a.id-b.id;});
 $('service-result-count').textContent=`${rows.length} of ${s.services.length} checks`;
 $('services').innerHTML=rows.map(c=>`<tr><td>${hostLink({id:c.device_id,name:c.host_name})}<br><small>${esc(c.ip)}</small></td><td>${serviceLink(c)}<br><small>${esc(c.kind.toUpperCase())} · port ${c.port} · ${c.interval}s</small></td><td>${badge(c.state,c.state_type)}${c.acknowledged?'<br><small>Acknowledged</small>':''}</td><td>${c.attempts}/${c.max_attempts} ${esc(c.state_type)}</td><td>${esc(c.output)}</td><td>${date(c.checked_at)}</td><td class="actions"><button data-service-details="${c.id}">Details</button><button data-service="${c.id}">Remove</button></td></tr>`).join('')||'<tr><td colspan="7">No matching service checks. Open Configuration to add TCP, HTTP or SNMP checks.</td></tr>';
 document.querySelectorAll('[data-service-sort]').forEach(button=>{const active=button.dataset.serviceSort===serviceSort.key;button.closest('th').setAttribute('aria-sort',active?(serviceSort.direction===1?'ascending':'descending'):'none');button.querySelector('.sort-indicator').textContent=active?(serviceSort.direction===1?'↑':'↓'):'↕';});
}
function renderProblems(s){const query=$('problem-search').value.trim().toLowerCase(),handling=$('problem-handling').value;const rows=problemsOf(s).filter(c=>(!query||[c.host_name,c.check_name,c.output].join(' ').toLowerCase().includes(query))&&(!handling||(handling==='unhandled'&&c.state_type==='HARD'&&!c.acknowledged&&!c.maintenance)||(handling==='acknowledged'&&c.acknowledged)||(handling==='maintenance'&&c.maintenance)||(handling==='retrying'&&c.state_type==='SOFT')));
 $('problems').innerHTML=rows.map(c=>`<tr><td>${hostLink({id:c.device_id||c.id,name:c.host_name})}<br>${c.target==='service'?serviceLink(c):'<small>Host ping</small>'}</td><td>${badge(c.state,c.state_type)}</td><td>${esc(c.output)}</td><td>${c.maintenance?'Maintenance until '+date(c.maintenance_until):c.acknowledged?'Acknowledged: '+esc(c.ack_note):'Unhandled'}</td><td class="actions">${ackButton(c)}</td></tr>`).join('')||'<tr><td colspan="5">No matching problems.</td></tr>';
}
function renderEvents(s){const query=$('event-search').value.trim().toLowerCase(),state=$('event-state').value;const source=reportData?reportData.events.map(e=>({...e,name:e.device_name,service_name:e.check_name})):s.events;const rows=source.filter(e=>(!state||e.status===state)&&(!query||[e.name,e.service_name,e.output].join(' ').toLowerCase().includes(query)));$('event-result-count').textContent=reportData?`${rows.length} of ${source.length} changes in report`:'Latest 100 events across the network';$('events').innerHTML=rows.map(e=>`<tr><td>${date(e.created_at)}</td><td>${hostLink({id:e.device_id,name:e.name})}</td><td>${e.service_id?serviceLink({id:e.service_id,name:e.service_name}):'Host ping'}</td><td>${badge(e.status)}<br><small>${esc(e.state_type)}</small></td><td>${esc(e.output)}</td></tr>`).join('')||'<tr><td colspan="5">No matching state changes recorded.</td></tr>';}
const localDate=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
function setReportPeriod(){const p=$('report-period').value;if(p==='custom')return;const today=new Date(),start=new Date(today.getFullYear(),today.getMonth(),today.getDate()),end=new Date(start);if(p.includes('week')){start.setDate(start.getDate()-(start.getDay()+6)%7);if(p==='last-week')start.setDate(start.getDate()-7);end.setDate(start.getDate()+6);}else{if(p==='last-month')start.setMonth(start.getMonth()-1);start.setDate(1);end.setFullYear(start.getFullYear(),start.getMonth()+1,0);}$('report-from').value=localDate(start);$('report-to').value=localDate(end);}
$('report-period').onchange=setReportPeriod;
for(const id of ['report-from','report-to'])$(id).onchange=()=>{$('report-period').value='custom';};
setReportPeriod();
$('report-run').onclick=async()=>{const from=$('report-from').value,to=$('report-to').value;const start=new Date(from+'T00:00:00').getTime()/1000,next=new Date(to+'T00:00:00');next.setDate(next.getDate()+1);const end=next.getTime()/1000;if(!from||!to||!Number.isFinite(start)||!Number.isFinite(end)||start>=end){$('report-note').textContent='Select a valid From and To date.';return;}const button=$('report-run');button.disabled=true;try{const params=new URLSearchParams({start,end,device_id:$('report-device').value||0});reportData=await request('/api/report?'+params);$('report-summary').innerHTML=reportData.summary.map(d=>`<tr><td>${hostLink({id:d.device_id,name:d.name})}</td><td>${esc(d.ip)} · ${esc(d.kind)}</td><td>${d.host_down}</td><td>${d.host_recovered}</td><td>${d.service_alerts}</td><td>${d.service_recovered}</td><td>${d.changes}</td></tr>`).join('')||'<tr><td colspan="7">No recorded changes in this period.</td></tr>';$('report-note').textContent=`${reportData.events.length} recorded changes from ${from} through ${to}. ${reportData.retention} Counts include SOFT and HARD changes; no uptime percentage is inferred.`;$('report-csv').disabled=false;if(latestState)renderEvents(latestState);}catch(error){$('report-note').textContent=error.message;}finally{button.disabled=false;}};
$('report-csv').onclick=()=>{if(!reportData)return;const columns=['created_at','device_name','ip','kind','check_name','status','state_type','output'];const safe=value=>{let s=String(value??'');if(/^[\s\t\r\n]*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};const csv='\uFEFF'+[columns,...reportData.events.map(row=>columns.map(c=>c==='created_at'?new Date(row[c]*1000).toISOString():row[c]))].map(row=>row.map(safe).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`device-report-${$('report-from').value}-to-${$('report-to').value}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
function renderGroups(s){const groups=[...new Set(s.devices.map(d=>d.group_name))].sort();$('host-groups').innerHTML=groups.map(group=>{const hosts=s.devices.filter(d=>d.group_name===group);return `<section class="panel"><div class="panel-heading"><h2>${esc(group)}</h2><button data-host-group="${esc(group)}">${hosts.length} hosts →</button></div><div class="group-counts">${['OK','CRITICAL','UNKNOWN','PENDING'].map(state=>`<span class="tag">${state}: ${hosts.filter(d=>d.state===state).length}</span>`).join('')}</div><div class="group-members">${hosts.map(d=>`<div><div>${hostLink(d)}<br><small>${esc(d.ip)}</small></div>${badge(d.state)}</div>`).join('')}</div></section>`;}).join('')||'<section class="panel"><h2>No host groups yet</h2><p>Add devices to create your first group. Groups are assigned in the host configuration form.</p><button data-view="manage">Add hosts</button></section>';}
for(const [id,renderer] of [['service-search',renderServices],['service-state',renderServices],['problem-search',renderProblems],['problem-handling',renderProblems],['event-search',renderEvents],['event-state',renderEvents]])$(id).addEventListener(id.endsWith('search')?'input':'change',()=>{if(latestState)renderer(latestState);});
document.querySelectorAll('[data-service-sort]').forEach(button=>button.onclick=()=>{const key=button.dataset.serviceSort;serviceSort={key,direction:serviceSort.key===key?-serviceSort.direction:1};if(latestState)renderServices(latestState);});
for(const id of ['group-filter','host-kind','host-state','host-search'])$(id).addEventListener(id==='host-search'?'input':'change',()=>{if(latestState)hostRows(latestState);});
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
renderDiscovery(s);
const progress=s.discovery_progress;if(progress){$('discovery-progress').max=Math.max(1,progress.total);$('discovery-progress').value=progress.checked;}if(progress)$('scan-progress').textContent=`Scan ${progress.status}: ${progress.checked.toLocaleString()} / ${progress.total.toLocaleString()} addresses checked · ${progress.found} responding`+(progress.error?' · '+progress.error:'');
for(const id of ['source','target','service-host','snmp-host']){const prev=$(id).value;$(id).innerHTML=s.devices.map(d=>`<option value="${d.id}">${esc(d.name)} (${esc(d.ip)})</option>`).join('');if(s.devices.some(d=>String(d.id)===prev))$(id).value=prev;}
const reportDevice=$('report-device').value;$('report-device').innerHTML='<option value="">All devices</option>'+s.devices.map(d=>`<option value="${d.id}">${esc(d.name)} (${esc(d.ip)})</option>`).join('');if(s.devices.some(d=>String(d.id)===reportDevice))$('report-device').value=reportDevice;
renderOperations(s);renderSnmpStatus();renderTopology(s);if(currentView==='host-details')await loadHostDetails();if(currentView==='service-details')await loadServiceDetails();
$('links').innerHTML=s.links.map(l=>`<button data-link="${l.id}">Remove link: ${esc(l.label||l.id)}</button>`).join('');
}catch(e){$('health').textContent='Connection lost';$('message').textContent=e.message;}}
const selectedDiscovered=new Set();
function updateDiscoverySelection(){$('add-discovered').textContent=`Add selected (${selectedDiscovered.size})`;$('add-discovered').disabled=!selectedDiscovered.size;}
function renderDiscovery(s){const rows=s.discovery_results||[];const available=new Set(rows.filter(r=>!r.device_id).map(r=>r.ip));for(const ip of selectedDiscovered)if(!available.has(ip))selectedDiscovered.delete(ip);
$('discovered-devices').innerHTML=rows.map(r=>`<tr><td>${r.device_id?'—':`<input type="checkbox" data-discovered="${esc(r.ip)}" aria-label="Select ${esc(r.ip)}" ${selectedDiscovered.has(r.ip)?'checked':''}>`}</td><td>${esc(r.ip)}</td><td>${r.latency==null?'—':r.latency+' ms'}</td><td>${date(r.last_seen)}</td><td>${r.device_id?'<span class="badge state-OK">Added</span>':'<span class="badge state-PENDING">Not added</span>'}</td></tr>`).join('')||'<tr><td colspan="5">No discovery results yet. Enter a subnet and click Search network.</td></tr>';updateDiscoverySelection();}
document.addEventListener('change',e=>{const ip=e.target.dataset.discovered;if(!ip)return;if(e.target.checked){if(selectedDiscovered.size>=256){e.target.checked=false;$('message').textContent='Select at most 256 devices per batch.';return;}selectedDiscovered.add(ip);}else selectedDiscovered.delete(ip);updateDiscoverySelection();});
$('clear-discovered').onclick=()=>{selectedDiscovered.clear();if(latestState)renderDiscovery(latestState);};
$('select-discovered').onclick=()=>{if(!latestState)return;selectedDiscovered.clear();for(const row of (latestState.discovery_results||[]).filter(r=>!r.device_id).slice(0,256))selectedDiscovered.add(row.ip);renderDiscovery(latestState);};
$('add-discovered').onclick=async()=>{const button=$('add-discovered');button.disabled=true;try{const result=await request('/api/add-discovered',{ips:[...selectedDiscovered]});selectedDiscovered.clear();await refresh();$('message').textContent=`Added ${result.added} devices; ${result.skipped} already present.`;}catch(error){$('message').textContent=error.message;}finally{updateDiscoverySelection();}};
function resetDeviceForm(){$('device-form').reset();$('device-form').elements.id.value='';$('device-title').textContent='Add device';$('device-save').textContent='Add device';$('cancel-edit').hidden=true;}
$('cancel-edit').onclick=resetDeviceForm;
for(const [id,path] of [['discovery-form','/api/discovery'],['device-form','/api/devices'],['link-form','/api/links'],['service-form','/api/services'],['snmp-form','/api/snmp']])$(id).onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const data=Object.fromEntries(new FormData(e.target));await mutate(id==='device-form'&&data.id?'/api/update-device':path,data);if(id==='device-form')resetDeviceForm();else if(id==='snmp-form'){for(const name of ['community','username','auth_password','priv_password'])e.target.elements[name].value='';}else if(id!=='discovery-form'){e.target.reset();if(id==='service-form')$('service-template').onchange();}$('message').textContent=id==='discovery-form'?'Search started. Results will appear below; no devices are added until you select Add selected.':'Saved';}catch(error){$('message').textContent=error.message;}finally{b.disabled=false;}};
const pages={
 dashboard:['Dashboard','Live network health and current incidents.'],hosts:['Host Status','Reachability, check output and device inventory.'],services:['Service Status','All TCP, HTTP and SNMP service checks.'],groups:['Host Groups','Device status organized by your inventory groups.'],problems:['Problems','Failed checks, acknowledgements and maintenance.'],maintenance:['Maintenance','Hosts currently in scheduled console maintenance.'],history:['Event History','Recorded host and service state transitions.'],topology:['Network Topology','Saved device connections and live reachability.'],manage:['Discovery / Inventory','Search your subnet, review devices and explicitly add hosts.'],configure:['Configuration','Service checks, thresholds, retries and SNMP access.'],engine:['Monitoring Engine','Nagios Core status and operational diagnostics.'],'host-details':['Host Details','Host state, services, configuration and recent events.'],'service-details':['Service Details','Check status, thresholds, attempts and recent events.']
};
function showView(view){if(!pages[view])return;currentView=view;const [title,description]=pages[view];$('page-title').textContent=title;$('page-description').textContent=description;$('breadcrumb-title').textContent=title;$('app-shell').classList.remove('sidebar-open');$('menu-toggle').setAttribute('aria-expanded','false');for(const name of Object.keys(pages))$('view-'+name).hidden=name!==view;document.querySelectorAll('.tabs button').forEach(button=>{const active=button.dataset.view===view||(view==='host-details'&&button.dataset.view==='hosts')||(view==='service-details'&&button.dataset.view==='services');button.setAttribute('aria-pressed',String(active));});}
function openHost(id){selectedHostId=String(id);showView('host-details');$('host-details').textContent='Loading host details…';return loadHostDetails();}
function openService(id){selectedServiceId=String(id);showView('service-details');$('service-details').textContent='Loading service details…';return loadServiceDetails();}
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
if(d.hostDetails)await openHost(d.hostDetails);
if(d.serviceDetails)await openService(d.serviceDetails);
if(d.configureSnmp){showView('configure');$('snmp-host').value=d.configureSnmp;renderSnmpStatus();$('snmp-form').scrollIntoView({block:'center'});}
if(d.fetchDetails){if(fetchingProfiles.has(d.fetchDetails))return;fetchingProfiles.add(d.fetchDetails);const button=e.target.closest('button');button.disabled=true;button.textContent='Fetching SNMP details…';try{await request('/api/fetch-device-details',{id:d.fetchDetails});if(currentView==='host-details'&&selectedHostId===d.fetchDetails)await loadHostDetails();$('message').textContent='Device details fetched. Inventory names and service checks were kept as configured.';}finally{fetchingProfiles.delete(d.fetchDetails);if(currentView==='host-details'&&selectedHostId===d.fetchDetails)await loadHostDetails();}}
if(d.hostGroup){$('host-search').value='';$('host-state').value='';$('group-filter').value=d.hostGroup;$('host-kind').value='';hostRows(latestState);showView('hosts');}
if(d.statusView){showView(d.statusView);if(d.statusView==='hosts'){$('host-state').value=d.statusState||'';$('host-search').value='';$('group-filter').value='';$('host-kind').value='';hostRows(latestState);}else if(d.statusView==='services'){$('service-state').value=d.statusState||'';$('service-search').value='';renderServices(latestState);}else if(d.statusView==='problems'){$('problem-handling').value=d.statusHandling||'';$('problem-search').value='';renderProblems(latestState);}}
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
 const rank=d=>({firewall:0,router:1,switch:2,ap:3,server:4,nas:4,printer:5,camera:5,phone:5,other:6}[d.kind]??5);
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
 svg+=s.devices.map(d=>{const {x,y}=positions.get(d.id);const color=d.status==='up'?'#5a9947':d.status==='down'?'#cb5555':'#a18c45';const name=d.name.length>22?d.name.slice(0,21)+'…':d.name;return `<g class="device-card" data-node="${d.id}" tabindex="0" role="button" aria-label="${esc(d.name)} ${esc(d.status)}"><title>${esc(d.name)} · ${esc(d.ip)} · ${esc(d.brand)} ${esc(d.model)} · ${esc(d.status)}</title><rect x="${x}" y="${y}" width="180" height="88" rx="10" fill="#ffffff" stroke="#d5dfeb"/><rect x="${x}" y="${y+12}" width="3" height="64" rx="2" fill="${color}"/><text x="${x+14}" y="${y+22}" fill="#64748b" font-size="10">${esc(d.kind.toUpperCase())}</text><circle cx="${x+164}" cy="${y+18}" r="4" fill="${color}"/><text x="${x+14}" y="${y+43}" fill="#172b4d" font-size="13" font-weight="600">${esc(name)}</text><text x="${x+14}" y="${y+61}" fill="#64748b" font-size="11">${esc(d.ip)}</text><text x="${x+14}" y="${y+77}" fill="${color}" font-size="10">${esc(d.status.toUpperCase())}${d.latency==null?'':' · '+d.latency+' ms'}</text></g>`;}).join('');
 if(!s.devices.length)svg+='<text x="500" y="260" text-anchor="middle" fill="#64748b">Add or discover devices to build your network map</text>';
 $('topology').innerHTML=svg;
 if(key!==topologyKey){topologyKey=key;fitTopology();}else setCamera();
 $('topology-count').textContent=`${s.devices.length} devices · ${links.length} connections`;
 $('topology').onclick=e=>{const node=e.target.closest('[data-node]');if(node){const d=devices.get(Number(node.dataset.node));openHost(d.id);}};
}
$('zoom-in').onclick=()=>zoomTopology(.75);$('zoom-out').onclick=()=>zoomTopology(1.33);$('zoom-fit').onclick=fitTopology;
let drag=null;
$('topology').onpointerdown=e=>{if(e.target.closest('[data-node]'))return;drag={x:e.clientX,y:e.clientY,camera:{...camera}};$('topology').setPointerCapture(e.pointerId);};
$('topology').onpointermove=e=>{if(!drag)return;const box=$('topology').getBoundingClientRect();const scale=Math.max(drag.camera.width/box.width,drag.camera.height/box.height);camera.x=drag.camera.x-(e.clientX-drag.x)*scale;camera.y=drag.camera.y-(e.clientY-drag.y)*scale;setCamera();};
$('topology').onpointerup=$('topology').onpointercancel=()=>{drag=null;};
$('topology').addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.closest('[data-node]')){e.preventDefault();e.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}});


request('/api/session').then(session=>session.authenticated?signedIn(session):showLogin()).catch(()=>showLogin());
setInterval(()=>{if($('auto-refresh').checked)refresh();},5000);

async function loadHostDetails(){
 const id=selectedHostId;if(!id)return;
 try{const data=await request('/api/device?id='+encodeURIComponent(id));if(currentView!=='host-details'||id!==selectedHostId)return;
 const h=data.host;
 $('page-title').textContent=h.name;$('breadcrumb-title').textContent='Host details';
 $('host-details').innerHTML=`<section class="panel"><div class="panel-heading"><h2>${esc(h.name)} <small>${esc(h.ip)}</small></h2><div class="actions">${badge(h.state,h.state_type)}${ackButton({...h,target:'host'})}<button data-edit="${h.id}">Edit device</button><button data-maintenance="${h.id}" data-minutes="${h.maintenance_until>Date.now()/1000?0:60}">${h.maintenance_until>Date.now()/1000?'End maintenance':'Maintain 1h'}</button></div></div><p class="check-output">${esc(h.output)}</p>${facts([['State type',h.state_type],['Check attempt',h.attempts+' / 3'],['Last check',date(h.checked_at)],['Latency',h.latency==null?'—':h.latency+' ms'],['Ping interval',data.interval+' seconds'],['Acknowledgement',h.acknowledged?h.ack_note:'None'],['Maintenance',h.maintenance_until>Date.now()/1000?'Until '+date(h.maintenance_until):'None']])}</section>
 <section class="panel"><div class="panel-heading"><h2>Device information</h2><div class="actions"><button data-configure-snmp="${h.id}">SNMP credentials</button><button data-fetch-details="${h.id}" ${h.snmp_configured&&!fetchingProfiles.has(String(h.id))?'':'disabled'}>${fetchingProfiles.has(String(h.id))?'Fetching SNMP details…':'Fetch device details'}</button></div></div>${facts([['Address',h.ip],['Type',h.kind],['Brand',h.brand||'Not configured'],['Model',h.model||'Not configured'],['Group',h.group_name],['SNMP',h.snmp_configured?'v'+h.snmp_version+' credentials configured':'Not configured']])}</section>
 ${profileDetails(data.profile)}
 <section class="panel"><div class="panel-heading"><h2>Service checks (${data.services.length})</h2><button data-view="configure">Configure services</button></div><div class="scroll"><table><thead><tr><th>Service / configuration</th><th>State / attempts</th><th>Output</th><th>Last check</th><th>Actions</th></tr></thead><tbody>${data.services.map(c=>`<tr><td>${serviceLink(c)}<br>${esc(c.kind.toUpperCase())} · port ${c.port}<br><small>Interval ${c.interval}s · timeout ${c.timeout}s${c.oid?' · OID '+esc(c.oid):''}${['http','https'].includes(c.kind)?' · Path '+esc(c.path):''}<br>Warning: ${esc(c.warning??'—')} · Critical: ${esc(c.critical??'—')}</small></td><td>${badge(c.state,c.state_type)}<br>${c.attempts} / ${c.max_attempts}<br>${c.acknowledged?'Acknowledged: '+esc(c.ack_note):''}</td><td>${esc(c.output)}</td><td>${date(c.checked_at)}</td><td>${ackButton({...c,target:'service'})}</td></tr>`).join('')||'<tr><td colspan="5">No service checks configured. Host ping is monitored automatically.</td></tr>'}</tbody></table></div></section>
 <section class="panel"><h2>Saved connections</h2>${data.links.map(l=>`<p>${esc(l.source_name)} ↔ ${esc(l.target_name)} · ${esc(l.label||'No interface label')}</p>`).join('')||'<p>No connections configured. Add connections in Topology.</p>'}<button data-view="topology">Open topology</button></section>
 <section class="panel"><h2>Recent state changes</h2><p>Latest 100 recorded events for this device and its services.</p>${data.events.map(e=>`<div class="event">${badge(e.status,e.state_type)} · ${esc(e.service_name||'Host ping')} · ${date(e.created_at)}<p>${esc(e.output)}</p></div>`).join('')||'<p>No state changes recorded yet.</p>'}</section>`;
 }catch(error){if(currentView==='host-details'&&id===selectedHostId)$('host-details').textContent=error.message;}
}

async function loadServiceDetails(){
 const id=selectedServiceId;if(!id)return;
 try{const data=await request('/api/service?id='+encodeURIComponent(id));if(currentView!=='service-details'||id!==selectedServiceId)return;const c=data.service;
 $('page-title').textContent=c.name;$('breadcrumb-title').textContent='Service Details';
 $('service-details').innerHTML=`<section class="panel"><div class="panel-heading"><h2>${esc(c.name)} on ${hostLink({id:c.device_id,name:c.host_name})}</h2><div class="actions">${badge(c.state,c.state_type)}${ackButton({...c,target:'service'})}</div></div><p class="check-output">${esc(c.output)}</p>${facts([['Address',c.ip],['State type',c.state_type],['Check attempt',c.attempts+' / '+c.max_attempts],['Last check',date(c.checked_at)],['Acknowledgement',c.acknowledged?c.ack_note:'None'],['Host maintenance',c.maintenance_until>Date.now()/1000?'Until '+date(c.maintenance_until):'None']])}</section><section class="panel"><div class="panel-heading"><h2>Check Configuration</h2><button data-view="configure">Configure checks</button></div>${facts([['Type',c.kind.toUpperCase()],['Port',c.port],['Interval',c.interval+' seconds'],['Timeout',c.timeout+' seconds'],['Warning threshold',c.warning],['Critical threshold',c.critical],['HTTP path',['http','https'].includes(c.kind)?c.path:'—'],['SNMP OID',c.kind==='snmp'?c.oid:'—']])}</section><section class="panel"><div class="panel-heading"><h2>Recent State Changes</h2></div>${data.events.map(e=>`<div class="event">${badge(e.status,e.state_type)} · ${date(e.created_at)}<p>${esc(e.output)}</p></div>`).join('')||'<p>No state changes recorded for this check.</p>'}</section>`;
 }catch(error){if(currentView==='service-details'&&id===selectedServiceId)$('service-details').textContent=error.message;}
}

function profileDetails(profile){
 if(!profile)return '<section class="panel"><h2>SNMP device inventory</h2><p>Save this device’s SNMP credentials, then click Fetch device details to read its system information and interfaces.</p></section>';
 const s=profile.system,uptime=Number(s.uptime_ticks)/100;
 const uptimeText=s.uptime_ticks==null?'Unavailable':`${Math.floor(uptime/86400)}d ${Math.floor(uptime%86400/3600)}h ${Math.floor(uptime%3600/60)}m`;
 const status={1:'UP',2:'DOWN',3:'Testing',4:'Unknown',5:'Dormant',6:'Not present',7:'Lower layer down'};
 return `<section class="panel"><div class="panel-heading"><h2>Fetched SNMP information</h2><small>Snapshot: ${date(profile.fetched_at)}</small></div>${facts([['Hostname',s.hostname],['Description',s.description],['System object ID',s.object_id],['Uptime',uptimeText],['Location',s.location],['Contact',s.contact]])}<h3>Network interfaces</h3><div class="scroll"><table><thead><tr><th>Index</th><th>Name / description</th><th>Operational state</th><th>Reported speed</th></tr></thead><tbody>${profile.interfaces.map(i=>`<tr><td>${i.index}</td><td>${esc(i.name??'Unavailable')}</td><td>${esc(status[i.status]||'Unavailable')}</td><td>${i.speed_bps==null?'Unavailable':esc(Number(i.speed_bps)/1000000)+' Mbps'}</td></tr>`).join('')||'<tr><td colspan="4">No supported interface information returned.</td></tr>'}</tbody></table></div><p>${esc(profile.warnings.join(' · '))}</p><p>This is a fetched snapshot. Model, CPU/RAM metrics and automatic LLDP connections require additional device-specific support.</p></section>`;
}
