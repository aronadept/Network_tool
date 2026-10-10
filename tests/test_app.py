import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
import app

class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=app.DB
        app.DB=Path(self.tmp.name)/'test.db'
        app.initialize()
        app.create_admin('tester','Test-only-password-123')
        self.csrf=''
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.csrf=self.post('/api/login',{'username':'tester','password':'Test-only-password-123'})['csrf']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        app.DB=self.old;self.tmp.cleanup()
    def post(self,path,data):
        req=urllib.request.Request(self.url+path,json.dumps(data).encode(),{'Content-Type':'application/json','X-CSRF-Token':self.csrf})
        with self.opener.open(req) as r:return json.load(r)
    def state(self):
        with self.opener.open(self.url+'/api/state') as r:return json.load(r)

class Workflow(ApiTestCase):
    def test_service_details_only_returns_selected_service_events(self):
        self.post('/api/devices',dict(name='Router',ip='192.168.1.1',kind='router'))
        host=self.state()['devices'][0]['id']
        with app.connect() as conn:
            conn.execute("UPDATE devices SET snmp_secret='private-fixture-secret' WHERE id=?",(host,))
            first=conn.execute("INSERT INTO services(device_id,name,kind,port) VALUES(?,'HTTP','http',80)",(host,)).lastrowid
            second=conn.execute("INSERT INTO services(device_id,name,kind,port) VALUES(?,'SSH','tcp',22)",(host,)).lastrowid
        app.record_result('services',first,'CRITICAL','HTTP failed',200,3,'HARD')
        app.record_result('services',second,'OK','SSH responding',200,1,'HARD')
        with self.opener.open(self.url+'/api/service?id='+str(first)) as response:
            details=json.load(response)
        self.assertEqual(details['service']['host_name'],'Router')
        self.assertEqual(details['service']['name'],'HTTP')
        self.assertEqual([e['output'] for e in details['events']],['HTTP failed'])
        self.assertNotIn('private-fixture-secret',json.dumps(details))
        self.assertNotIn('snmp_secret',details['service'])
        for query,code in [('bad',400),('0',400),('99999',404)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.opener.open(self.url+'/api/service?id='+query)
            self.assertEqual(error.exception.code,code)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url+'/api/service?id='+str(first))
        self.assertEqual(error.exception.code,401)

    def test_host_details_redaction_history_and_access(self):
        for name,ip in [('Router','192.168.1.1'),('Switch','192.168.1.2')]:
            self.post('/api/devices',dict(name=name,ip=ip,kind='router'))
        a,b=[d['id'] for d in self.state()['devices']]
        with app.connect() as conn:
            conn.execute("UPDATE devices SET snmp_secret='private-fixture-secret',snmp_version='3' WHERE id=?",(a,))
        app.record_result('devices',a,'CRITICAL','Router offline',200,3,'HARD')
        app.record_result('devices',b,'OK','Other host output',200,1,'HARD')
        self.post('/api/links',dict(source=a,target=b,label='uplink'))
        with self.opener.open(self.url+'/api/device?id='+str(a)) as response:
            details=json.load(response)
        self.assertTrue(details['host']['snmp_configured'])
        self.assertNotIn('snmp_secret',details['host'])
        self.assertNotIn('private-fixture-secret',json.dumps(details))
        self.assertEqual([e['output'] for e in details['events']],['Router offline'])
        self.assertEqual(details['links'][0]['label'],'uplink')
        for query,code in [('bad',400),('99999',404)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.opener.open(self.url+'/api/device?id='+query)
            self.assertEqual(error.exception.code,code)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url+'/api/device?id='+str(a))
        self.assertEqual(error.exception.code,401)

    def test_inventory_monitor_topology_and_delete(self):
        self.post('/api/devices',{'name':'Router','ip':'127.0.0.1','kind':'router'})
        self.post('/api/devices',{'name':'Switch','ip':'192.168.1.2','kind':'switch'})
        for d in self.state()['devices']:
            app.record_result('devices',d['id'],'OK','Ping responding: 1.25 ms',100,1,'HARD')
            app.record_result('devices',d['id'],'OK','Ping responding: 1.25 ms',100,1,'HARD')
        s=self.state();self.assertEqual(len(s['events']),2)
        self.assertTrue(all(d['status']=='up' for d in s['devices']))
        a,b=[d['id'] for d in s['devices']]
        self.post('/api/links',{'source':a,'target':b,'label':'uplink'})
        self.assertEqual(self.state()['links'][0]['label'],'uplink')
        for d in self.state()['devices']:
            app.record_result('devices',d['id'],'CRITICAL','No ICMP response',200,3,'HARD')
        self.assertEqual(len(self.state()['events']),4)
        self.post('/api/delete-device',{'id':a})
        self.assertEqual(len(self.state()['devices']),1)
        self.assertEqual(self.state()['links'],[])
    def test_invalid_address_and_cross_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post('/api/devices',{'name':'Bad','ip':'127.0.0.1;echo bad','kind':'router'})
        self.assertEqual(e.exception.code,400)
        req=urllib.request.Request(self.url+'/api/devices',b'{}',{'Origin':'http://evil.example'})
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
        self.assertEqual(e.exception.code,403)
    def test_discovery_deduplicates_and_ignores_down_hosts(self):
        def fake(address):
            return ('up', 1) if address == '192.168.1.1' else ('down', None)
        with patch('app.probe', side_effect=fake):
            app.discover('192.168.1.0/30')
            app.discover('192.168.1.0/30')
        self.assertEqual(self.state()['devices'], [])
        with app.connect() as conn:
            rows=conn.execute('SELECT * FROM discovery_results').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['ip'],'192.168.1.1')
        self.post('/api/add-discovered',{'ips':['192.168.1.1']})
        self.assertEqual(len(self.state()['devices']),1)
        self.assertEqual(self.post('/api/add-discovered',{'ips':['192.168.1.1']})['skipped'],1)
        with self.assertRaises(ValueError):app.discover('192.168.0.0/15')

    def test_discovery_approval_is_explicit_and_atomic(self):
        with app.connect() as conn:
            conn.execute("INSERT INTO discovery(subnet) VALUES('192.168.1.0/24')")
            conn.execute("INSERT INTO discovery_results(ip,subnet,last_seen,latency) VALUES('192.168.1.1','192.168.1.0/24',100,1)")
            conn.execute("INSERT INTO discovery_results(ip,subnet,last_seen,latency) VALUES('192.168.1.2','192.168.1.0/24',100,2)")
        self.assertEqual(len(self.state()['discovery_results']),2)
        self.assertEqual(self.state()['devices'],[])
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/add-discovered',{'ips':['192.168.1.1','192.168.1.3']})
        self.assertEqual(self.state()['devices'],[])
        self.assertEqual(self.post('/api/add-discovered',{'ips':['192.168.1.2']})['added'],1)
        result={r['ip']:r for r in self.state()['discovery_results']}
        self.assertIsNone(result['192.168.1.1']['device_id'])
        self.assertIsNotNone(result['192.168.1.2']['device_id'])

    def test_large_subnet_configuration_and_bounds(self):
        with patch('app.shutil.which', return_value='/usr/bin/ping'):
            self.post('/api/discovery', {'subnet': '10.5.12.0/16'})
            self.assertEqual(self.state()['discovery_subnet'], '10.5.0.0/16')
            for subnet in ['10.0.0.0/15', '::/64']:
                with self.assertRaises(urllib.error.HTTPError) as e:
                    self.post('/api/discovery', {'subnet': subnet})
                self.assertEqual(e.exception.code, 400)
            self.post('/api/discovery', {'subnet': ''})
            self.assertIsNone(self.state()['discovery_subnet'])

    def test_large_scan_cancels_with_bounded_work(self):
        def cancelled():
            with app.DISCOVERY_LOCK:
                return app.DISCOVERY_PROGRESS['checked'] >= 40
        with patch('app.probe', return_value=('down', None)) as probe:
            app.discover('10.5.0.0/16', cancelled)
        self.assertLessEqual(probe.call_count, 40 + app.DISCOVERY_WORKERS)
        self.assertEqual(app.DISCOVERY_PROGRESS['total'], 65534)
        self.assertEqual(app.DISCOVERY_PROGRESS['status'], 'cancelled')

    def test_missing_ping_not_offline(self):
        with patch('app.shutil.which',return_value=None):self.assertEqual(app.probe('127.0.0.1'),('unavailable',None))

if __name__=='__main__':unittest.main()
