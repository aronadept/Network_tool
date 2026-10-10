"""Read-only, bounded SNMP system and interface inventory."""
import asyncio
import time
from checks import snmp_get

SYSTEM = {'hostname':'1.3.6.1.2.1.1.5.0', 'description':'1.3.6.1.2.1.1.1.0',
          'object_id':'1.3.6.1.2.1.1.2.0', 'uptime_ticks':'1.3.6.1.2.1.1.3.0',
          'location':'1.3.6.1.2.1.1.6.0', 'contact':'1.3.6.1.2.1.1.4.0'}

async def interface_column(address, credentials, root, port=161):
    from pysnmp.hlapi.v3arch.asyncio import (SnmpEngine, CommunityData, UsmUserData,
        UdpTransportTarget, ContextData, ObjectType, ObjectIdentity, walk_cmd,
        usmHMAC192SHA256AuthProtocol, usmAesCfb128Protocol)
    engine=SnmpEngine()
    values={}
    try:
        auth=CommunityData(credentials['community'],mpModel=1) if credentials['version']=='2c' else UsmUserData(
            credentials['username'],credentials['auth_password'],credentials['priv_password'],
            authProtocol=usmHMAC192SHA256AuthProtocol,privProtocol=usmAesCfb128Protocol)
        target=await UdpTransportTarget.create((address,port),timeout=2,retries=0)
        async for error,status,_,bindings in walk_cmd(engine,auth,target,ContextData(),
                ObjectType(ObjectIdentity(root)),lexicographicMode=False,maxRows=64):
            if error or status:
                raise ValueError('Interface table unavailable')
            for oid,value in bindings:
                key=str(oid)
                if not key.startswith(root+'.') or value.__class__.__name__ in ('NoSuchObject','NoSuchInstance','EndOfMibView'):
                    return values
                suffix=key[len(root)+1:]
                if not suffix.isdigit():
                    continue
                values[int(suffix)]=value.prettyPrint()[:500]
        return values
    finally:
        engine.close_dispatcher()

async def collect(address, credentials):
    profile={'fetched_at':time.time(),'system':{},'interfaces':[],'warnings':[]}
    async def scalar(name,oid):
        try:
            state,text,number=await snmp_get(address,161,oid,credentials,2)
            if state=='OK':
                profile['system'][name]=number if name=='uptime_ticks' and number is not None else text
            else:
                profile['warnings'].append(name+' unavailable')
        except Exception:
            profile['warnings'].append(name+' unavailable')
    await asyncio.gather(*(scalar(name,oid) for name,oid in SYSTEM.items()))
    if not profile['system']:
        raise ValueError('SNMP did not respond with system details. Check SNMP access, credentials and UDP port 161.')
    columns={'name':'1.3.6.1.2.1.2.2.1.2','status':'1.3.6.1.2.1.2.2.1.8','speed_bps':'1.3.6.1.2.1.2.2.1.5'}
    async def column(name,root):
        try:
            return name,await asyncio.wait_for(interface_column(address,credentials,root),10)
        except Exception:
            profile['warnings'].append('Interface '+name+' unavailable or timed out')
            return name,{}
    tables=dict(await asyncio.gather(*(column(name,root) for name,root in columns.items())))
    indices=sorted(set().union(*(table.keys() for table in tables.values())))[:64]
    for index in indices:
        profile['interfaces'].append(dict(index=index,**{name:table.get(index) for name,table in tables.items()}))
    if len(indices)==64:
        profile['warnings'].append('Interface inventory limited to the first 64 entries')
    return profile

def fetch_profile(address,credentials):
    try:
        return asyncio.run(asyncio.wait_for(collect(address,credentials),25))
    except TimeoutError:
        raise ValueError('SNMP inventory timed out. Check device reachability and credentials.') from None
