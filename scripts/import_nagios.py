"""Import IPv4 hosts from a local Nagios ZIP or TAR.GZ without executing backup files."""
import argparse
import ipaddress
import re
import tarfile
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app


IPV4_LITERAL = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')


def parse_definitions(text, kind):
    # Read configuration as data. Never evaluate check commands, macros or plugin scripts.
    text='\n'.join(line.split('#',1)[0].split(';',1)[0] for line in text.splitlines())
    for body in re.findall(r'define\s+'+kind+r'\s*\{([^{}]*)\}',text,re.S):
        fields={}
        for line in body.splitlines():
            parts=line.strip().split(None,1)
            if len(parts)==2:
                fields[parts[0]]=parts[1].strip()
        if fields.get('register')!='0':
            yield fields


def parse_hosts(text):
    for fields in parse_definitions(text,'host'):
        if fields.get('host_name') and fields.get('address'):
            yield fields


def command_address(command):
    # A legacy camera check may contain one literal IP as an argument.
    candidates=set()
    for literal in IPV4_LITERAL.findall(command):
        try:
            address=ipaddress.IPv4Address(literal)
            if not (address.is_unspecified or address.is_multicast or address.is_loopback):
                candidates.add(str(address))
        except ipaddress.AddressValueError:
            pass
    return next(iter(candidates)) if len(candidates)==1 else None


def classify(name,alias,hostgroup=''):
    text=(name+' '+alias+' '+hostgroup).lower()
    if any(x in text for x in ('synology','nas','network attached storage')):
        kind,group='nas','NAS / Storage'
    elif any(x in text for x in ('printer','imagerunner','canon','hplj','epson','wf-c','wf-m','wf-am','l6490','l-3250')):
        kind,group='printer','Printers'
    elif 'switch' in text or 'dgs-' in text:
        kind,group='switch','Switches'
    elif any(x in text for x in ('camera','cctv','ptz','nvr')):
        kind,group='camera','Cameras'
    elif any(x in text for x in ('nanostation','access point','accesspoint')) or re.search(r'(^|\W)ap[_\W]',text):
        kind,group='ap','Access Points / RF'
    elif any(x in text for x in ('ip-phone','ip_phone','sip_phone','sip phone')):
        kind,group='phone','IP Phones'
    elif 'router' in text or 'mikrotik' in text:
        kind,group='router','Routers'
    elif 'firewall' in text or 'sonicwall' in text:
        kind,group='firewall','Firewalls'
    elif any(x in text for x in ('server','proxmox','windows','apache')):
        kind,group='server','Servers'
    else:
        kind,group='other','Imported Nagios'
    brand='Synology' if 'synology' in text else 'D-Link' if any(x in text for x in ('dlink','d-link','dgs-')) else 'TP-Link' if 'tplink' in text else 'Canon' if any(x in text for x in ('canon','imagerunner')) else 'Epson' if 'epson' in text else ''
    return kind,group,brand


def config_files(path):
    path=Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            infos=[item for item in archive.infolist() if not item.is_dir() and item.filename.lower().endswith('.cfg')]
            if len(infos)>2000 or sum(item.file_size for item in infos)>32*1024*1024:
                raise ValueError('Configuration archive exceeds import limits')
            for item in infos:
                if item.file_size>2*1024*1024:raise ValueError('Configuration file exceeds import limit')
                yield item.filename,archive.read(item).decode('utf-8-sig',errors='replace')
    else:
        with tarfile.open(path,'r:gz') as archive:
            infos=[item for item in archive.getmembers() if item.isfile() and item.name.lower().endswith('.cfg')]
            if len(infos)>2000 or sum(item.size for item in infos)>32*1024*1024:
                raise ValueError('Configuration archive exceeds import limits')
            for item in infos:
                if item.size>2*1024*1024:raise ValueError('Configuration file exceeds import limit')
                yield item.name,archive.extractfile(item).read().decode('utf-8-sig',errors='replace')


def read_backup(path,include_examples=False):
    hosts={};report=dict(definitions=0,duplicates=0,conflicts=0,examples_skipped=0,
                         invalid_addresses=0,camera_from_checks=0,unresolved_hosts=0)
    files=list(config_files(path))
    # Prefer the active server/ tree over dated and older snapshots.
    def priority(item):
        name=item[0].lower()
        return (0 if '/server/' in name else 2 if 'backup_file' in name else 1,name)
    for filename,text in sorted(files,key=priority):
        definitions=list(parse_definitions(text,'host'))
        camera_source='camera' in filename.lower() or 'ptz' in filename.lower() or any(
            'camera' in (d.get('hostgroups','')+' '+d.get('alias','')).lower() for d in definitions)
        candidates=[]
        missing=set()
        for fields in definitions:
            if not fields.get('host_name'):
                continue
            if fields.get('address'):
                candidates.append(fields)
            elif camera_source:
                address=command_address(fields.get('check_command',''))
                if address:
                    candidates.append({**fields,'address':address})
                    report['camera_from_checks']+=1
                else:missing.add(fields['host_name'])
            else:
                report['unresolved_hosts']+=1
        if camera_source and missing:
            for service in parse_definitions(text,'service'):
                if service.get('host_name') not in missing:
                    continue
                address=command_address(service.get('check_command',''))
                if address:
                    description=service.get('service_description','')
                    candidates.append(dict(host_name=description or service['host_name'],
                        address=address,alias='Camera',hostgroups='Cameras'))
                    report['camera_from_checks']+=1
            # Group-only camera hosts are skipped if the backup has no usable IP.
            report['unresolved_hosts']+=len(missing)
        for fields in candidates:
            report['definitions']+=1
            name=fields['host_name']
            if not include_examples and ('/objects/' in filename.lower() or name=='localhost'):
                report['examples_skipped']+=1
                continue
            try:
                address=ipaddress.IPv4Address(fields['address'])
                if address.is_unspecified or address.is_multicast or address.is_loopback:
                    raise ValueError()
            except (ValueError,ipaddress.AddressValueError):
                report['invalid_addresses']+=1
                continue
            ip=str(address)
            if ip in hosts:
                report['duplicates']+=1
                if hosts[ip]['name']!=name[:80]:report['conflicts']+=1
                continue
            hostgroup=fields.get('hostgroups','').split(',')[0].strip()
            kind,group,brand=classify(name,fields.get('alias',''),hostgroup)
            if kind in ('nas','printer','camera','phone'):
                # Consistent group names make these devices easy to find.
                hostgroup=group
            hosts[ip]=dict(name=name[:80],ip=ip,kind=kind,brand=brand,
                group_name=(hostgroup or group)[:80])
    return list(hosts.values()),report

def import_hosts(hosts):
    added=0;existing=0
    with app.connect() as conn:
        for host in hosts:
            cursor=conn.execute('INSERT OR IGNORE INTO devices(name,ip,kind,brand,group_name) VALUES(:name,:ip,:kind,:brand,:group_name)',host)
            added+=cursor.rowcount
            existing+=1-cursor.rowcount
    return added,existing


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup',type=Path)
    parser.add_argument('--apply',action='store_true',help='Add the reviewed hosts to the app database; default is preview only')
    parser.add_argument('--include-example-hosts',action='store_true',help='Include host definitions under objects/ (localhost is still excluded by address validation)')
    args=parser.parse_args()
    try:
        hosts,report=read_backup(args.backup,args.include_example_hosts)
    except (OSError,ValueError,zipfile.BadZipFile,tarfile.TarError,RuntimeError):
        parser.error('Could not read a supported ZIP or TAR.GZ backup; verify its path, format and limits')
    print(f"Unique importable hosts: {len(hosts)}; duplicate definitions: {report['duplicates']}; alternate names for the same IP: {report['conflicts']}; camera IPs from checks: {report['camera_from_checks']}; hosts without IP: {report['unresolved_hosts']}; example definitions skipped: {report['examples_skipped']}; unsupported addresses: {report['invalid_addresses']}")
    print('Device types:', ', '.join(f'{kind} {sum(h["kind"]==kind for h in hosts)}' for kind in sorted({h['kind'] for h in hosts})))
    for host in hosts:
        print(f"{host['ip']:16} {host['name']} [{host['kind']}; {host['group_name']}]")
    if not args.apply:
        print('Preview only: no database changes. Review historical IPs, then rerun with --apply.')
        return
    app.initialize()
    added,existing=import_hosts(hosts)
    print(f'Added {added} hosts; skipped {existing} existing IPs. Existing configuration was preserved.')
    print('Start/restart the app service to load the new Nagios host checks.')
    print('Only inventory was imported. Credentials, custom plugins and old service commands were not imported or executed.')


if __name__=='__main__':main()
