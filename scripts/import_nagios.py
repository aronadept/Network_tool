"""Import IPv4 hosts from a local Nagios ZIP without executing backup files."""
import argparse
import ipaddress
import re
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app


def parse_hosts(text):
    # Strip comments before recognizing definitions, so commented examples stay inactive.
    text='\n'.join(line.split('#',1)[0].split(';',1)[0] for line in text.splitlines())
    for body in re.findall(r'define\s+host\s*\{([^{}]*)\}',text,re.S):
        fields={}
        for line in body.splitlines():
            parts=line.strip().split(None,1)
            if len(parts)==2:
                fields[parts[0]]=parts[1].strip()
        if fields.get('register')!='0' and fields.get('host_name') and fields.get('address'):
            yield fields


def classify(name,alias):
    text=(name+' '+alias).lower()
    if 'printer' in text or any(x in text for x in ('canon','imagerunner','hplj')):
        kind,group='other','Printers'
    elif 'switch' in text or 'dgs-' in text:
        kind,group='switch','Switches'
    elif 'nanostation' in text or re.search(r'(^|\W)ap[_\W]',text):
        kind,group='ap','Access Points / RF'
    elif any(x in text for x in ('server','proxmox','windows','apache')):
        kind,group='server','Servers'
    else:
        kind,group='other','Imported Nagios'
    brand='D-Link' if any(x in text for x in ('dlink','d-link','dgs-')) else 'TP-Link' if 'tplink' in text else 'Canon' if any(x in text for x in ('canon','imagerunner')) else ''
    return kind,group,brand


def read_backup(path,include_examples=False):
    hosts={};report=dict(definitions=0,duplicates=0,conflicts=0,examples_skipped=0,invalid_addresses=0)
    with zipfile.ZipFile(path) as archive:
        files=[i for i in archive.infolist() if i.filename.lower().endswith('.cfg') and not i.is_dir()]
        if len(files)>2000 or sum(i.file_size for i in files)>32*1024*1024:
            raise ValueError('Configuration archive exceeds import limits')
        # Prefer top-level custom configuration, then dated snapshots, then older backups.
        def priority(info):
            name=info.filename.lower()
            return (0 if name.startswith('nagios/server/') else 2 if 'backup_file' in name else 1,name)
        for info in sorted(files,key=priority):
            if info.file_size>2*1024*1024:
                raise ValueError('Configuration file exceeds import limit')
            text=archive.read(info).decode('utf-8-sig',errors='replace')
            for fields in parse_hosts(text):
                report['definitions']+=1
                name=fields['host_name']
                if not include_examples and ('/objects/' in info.filename.lower() or name=='localhost'):
                    report['examples_skipped']+=1
                    continue
                try:
                    address=ipaddress.IPv4Address(fields['address'])
                    if address.is_unspecified or address.is_multicast or address.is_loopback:
                        raise ValueError()
                except ValueError:
                    report['invalid_addresses']+=1
                    continue
                ip=str(address)
                if ip in hosts:
                    report['duplicates']+=1
                    if hosts[ip]['name']!=name[:80]:report['conflicts']+=1
                    continue
                kind,group,brand=classify(name,fields.get('alias',''))
                hosts[ip]=dict(name=name[:80],ip=ip,kind=kind,brand=brand,
                    group_name=(fields.get('hostgroups','').split(',')[0].strip() or group)[:80])
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
    except (OSError,ValueError,zipfile.BadZipFile,RuntimeError):
        parser.error('Could not read a supported ZIP backup; verify its path, format and limits')
    print(f"Unique importable hosts: {len(hosts)}; duplicate definitions: {report['duplicates']}; alternate names for the same IP: {report['conflicts']}; example definitions skipped: {report['examples_skipped']}; unsupported addresses: {report['invalid_addresses']}")
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
