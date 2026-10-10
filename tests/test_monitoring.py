import asyncio
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import app
import checks
import engine
from security import decrypt_credentials, password_hash, password_matches
from test_app import ApiTestCase


class AuthAndConfiguration(ApiTestCase):
    def test_sessions_and_csrf_protect_mutations(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url+'/api/state')
        self.assertEqual(error.exception.code,401)
        token=self.csrf;self.csrf='incorrect'
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/devices',{'name':'Blocked','ip':'127.0.0.1','kind':'server'})
        self.assertEqual(error.exception.code,403)
        self.csrf=token
        self.post('/api/logout',{})
        with self.assertRaises(urllib.error.HTTPError) as error:self.state()
        self.assertEqual(error.exception.code,401)

    def test_snmp_secret_encryption_services_and_acknowledgement(self):
        self.post('/api/devices',{'name':'Switch','ip':'127.0.0.1','kind':'switch'})
        self.post('/api/snmp',{'device_id':1,'version':'2c','community':'test-secret-community'})
        state=self.state()
        self.assertNotIn('test-secret-community',json.dumps(state))
        self.assertNotIn('snmp_secret',state['devices'][0])
        with app.connect() as conn:
            secret=conn.execute('SELECT snmp_secret FROM devices').fetchone()[0]
        self.assertNotIn('test-secret-community',secret)
        self.assertEqual(decrypt_credentials(app.DB,secret)['community'],'test-secret-community')
        self.post('/api/services',{'device_id':1,'name':'Uptime','kind':'snmp','oid':'1.3.6.1.2.1.1.3.0','port':161,'interval':10,'warning':100,'critical':200})
        app.record_result('services',1,'CRITICAL','value 300',100,1,'SOFT')
        app.record_result('services',1,'CRITICAL','value 300',110,3,'HARD')
        self.post('/api/acknowledge',{'target':'service','id':1,'note':'Investigating'})
        self.assertEqual(self.state()['services'][0]['acknowledged'],1)
        self.post('/api/maintenance',{'id':1,'minutes':60})
        self.assertGreater(self.state()['devices'][0]['maintenance_until'],time.time())
        app.record_result('services',1,'OK','Recovered',120,1,'HARD')
        self.assertEqual(self.state()['services'][0]['acknowledged'],0)
        self.assertEqual(len(self.state()['events']),3)

    def test_bad_thresholds_and_credentials_are_rejected(self):
        self.post('/api/devices',{'name':'Router','ip':'127.0.0.1','kind':'router'})
        for data in [dict(device_id=1,name='Bad',kind='tcp',warning=20,critical=10),
                     dict(device_id=1,name='Bad',kind='tcp',warning='nan'),
                     dict(device_id=1,name='Bad',kind='http',path='http://external.example'),
                     dict(device_id=1,name='Bad',kind='snmp',oid='not-an-oid')]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.post('/api/services',data)
            self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/snmp',dict(device_id=1,version='3',username='test',auth_password='short',priv_password='short'))


class Checks(unittest.TestCase):
    def test_tcp_and_http_real_requests(self):
        class LocalHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path=='/health' else 503)
                self.end_headers();self.wfile.write(b'local fixture')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),LocalHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=dict(port=server.server_port,timeout=2,warning=None,critical=None,path='/health',oid='')
        try:
            for kind in ['tcp','http']:
                state,_,value=checks.run_check({'ip':'127.0.0.1'},dict(base,kind=kind))
                self.assertEqual(state,'OK');self.assertGreaterEqual(value,0)
            self.assertEqual(checks.run_check({'ip':'127.0.0.1'},dict(base,kind='http',path='/failed'))[0],'CRITICAL')
        finally:
            server.shutdown();server.server_close();thread.join()
        self.assertEqual(checks.run_check({'ip':'127.0.0.1'},dict(base,kind='tcp'))[0],'CRITICAL')

    def test_thresholds_tls_and_missing_credentials(self):
        self.assertEqual(checks.threshold(30,10,20),'CRITICAL')
        self.assertEqual(checks.threshold(15,10,20),'WARNING')
        self.assertEqual(checks.threshold(float('nan'),None,None),'UNKNOWN')
        c=dict(kind='snmp',timeout=1,port=161,oid='1.3.6.1.2.1.1.3.0',warning=None,critical=None)
        self.assertEqual(checks.run_check({'ip':'127.0.0.1'},c)[0],'UNKNOWN')
        with patch('http.client.HTTPSConnection',side_effect=ssl.SSLError('certificate invalid')):
            self.assertEqual(checks.run_check({'ip':'127.0.0.1'},dict(c,kind='https'))[0],'UNKNOWN')

    def test_snmp_v3_authpriv_real_udp_roundtrip(self):
        from pysnmp.entity import engine as snmp_engine, config
        from pysnmp.carrier.asyncio.dgram import udp
        from pysnmp.entity.rfc3413 import cmdrsp, context
        from pysnmp.proto.rfc1902 import TimeTicks
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1];sock.close()
        ready=threading.Event();state={};errors=[]
        def agent():
            loop=asyncio.new_event_loop();asyncio.set_event_loop(loop);state['loop']=loop
            snmp=snmp_engine.SnmpEngine()
            try:
                config.add_transport(snmp,udp.DOMAIN_NAME,udp.UdpTransport().open_server_mode(('127.0.0.1',port)))
                config.add_v3_user(snmp,'fixtureuser',config.USM_AUTH_HMAC192_SHA256,'fixture-auth-123',config.USM_PRIV_CFB128_AES,'fixture-priv-123')
                config.add_vacm_user(snmp,3,'fixtureuser','authPriv',(1,3,6,1,2,1))
                ctx=context.SnmpContext(snmp)
                builder=ctx.get_mib_instrum().get_mib_builder()
                instance,=builder.import_symbols('SNMPv2-SMI','MibScalarInstance')
                builder.export_symbols('NETWORK_TEST',instance((1,3,6,1,2,1,1,3),(0,),TimeTicks(12345)))
                responder=cmdrsp.GetCommandResponder(snmp,ctx)
                snmp.transport_dispatcher.job_started(1)
                loop.call_later(.1,ready.set)
                snmp.transport_dispatcher.run_dispatcher()
            except Exception as exc:
                errors.append(exc);ready.set()
            finally:
                snmp.close_dispatcher()
                pending=asyncio.all_tasks(loop)
                for task in pending:task.cancel()
                loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
                loop.close()
        thread=threading.Thread(target=agent);thread.start()
        try:
            self.assertTrue(ready.wait(5));self.assertEqual(errors,[])
            c=dict(kind='snmp',port=port,timeout=3,oid='1.3.6.1.2.1.1.3.0',warning=None,critical=None)
            result=checks.run_check({'ip':'127.0.0.1'},c,dict(version='3',username='fixtureuser',auth_password='fixture-auth-123',priv_password='fixture-priv-123'))
            self.assertEqual(result[0],'OK');self.assertEqual(result[2],12345)
        finally:
            if 'loop' in state and state['loop'].is_running():state['loop'].call_soon_threadsafe(state['loop'].stop)
            thread.join(5)
            self.assertFalse(thread.is_alive())

    def test_password_hashing(self):
        stored=password_hash('Test-password-long')
        self.assertNotIn('Test-password-long',stored)
        self.assertTrue(password_matches('Test-password-long',stored))
        self.assertFalse(password_matches('wrong',stored))
        with self.assertRaises(ValueError):password_hash('short')

    def test_snmp_v2c_real_udp_roundtrip(self):
        from pyasn1.codec.ber import decoder,encoder
        from pysnmp.proto import api
        protocol=api.PROTOCOL_MODULES[1]
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind(('127.0.0.1',0));sock.settimeout(5)
        errors=[]
        def respond():
            try:
                packet,address=sock.recvfrom(4096)
                request,_=decoder.decode(packet,asn1Spec=protocol.Message())
                response=protocol.apiMessage.get_response(request)
                pdu=protocol.apiMessage.get_pdu(response)
                protocol.apiPDU.set_varbinds(pdu,[('1.3.6.1.2.1.1.3.0',protocol.TimeTicks(12345))])
                sock.sendto(encoder.encode(response),address)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=respond);thread.start()
        try:
            c=dict(kind='snmp',port=sock.getsockname()[1],timeout=2,oid='1.3.6.1.2.1.1.3.0',warning=10000,critical=20000)
            state,output,value=checks.run_check({'ip':'127.0.0.1'},c,dict(version='2c',community='fixture-only'))
            thread.join(timeout=5)
            self.assertEqual(errors,[]);self.assertEqual(state,'WARNING');self.assertEqual(value,12345)
        finally:sock.close();thread.join(timeout=5)


class MigrationAndCore(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=app.DB;app.DB=Path(self.tmp.name)/'test.db'
    def tearDown(self):app.DB=self.old;self.tmp.cleanup()
    def test_old_database_migrates_without_losing_inventory(self):
        conn=sqlite3.connect(app.DB)
        conn.executescript("CREATE TABLE devices(id INTEGER PRIMARY KEY,name TEXT NOT NULL,ip TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'unknown',latency REAL,checked_at REAL); INSERT INTO devices(name,ip,kind) VALUES('Existing switch','192.168.1.1','switch');")
        conn.close();app.initialize();app.initialize()
        with app.connect() as conn:
            self.assertEqual(conn.execute('SELECT name FROM devices').fetchone()[0],'Existing switch')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM services').fetchone()[0],0)

    @unittest.skipUnless(engine.binary(),'Nagios Core binary not installed')
    def test_real_nagios_runs_service_plugin_and_writes_status(self):
        app.initialize()
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen()
        with app.connect() as conn:
            conn.execute("INSERT INTO devices(name,ip,kind) VALUES('Loopback','127.0.0.1','server')")
            conn.execute("INSERT INTO services(device_id,name,kind,port,interval,max_attempts) VALUES(1,'Local TCP','tcp',?,10,1)",(sock.getsockname()[1],))
        config=engine.prepare(app.DB)
        log=(Path(self.tmp.name)/'core.log').open('w')
        process=subprocess.Popen([engine.binary(),str(config)],stdout=log,stderr=log)
        try:
            deadline=time.time()+35
            while time.time()<deadline:
                self.assertIsNone(process.poll(),Path(log.name).read_text())
                engine.sync(app.DB)
                with app.connect() as conn:
                    service=conn.execute('SELECT * FROM services').fetchone()
                if service['state']=='OK':break
                time.sleep(.5)
            self.assertEqual(service['state'],'OK',Path(log.name).read_text())
            self.assertEqual(service['state_type'],'HARD')
            self.assertIn('TCP',service['output'])
            self.assertGreater(service['checked_at'],time.time()-40)
        finally:
            process.terminate();process.wait(timeout=10);log.close();sock.close()

if __name__=='__main__':unittest.main()
