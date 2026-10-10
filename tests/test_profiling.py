import asyncio
import json
import urllib.error
from unittest.mock import patch, AsyncMock
import profiling
from test_app import ApiTestCase

class ProfileApi(ApiTestCase):
    def test_fetch_requires_credentials_and_persists_redacted_inventory(self):
        self.post('/api/devices',dict(name='Manual name',ip='192.168.1.1',kind='router'))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/fetch-device-details',dict(id=1))
        self.assertEqual(error.exception.code,400)
        self.post('/api/snmp',dict(device_id=1,version='2c',community='private-fixture'))
        profile=dict(fetched_at=100,system=dict(hostname='SNMP router'),interfaces=[dict(index=1,name='eth0',status='1',speed_bps='1000000000')],warnings=[])
        with patch('profiling.fetch_profile',return_value=profile) as fetch:
            self.post('/api/fetch-device-details',dict(id=1))
            self.assertEqual(fetch.call_args.args[0],'192.168.1.1')
        with self.opener.open(self.url+'/api/device?id=1') as response:
            details=json.load(response)
        self.assertEqual(details['profile'],profile)
        self.assertEqual(details['host']['name'],'Manual name')
        self.assertNotIn('private-fixture',json.dumps(details))
        with patch('profiling.fetch_profile',side_effect=ValueError('SNMP unavailable')):
            with self.assertRaises(urllib.error.HTTPError):
                self.post('/api/fetch-device-details',dict(id=1))
        with self.opener.open(self.url+'/api/device?id=1') as response:
            self.assertEqual(json.load(response)['profile'],profile)
        self.post('/api/delete-device',dict(id=1))
        import app
        with app.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM device_profiles').fetchone()[0],0)

class ProfileCollection(ApiTestCase):
    def test_partial_inventory_keeps_supported_fields(self):
        async def scalar(address,port,oid,credentials,timeout):
            return ('OK','Router',None) if oid==profiling.SYSTEM['hostname'] else ('UNKNOWN','unsupported',None)
        async def column(address,credentials,oid):
            if oid.endswith('.2'):return {1:'ether1'}
            raise ValueError('unsupported')
        with patch('profiling.snmp_get',side_effect=scalar),patch('profiling.interface_column',side_effect=column):
            result=profiling.fetch_profile('192.168.1.1',dict(version='2c',community='fixture'))
        self.assertEqual(result['system'],dict(hostname='Router'))
        self.assertEqual(result['interfaces'][0]['name'],'ether1')
        self.assertIsNone(result['interfaces'][0]['status'])
        self.assertTrue(result['warnings'])
    def test_failed_system_probe_does_not_report_fake_information(self):
        with patch('profiling.snmp_get',new=AsyncMock(return_value=('UNKNOWN','unsupported',None))):
            with self.assertRaisesRegex(ValueError,'SNMP did not respond'):
                profiling.fetch_profile('192.168.1.1',dict(version='2c',community='fixture'))

class InterfaceWalk(ApiTestCase):
    def test_real_udp_interface_walk_stops_outside_subtree(self):
        import socket, threading
        from pyasn1.codec.ber import decoder,encoder
        from pysnmp.proto import api
        protocol=api.PROTOCOL_MODULES[1]
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1',0));sock.settimeout(5)
        errors=[];root='1.3.6.1.2.1.2.2.1.2'
        def respond():
            try:
                for oid,value in [(root+'.1','ether1'),('1.3.6.1.3.0','outside')]:
                    packet,address=sock.recvfrom(4096)
                    request,_=decoder.decode(packet,asn1Spec=protocol.Message())
                    response=protocol.apiMessage.get_response(request)
                    pdu=protocol.apiMessage.get_pdu(response)
                    protocol.apiPDU.set_varbinds(pdu,[(oid,protocol.OctetString(value))])
                    sock.sendto(encoder.encode(response),address)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=respond);thread.start()
        try:
            values=asyncio.run(profiling.interface_column('127.0.0.1',dict(version='2c',community='fixture'),root,port=sock.getsockname()[1]))
            thread.join(timeout=5)
            self.assertEqual(errors,[])
            self.assertEqual(values,{1:'ether1'})
        finally:sock.close();thread.join(timeout=5)
