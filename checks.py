"""Bounded checks returning Nagios-style states without running arbitrary commands."""
import asyncio
import http.client
import math
import socket
import ssl
import time


def threshold(value, warning, critical):
    if not math.isfinite(value):
        return 'UNKNOWN'
    if critical is not None and value >= critical:
        return 'CRITICAL'
    if warning is not None and value >= warning:
        return 'WARNING'
    return 'OK'


async def snmp_get(address, port, oid, credentials, timeout):
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine, CommunityData, UsmUserData, UdpTransportTarget, ContextData,
        ObjectType, ObjectIdentity, get_cmd, usmHMAC192SHA256AuthProtocol, usmAesCfb128Protocol)
    engine = SnmpEngine()
    try:
        if credentials['version'] == '2c':
            auth = CommunityData(credentials['community'], mpModel=1)
        else:
            auth = UsmUserData(credentials['username'], credentials['auth_password'],
                               credentials['priv_password'], authProtocol=usmHMAC192SHA256AuthProtocol,
                               privProtocol=usmAesCfb128Protocol)
        target = await UdpTransportTarget.create((address, port), timeout=timeout, retries=0)
        error, status, _, bindings = await get_cmd(engine, auth, target, ContextData(),
                                                  ObjectType(ObjectIdentity(oid)))
        if error or status or not bindings:
            return 'UNKNOWN', 'SNMP request failed: check reachability, credentials and OID', None
        value = bindings[0][1]
        if value.__class__.__name__ in ('NoSuchObject', 'NoSuchInstance', 'EndOfMibView'):
            return 'UNKNOWN', 'SNMP OID is not supported by this device', None
        text = value.prettyPrint()[:240]
        try:
            numeric = float(value)
        except (ValueError, TypeError):
            numeric = None
        return 'OK', text, numeric
    finally:
        engine.close_dispatcher()


def run_check(device, check, credentials=None):
    started = time.monotonic()
    kind = check['kind']
    try:
        if kind == 'tcp':
            with socket.create_connection((device['ip'], check['port']), timeout=check['timeout']):
                pass
            value = round((time.monotonic() - started) * 1000, 2)
            return threshold(value, check['warning'], check['critical']), f"TCP {check['port']} connected in {value} ms", value
        if kind in ('http', 'https'):
            cls = http.client.HTTPSConnection if kind == 'https' else http.client.HTTPConnection
            kwargs = {'timeout': check['timeout']}
            if kind == 'https':
                kwargs['context'] = ssl.create_default_context()
            conn = cls(device['ip'], check['port'], **kwargs)
            try:
                conn.request('GET', check['path'], headers={'User-Agent': 'NetworkMonitor/1.0'})
                response = conn.getresponse()
                code = response.status
                value = round((time.monotonic() - started) * 1000, 2)
                state = threshold(value, check['warning'], check['critical']) if 200 <= code < 400 else 'CRITICAL'
                return state, f'HTTP {code}, {value} ms (redirects not followed)', value
            finally:
                conn.close()
        if kind == 'snmp':
            if not credentials:
                return 'UNKNOWN', 'Configure SNMP credentials for this host', None
            state, output, value = asyncio.run(snmp_get(device['ip'], check['port'], check['oid'], credentials, check['timeout']))
            if state == 'OK' and (check['warning'] is not None or check['critical'] is not None):
                if value is None:
                    return 'UNKNOWN', 'Numeric thresholds require a numeric OID value', None
                state = threshold(value, check['warning'], check['critical'])
            return state, output, value
        return 'UNKNOWN', 'Unsupported check type', None
    except ssl.SSLError:
        return 'UNKNOWN', 'TLS certificate verification failed; configure a trusted device certificate', None
    except (TimeoutError, ConnectionError, socket.gaierror, OSError, http.client.HTTPException):
        return 'CRITICAL', f'{kind.upper()} connection failed or timed out', None
    except Exception:
        # Do not return exception strings that could include SNMP credentials.
        return 'UNKNOWN', 'Check execution failed; verify configuration and installed dependencies', None
