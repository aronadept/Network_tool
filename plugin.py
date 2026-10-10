"""Nagios-compatible checks. Numeric object IDs only; secrets never enter command lines."""
import sys
import app
import checks
from security import decrypt_credentials


def execute(kind, identifier):
    with app.connect() as conn:
        if kind == 'host':
            device = conn.execute('SELECT * FROM devices WHERE id=?', (identifier,)).fetchone()
            if not device:
                return 'UNKNOWN', 'Host does not exist', None
            state, latency = app.probe(device['ip'])
            if state == 'up':
                return 'OK', f'Ping responding: {latency if latency is not None else "?"} ms', latency
            if state == 'down':
                return 'CRITICAL', 'No ICMP response', None
            return 'UNKNOWN', 'Install iputils-ping or check ICMP permissions', None
        check = conn.execute('SELECT * FROM services WHERE id=?', (identifier,)).fetchone()
        if not check:
            return 'UNKNOWN', 'Service does not exist', None
        device = conn.execute('SELECT * FROM devices WHERE id=?', (check['device_id'],)).fetchone()
        credentials = decrypt_credentials(app.DB, device['snmp_secret']) if device['snmp_secret'] else None
    return checks.run_check(device, check, credentials)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] not in ('host', 'service'):
            raise ValueError('Invalid arguments')
        state, output, value = execute(sys.argv[1], int(sys.argv[2]))
        safe = output.replace('\n',' ').replace('\r',' ').replace('|','/')
        performance = f' | value={value}' if value is not None else ''
        print(f'{state} - {safe}{performance}')
        sys.exit({'OK':0,'WARNING':1,'CRITICAL':2,'UNKNOWN':3}[state])
    except Exception:
        print('UNKNOWN - Check failed; verify configuration, prerequisites and credential key')
        sys.exit(3)
