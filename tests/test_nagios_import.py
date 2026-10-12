import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile
import app

spec=importlib.util.spec_from_file_location('nagios_import',Path(__file__).resolve().parents[1]/'scripts/import_nagios.py')
loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)

class NagiosImport(unittest.TestCase):
    def test_comments_templates_and_commands_are_not_imported(self):
        text='''# define host { host_name fake }
 define command {
 command_name dangerous
 command_line rm -rf /anything
 }
 define host {
 host_name template
 register 0
 address 192.168.1.1
 }
 define host {
 host_name Switch ; comment
 address 192.168.1.2 # comment
 }
'''
        self.assertEqual(list(loader.parse_hosts(text)),[dict(host_name='Switch',address='192.168.1.2')])
    def test_snapshots_are_deduplicated_and_examples_are_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'backup.zip'
            def host(name,address):return 'define host {\n host_name '+name+'\n address '+address+'\n}\n'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('Nagios/server/hosts.cfg',host('Current switch','192.168.1.2'))
                z.writestr('Nagios/backup_file/hosts.cfg',host('Old name','192.168.1.2')+host('AP_Office','192.168.7.1'))
                z.writestr('Nagios/etc/objects/switch.cfg',host('Example','192.168.1.253'))
                z.writestr('../outside.cfg',host('Bad','hostname.invalid')+host('localhost','127.0.0.1'))
            hosts,report=loader.read_backup(path)
            self.assertEqual(len(hosts),2)
            self.assertEqual(next(h for h in hosts if h['ip']=='192.168.1.2')['name'],'Current switch')
            self.assertEqual(report['duplicates'],1)
            self.assertEqual(report['conflicts'],1)
            self.assertEqual(report['examples_skipped'],2)
            self.assertEqual(report['invalid_addresses'],1)
            self.assertFalse((Path(folder).parent/'outside.cfg').exists())
    def test_import_is_idempotent_and_preserves_existing_hosts(self):
        old=app.DB
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.DB=Path(folder)/'network.db';app.initialize()
                with app.connect() as c:c.execute("INSERT INTO devices(name,ip,kind,brand) VALUES('Existing name','192.168.1.2','router','Existing brand')")
                hosts=[dict(name='Imported switch',ip='192.168.1.2',kind='switch',brand='D-Link',group_name='Switches'),dict(name='AP_Office',ip='192.168.7.1',kind='ap',brand='',group_name='APs')]
                self.assertEqual(loader.import_hosts(hosts),(1,1))
                self.assertEqual(loader.import_hosts(hosts),(0,2))
                with app.connect() as c:
                    d=c.execute("SELECT * FROM devices WHERE ip='192.168.1.2'").fetchone()
                    self.assertEqual(d['name'],'Existing name');self.assertEqual(d['brand'],'Existing brand')
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM services').fetchone()[0],0)
        finally:app.DB=old

class CurrentTarImport(unittest.TestCase):
    def test_tar_camera_check_ips_and_types(self):
        import io,tarfile
        config='''define host {\n host_name Synology_NAS\n address 192.168.1.10\n alias NAS Storage Device\n}\ndefine host {\n host_name Camera_Status\n alias Cameras\n hostgroups Hikvision_camera\n}\ndefine service {\n host_name Camera_Status\n service_description Gate 01 Camera\n check_command check_camera!192.168.2.11\n}\ndefine service {\n host_name Camera_Status\n service_description Gate 02 Camera\n check_command check_camera!192.168.2.12\n}\ndefine host {\n host_name EPSON_WF-C5890\n address 192.168.1.20\n alias Network Printer\n}\n'''
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'backup.tar.gz'
            with tarfile.open(path,'w:gz') as archive:
                data=config.encode();info=tarfile.TarInfo('usr/local/nagios/etc/server/hosts.cfg');info.size=len(data)
                archive.addfile(info,io.BytesIO(data))
                link=tarfile.TarInfo('usr/local/nagios/etc/server/linked.cfg');link.type=tarfile.SYMTYPE;link.linkname='../outside.cfg'
                archive.addfile(link)
            hosts,report=loader.read_backup(path)
            self.assertEqual(len(hosts),4)
            self.assertEqual({h['kind'] for h in hosts},{'nas','printer','camera'})
            self.assertEqual(report['camera_from_checks'],2)
            self.assertEqual({h['group_name'] for h in hosts if h['kind']=='camera'},{'Cameras'})
            self.assertFalse((Path(folder).parent/'outside.cfg').exists())

    def test_explicit_device_categories(self):
        self.assertEqual(loader.classify('Synology_NAS','NAS Storage Device')[0],'nas')
        self.assertEqual(loader.classify('imageRUNNER_IR3020','Network Printer')[0],'printer')
        self.assertEqual(loader.classify('Gate_Camera','Cameras')[0],'camera')
        self.assertEqual(loader.classify('IP-Phone_Office','SIP Phone')[0],'phone')
