"""Network Monitor web/API layer backed by Nagios Core."""
import atexit
import concurrent.futures
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import engine
import profiling
from security import password_hash, password_matches, encrypt_credentials, decrypt_credentials

DB = Path(os.environ.get('NETWORK_DB', 'data/network.db'))
DISCOVERY_WORKERS = max(1, min(64, int(os.environ.get('DISCOVERY_WORKERS', '32'))))
DISCOVERY_WAKE = threading.Event()
DISCOVERY_LOCK = threading.Lock()
DISCOVERY_GENERATION = 0
DISCOVERY_PROGRESS = {'status': 'idle', 'checked': 0, 'total': 0, 'found': 0, 'subnet': None}
INTERVAL = max(10, int(os.environ.get('POLL_INTERVAL', '30')))
LOGIN_LOCK = threading.Lock()
LOGIN_ATTEMPTS = {}


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(DB, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def initialize():
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS device_profiles (device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS discovery_results (
          ip TEXT PRIMARY KEY, subnet TEXT NOT NULL, last_seen REAL NOT NULL, latency REAL);
        CREATE TABLE IF NOT EXISTS discovery (id INTEGER PRIMARY KEY CHECK(id=1), subnet TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS devices (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unknown', latency REAL, checked_at REAL);
        CREATE TABLE IF NOT EXISTS links (
          id INTEGER PRIMARY KEY, source INTEGER REFERENCES devices(id) ON DELETE CASCADE,
          target INTEGER REFERENCES devices(id) ON DELETE CASCADE, label TEXT NOT NULL,
          UNIQUE(source,target), CHECK(source != target));
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
          status TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS services (
          id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          name TEXT NOT NULL, kind TEXT NOT NULL, port INTEGER NOT NULL, path TEXT NOT NULL DEFAULT '/',
          oid TEXT NOT NULL DEFAULT '', warning REAL, critical REAL, timeout INTEGER NOT NULL DEFAULT 3,
          interval INTEGER NOT NULL DEFAULT 60, max_attempts INTEGER NOT NULL DEFAULT 3,
          state TEXT NOT NULL DEFAULT 'PENDING', state_type TEXT NOT NULL DEFAULT 'SOFT',
          attempts INTEGER NOT NULL DEFAULT 0, output TEXT NOT NULL DEFAULT 'Waiting for first check',
          checked_at REAL, acknowledged INTEGER NOT NULL DEFAULT 0, ack_note TEXT NOT NULL DEFAULT '',
          UNIQUE(device_id,name));
        """)
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(devices)')}
        additions = {'brand':"TEXT NOT NULL DEFAULT ''", 'model':"TEXT NOT NULL DEFAULT ''",
          'group_name':"TEXT NOT NULL DEFAULT 'Network'", 'snmp_secret':"TEXT NOT NULL DEFAULT ''",
          'snmp_version':"TEXT NOT NULL DEFAULT ''", 'state':"TEXT NOT NULL DEFAULT 'PENDING'",
          'state_type':"TEXT NOT NULL DEFAULT 'SOFT'", 'attempts':'INTEGER NOT NULL DEFAULT 0',
          'output':"TEXT NOT NULL DEFAULT 'Waiting for Nagios check'", 'acknowledged':'INTEGER NOT NULL DEFAULT 0',
          'ack_note':"TEXT NOT NULL DEFAULT ''", 'maintenance_until':'REAL NOT NULL DEFAULT 0'}
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE devices ADD COLUMN {name} {definition}')
        event_columns = {r['name'] for r in conn.execute('PRAGMA table_info(events)')}
        for name, definition in {'service_id':'INTEGER REFERENCES services(id) ON DELETE CASCADE',
                                  'output':"TEXT NOT NULL DEFAULT ''", 'state_type':"TEXT NOT NULL DEFAULT 'HARD'"}.items():
            if name not in event_columns:
                conn.execute(f'ALTER TABLE events ADD COLUMN {name} {definition}')
    os.chmod(DB, 0o600)


def create_admin(username, password):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', username):
        raise ValueError('Username must be 1–40 letters, digits, dots, underscores or hyphens')
    hashed = password_hash(password)
    with connect() as conn:
        conn.execute('INSERT INTO users(username,password_hash) VALUES(?,?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash', (username, hashed))
        user = conn.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        conn.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))


def record_result(table, identifier, state, output, checked_at, attempts, state_type):
    if table not in ('devices','services'):
        raise ValueError('Invalid result table')
    with connect() as conn:
        previous = conn.execute(f'SELECT * FROM {table} WHERE id=?', (identifier,)).fetchone()
        if not previous or (previous['checked_at'] and checked_at <= previous['checked_at']):
            return
        changed = previous['state'] != state or previous['state_type'] != state_type
        reset_ack = previous['state'] != state or state == 'OK'
        conn.execute(f'UPDATE {table} SET state=?,output=?,checked_at=?,attempts=?,state_type=?,acknowledged=?,ack_note=? WHERE id=?',
          (state,output,checked_at,attempts,state_type,0 if reset_ack else previous['acknowledged'],
           '' if reset_ack else previous['ack_note'],identifier))
        if table == 'devices':
            status = {'OK':'up','CRITICAL':'down','UNKNOWN':'unavailable'}.get(state, 'unknown')
            latency = re.search(r'Ping responding: ([\d.]+) ms', output)
            conn.execute('UPDATE devices SET status=?,latency=? WHERE id=?', (status,float(latency.group(1)) if latency else None,identifier))
        if changed:
            conn.execute('INSERT INTO events(device_id,service_id,status,created_at,output,state_type) VALUES(?,?,?,?,?,?)',
              (identifier if table=='devices' else previous['device_id'], None if table=='devices' else identifier,state,checked_at,output,state_type))
            conn.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 10000)')


def probe(address):
    if not shutil.which('ping'):
        return 'unavailable', None
    result = subprocess.run(['ping', '-n', '-c', '1', '-W', '2', address], capture_output=True, text=True, timeout=5)
    match = re.search(r'time[=<]([\d.]+)', result.stdout)
    return ('up' if result.returncode == 0 else 'down', float(match.group(1)) if match else None)


def short_text(value, name, required=False, limit=80):
    value = str(value or '').strip()
    if len(value) > limit or (required and not value) or any(ord(c)<32 for c in value):
        raise ValueError(f'Invalid {name}')
    return value


def service_values(data):
    kind = data.get('kind')
    if kind not in ('tcp','http','https','snmp'):
        raise ValueError('Choose TCP, HTTP, HTTPS or SNMP')
    port = int(data.get('port') or {'tcp':22,'http':80,'https':443,'snmp':161}[kind])
    interval, timeout, retries = int(data.get('interval',60)), int(data.get('timeout',3)), int(data.get('max_attempts',3))
    if not 1<=port<=65535 or not 10<=interval<=86400 or not 1<=timeout<=10 or not 1<=retries<=10:
        raise ValueError('Invalid port, interval, timeout or retry count')
    path = short_text(data.get('path') or '/', 'HTTP path', limit=500)
    if not path.startswith('/') or path.startswith('//') or any(c.isspace() for c in path):
        raise ValueError('HTTP path must be a local path such as /health')
    oid = short_text(data.get('oid'), 'OID', limit=160).lstrip('.')
    if kind == 'snmp' and not re.fullmatch(r'[0-2](?:\.\d+){2,}', oid):
        raise ValueError('Supply a numeric SNMP OID')
    warning = float(data['warning']) if data.get('warning') not in ('',None) else None
    critical = float(data['critical']) if data.get('critical') not in ('',None) else None
    if any(v is not None and not math.isfinite(v) for v in (warning,critical)) or (warning is not None and critical is not None and warning>=critical):
        raise ValueError('Thresholds must be finite and warning must be below critical')
    return (int(data['device_id']), short_text(data.get('name'),'service name',True),kind,port,path,oid,warning,critical,timeout,interval,retries)


def discovery_network(subnet):
    network = ipaddress.ip_network(subnet, strict=False)
    if network.version != 4 or network.prefixlen < 16:
        raise ValueError('Use an IPv4 /16 or smaller subnet (such as /24)')
    return network


def discover(subnet, cancelled=lambda: False):
    network = discovery_network(subnet)
    total = network.num_addresses - 2 if network.prefixlen < 31 else network.num_addresses
    with DISCOVERY_LOCK:
        DISCOVERY_PROGRESS.update(status='scanning', checked=0, total=total, found=0,
                                  subnet=str(network), error=None)
    def inspect(address):
        try:
            status, latency = probe(str(address))
            return str(address), status, latency
        except (OSError, subprocess.TimeoutExpired):
            return str(address), 'unavailable', None
    addresses = iter(network.hosts())
    # Keep only one bounded batch in memory, even for a /16.
    with concurrent.futures.ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as pool:
        while not cancelled():
            import itertools
            batch = list(itertools.islice(addresses, DISCOVERY_WORKERS))
            if not batch:
                break
            futures = [pool.submit(inspect, address) for address in batch]
            for future in concurrent.futures.as_completed(futures):
                address, status, latency = future.result()
                if cancelled():
                    break
                if status == 'up':
                    with connect() as conn:
                        conn.execute('INSERT INTO discovery_results(ip,subnet,last_seen,latency) VALUES(?,?,?,?) ON CONFLICT(ip) DO UPDATE SET subnet=excluded.subnet,last_seen=excluded.last_seen,latency=excluded.latency',
                                     (address,str(network),time.time(),latency))
                with DISCOVERY_LOCK:
                    DISCOVERY_PROGRESS['checked'] += 1
                    DISCOVERY_PROGRESS['found'] += int(status == 'up')
    was_cancelled = cancelled()
    with DISCOVERY_LOCK:
        DISCOVERY_PROGRESS['status'] = 'cancelled' if was_cancelled else 'complete'


def discovery_loop():
    while True:
        DISCOVERY_WAKE.clear()
        with DISCOVERY_LOCK:
            generation = DISCOVERY_GENERATION
        def cancelled():
            with DISCOVERY_LOCK:
                return generation != DISCOVERY_GENERATION
        try:
            with connect() as conn:
                config = conn.execute('SELECT subnet FROM discovery WHERE id=1').fetchone()
            if config:
                if not shutil.which('ping'):
                    raise RuntimeError('Install iputils-ping to run discovery')
                discover(config['subnet'], cancelled)
            else:
                with DISCOVERY_LOCK:
                    DISCOVERY_PROGRESS.update(status='disabled', subnet=None)
        except (sqlite3.Error, ValueError, RuntimeError) as exc:
            with DISCOVERY_LOCK:
                DISCOVERY_PROGRESS.update(status='error', error=str(exc))
        DISCOVERY_WAKE.wait(300)


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type='application/json', cookie=None):
        data = json.dumps(body).encode() if content_type == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    def cookie(self, token, age=43200):
        secure = '; Secure' if os.environ.get('COOKIE_SECURE') == '1' else ''
        return f'network_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}'

    def session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie',''))
            token = cookie['network_session'].value
        except (KeyError, ValueError, CookieError):
            return None
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with connect() as conn:
            return conn.execute('SELECT s.*,u.username FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires_at>?', (hashed,time.time())).fetchone()

    def do_GET(self):
        if self.path == '/api/session':
            session = self.session()
            self.respond(200, {'authenticated':bool(session),'username':session['username'] if session else None,'csrf':session['csrf'] if session else None})
            return
        if urlsplit(self.path).path == '/api/service':
            if not self.session():
                self.respond(401, {'error':'Please sign in'})
                return
            try:
                service_id = int(parse_qs(urlsplit(self.path).query)['id'][0])
                if service_id < 1:
                    raise ValueError()
            except (KeyError, ValueError):
                self.respond(400, {'error':'A valid service ID is required'})
                return
            with connect() as conn:
                row = conn.execute('SELECT s.*,d.name AS host_name,d.ip,d.maintenance_until FROM services s JOIN devices d ON d.id=s.device_id WHERE s.id=?', (service_id,)).fetchone()
                if row is None:
                    self.respond(404, {'error':'Service not found'})
                    return
                events = [dict(r) for r in conn.execute('SELECT * FROM events WHERE service_id=? ORDER BY id DESC LIMIT 100', (service_id,))]
            self.respond(200, dict(service=dict(row), events=events))
            return
        if urlsplit(self.path).path == '/api/device':
            if not self.session():
                self.respond(401, {'error':'Please sign in'})
                return
            try:
                device_id = int(parse_qs(urlsplit(self.path).query)['id'][0])
                if device_id < 1:
                    raise ValueError()
            except (KeyError, ValueError):
                self.respond(400, {'error':'A valid device ID is required'})
                return
            with connect() as conn:
                row = conn.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
                if row is None:
                    self.respond(404, {'error':'Device not found'})
                    return
                host = dict(row)
                host['snmp_configured'] = bool(host.pop('snmp_secret'))
                services = [dict(r) for r in conn.execute('SELECT * FROM services WHERE device_id=? ORDER BY name', (device_id,))]
                events = [dict(r) for r in conn.execute('SELECT e.*,s.name AS service_name FROM events e LEFT JOIN services s ON s.id=e.service_id WHERE e.device_id=? ORDER BY e.id DESC LIMIT 100', (device_id,))]
                links = [dict(r) for r in conn.execute('SELECT l.*,a.name AS source_name,b.name AS target_name FROM links l JOIN devices a ON a.id=l.source JOIN devices b ON b.id=l.target WHERE l.source=? OR l.target=?', (device_id,device_id))]
                profile_row=conn.execute('SELECT payload FROM device_profiles WHERE device_id=?',(device_id,)).fetchone()
                profile=json.loads(profile_row['payload']) if profile_row else None
            self.respond(200, dict(host=host, services=services, events=events, links=links, interval=INTERVAL,profile=profile))
            return
        if self.path == '/api/state':
            if not self.session():
                self.respond(401, {'error':'Please sign in'})
                return
            with connect() as conn:
                devices = []
                for row in conn.execute('SELECT * FROM devices ORDER BY name'):
                    d = dict(row)
                    d['snmp_configured'] = bool(d.pop('snmp_secret'))
                    devices.append(d)
                services = [dict(r) for r in conn.execute('SELECT s.*,d.name AS host_name,d.ip,d.maintenance_until FROM services s JOIN devices d ON d.id=s.device_id ORDER BY d.name,s.name')]
                links = [dict(r) for r in conn.execute('SELECT * FROM links')]
                config = conn.execute('SELECT subnet FROM discovery WHERE id=1').fetchone()
                candidates = [dict(r) for r in conn.execute('SELECT r.*,d.id AS device_id FROM discovery_results r LEFT JOIN devices d ON d.ip=r.ip WHERE r.subnet=? ORDER BY r.ip', (config['subnet'] if config else '',))]
                events = [dict(r) for r in conn.execute('SELECT e.*,d.name,s.name AS service_name FROM events e JOIN devices d ON d.id=e.device_id LEFT JOIN services s ON s.id=e.service_id ORDER BY e.id DESC LIMIT 100')]
            with DISCOVERY_LOCK:
                progress = dict(DISCOVERY_PROGRESS)
            with engine.LOCK:
                health = dict(engine.HEALTH)
            self.respond(200, dict(devices=devices,services=services,links=links,events=events,engine=health,discovery_results=candidates,
                                   discovery_progress=progress,discovery_subnet=config['subnet'] if config else None,
                                   ping_available=bool(shutil.which('ping')),interval=INTERVAL))
        elif self.path in ('/', '/app.js', '/style.css', '/hl-agro-logo.svg'):
            name = 'index.html' if self.path == '/' else self.path[1:]
            mime = {'index.html':'text/html; charset=utf-8','app.js':'text/javascript','style.css':'text/css','hl-agro-logo.svg':'image/svg+xml'}[name]
            self.respond(200,(Path(__file__).parent/'web'/name).read_bytes(),mime)
        else:
            self.respond(404, {'error':'Not found'})

    def login(self, data):
        address = self.client_address[0]
        now = time.time()
        with LOGIN_LOCK:
            for key in list(LOGIN_ATTEMPTS):
                if LOGIN_ATTEMPTS[key][1] < now:
                    del LOGIN_ATTEMPTS[key]
            count, expires = LOGIN_ATTEMPTS.get(address,(0,now+300))
            if count >= 10:
                self.respond(429, {'error':'Too many login attempts; retry after 5 minutes'})
                return
            LOGIN_ATTEMPTS[address] = (count+1,expires)
        with connect() as conn:
            user = conn.execute('SELECT * FROM users WHERE username=?',(str(data.get('username','')),)).fetchone()
            # A dummy hash keeps unknown users on the same costly verification path.
            valid = password_matches(data.get('password',''),user['password_hash'] if user else DUMMY_HASH)
            if not user or not valid:
                self.respond(401, {'error':'Invalid username or password'})
                return
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            conn.execute('DELETE FROM sessions WHERE expires_at<?', (now,))
            conn.execute('INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)',
                         (hashlib.sha256(token.encode()).hexdigest(),user['id'],csrf,now+43200))
        with LOGIN_LOCK:
            LOGIN_ATTEMPTS.pop(address,None)
        self.respond(200, {'ok':True,'username':user['username'],'csrf':csrf},cookie=self.cookie(token))

    def do_POST(self):
        origin = self.headers.get('Origin')
        expected = os.environ.get('PUBLIC_ORIGIN') or 'http://' + self.headers.get('Host','')
        if origin and origin != expected:
            self.respond(403, {'error':'Origin rejected'})
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 8192:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data,dict):
                raise ValueError('Expected an object')
            if self.path == '/api/login':
                self.login(data)
                return
            session = self.session()
            if not session:
                self.respond(401, {'error':'Please sign in'})
                return
            if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):
                self.respond(403, {'error':'Invalid session token; reload the page'})
                return
            if self.path == '/api/logout':
                with connect() as conn:
                    conn.execute('DELETE FROM sessions WHERE token_hash=?',(session['token_hash'],))
                self.respond(200, {'ok':True},cookie=self.cookie('',0))
                return
            if self.path == '/api/fetch-device-details':
                device_id=int(data['id'])
                with connect() as conn:
                    host=conn.execute('SELECT ip,snmp_secret FROM devices WHERE id=?',(device_id,)).fetchone()
                if not host:
                    raise ValueError('Device not found')
                if not host['snmp_secret']:
                    raise ValueError('Save SNMP credentials for this device first')
                try:
                    credentials=decrypt_credentials(DB,host['snmp_secret'])
                except Exception:
                    raise ValueError('Stored SNMP credentials could not be read; save them again') from None
                profile=profiling.fetch_profile(host['ip'],credentials)
                with connect() as conn:
                    conn.execute('INSERT INTO device_profiles(device_id,payload) VALUES(?,?) ON CONFLICT(device_id) DO UPDATE SET payload=excluded.payload',(device_id,json.dumps(profile)))
                self.respond(200,{'ok':True,'profile':profile})
                return
            reload_engine = False
            response = {'ok':True}
            with connect() as conn:
                if self.path == '/api/discovery':
                    subnet = str(data.get('subnet','')).strip()
                    if subnet:
                        network = discovery_network(subnet)
                        if not shutil.which('ping'):
                            raise ValueError('Install iputils-ping before enabling discovery')
                        conn.execute('INSERT OR REPLACE INTO discovery(id,subnet) VALUES(1,?)',(str(network),))
                    else:
                        conn.execute('DELETE FROM discovery')
                elif self.path == '/api/add-discovered':
                    submitted = data.get('ips')
                    if not isinstance(submitted,list) or not 1<=len(submitted)<=256:
                        raise ValueError('Select 1 to 256 discovered devices')
                    addresses = list(dict.fromkeys(str(ipaddress.IPv4Address(address)) for address in submitted))
                    for address in addresses:
                        if not conn.execute('SELECT ip FROM discovery_results WHERE ip=?',(address,)).fetchone():
                            raise ValueError('Only discovered addresses can be added from this list')
                    added = 0
                    for address in addresses:
                        cursor = conn.execute("INSERT OR IGNORE INTO devices(name,ip,kind) VALUES(?,?,'other')", ('Discovered '+address,address))
                        added += cursor.rowcount
                    reload_engine = bool(added)
                    response.update(added=added,skipped=len(addresses)-added)
                elif self.path in ('/api/devices','/api/update-device'):
                    address = str(ipaddress.ip_address(data.get('ip','')))
                    if ipaddress.ip_address(address).version != 4:
                        raise ValueError('Use an IPv4 address')
                    kind = data.get('kind','other')
                    if kind not in ('switch','ap','firewall','router','server','nas','printer','camera','phone','other'):
                        raise ValueError('Invalid device type')
                    values = (short_text(data.get('name'),'device name',True),address,kind,
                              short_text(data.get('brand'),'brand'),short_text(data.get('model'),'model'),
                              short_text(data.get('group_name') or 'Network','group',True))
                    if self.path == '/api/devices':
                        conn.execute('INSERT INTO devices(name,ip,kind,brand,model,group_name) VALUES(?,?,?,?,?,?)',values)
                    else:
                        cursor = conn.execute('UPDATE devices SET name=?,ip=?,kind=?,brand=?,model=?,group_name=? WHERE id=?',values+(int(data['id']),))
                        if not cursor.rowcount:
                            raise ValueError('Device not found')
                    reload_engine = True
                elif self.path == '/api/services':
                    conn.execute('INSERT INTO services(device_id,name,kind,port,path,oid,warning,critical,timeout,interval,max_attempts) VALUES(?,?,?,?,?,?,?,?,?,?,?)',service_values(data))
                    reload_engine = True
                elif self.path == '/api/delete-service':
                    conn.execute('DELETE FROM services WHERE id=?',(int(data['id']),))
                    reload_engine = True
                elif self.path == '/api/snmp':
                    identifier = int(data['device_id'])
                    if not conn.execute('SELECT id FROM devices WHERE id=?',(identifier,)).fetchone():
                        raise ValueError('Host not found')
                    version = data.get('version')
                    if version not in ('2c','3'):
                        raise ValueError('Choose SNMP v2c or v3')
                    credentials = {'version':version}
                    names = ('community',) if version == '2c' else ('username','auth_password','priv_password')
                    for name in names:
                        credentials[name] = short_text(data.get(name), name,True,256)
                    if version == '3' and (len(credentials['auth_password'])<8 or len(credentials['priv_password'])<8):
                        raise ValueError('SNMPv3 passphrases require at least 8 characters')
                    conn.execute('UPDATE devices SET snmp_secret=?,snmp_version=? WHERE id=?',
                                 (encrypt_credentials(DB,credentials),version,identifier))
                elif self.path == '/api/acknowledge':
                    table = 'services' if data.get('target') == 'service' else 'devices'
                    identifier = int(data['id'])
                    row = conn.execute(f'SELECT * FROM {table} WHERE id=?',(identifier,)).fetchone()
                    if not row or row['state'] in ('OK','PENDING'):
                        raise ValueError('No active problem to acknowledge')
                    note = short_text(data.get('note'), 'acknowledgement note',True,300)
                    conn.execute(f'UPDATE {table} SET acknowledged=1,ack_note=? WHERE id=?',(note,identifier))
                elif self.path == '/api/maintenance':
                    minutes = int(data.get('minutes',60))
                    if not 0<=minutes<=10080:
                        raise ValueError('Maintenance must be 0 to 10080 minutes')
                    cursor = conn.execute('UPDATE devices SET maintenance_until=? WHERE id=?',(time.time()+minutes*60 if minutes else 0,int(data['id'])))
                    if not cursor.rowcount:
                        raise ValueError('Host not found')
                elif self.path == '/api/links':
                    conn.execute('INSERT INTO links(source,target,label) VALUES(?,?,?)',
                                 (int(data['source']),int(data['target']),short_text(data.get('label'),'link label')))
                elif self.path == '/api/delete-device':
                    conn.execute('DELETE FROM devices WHERE id=?',(int(data['id']),))
                    reload_engine = True
                elif self.path == '/api/delete-link':
                    conn.execute('DELETE FROM links WHERE id=?',(int(data['id']),))
                else:
                    self.respond(404, {'error':'Not found'})
                    return
            if self.path == '/api/discovery':
                global DISCOVERY_GENERATION
                with DISCOVERY_LOCK:
                    DISCOVERY_GENERATION += 1
                DISCOVERY_WAKE.set()
            if reload_engine:
                engine.WAKE.set()
            self.respond(201, response)
        except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as exc:
            self.respond(400, {'error':str(exc)})
        except sqlite3.Error:
            self.respond(503, {'error':'Database operation failed; try again'})


DUMMY_HASH = password_hash(secrets.token_urlsafe(32))

if __name__ == '__main__':
    import argparse
    import getpass
    parser = argparse.ArgumentParser(description='Network Monitor powered by Nagios Core')
    parser.add_argument('command',nargs='?',choices=['serve','create-admin'],default='serve')
    parser.add_argument('--username',default='admin')
    args = parser.parse_args()
    initialize()
    if args.command == 'create-admin':
        password = getpass.getpass('New admin password (at least 12 characters): ')
        if password != getpass.getpass('Repeat password: '):
            parser.error('Passwords do not match')
        create_admin(args.username,password)
        print('Admin account saved. Run app.py to start the server.')
    else:
        with connect() as conn:
            if not conn.execute('SELECT id FROM users LIMIT 1').fetchone():
                parser.error('Create an account first: python app.py create-admin')
        threading.Thread(target=engine.loop,args=(DB,),daemon=True).start()
        threading.Thread(target=discovery_loop,daemon=True).start()
        atexit.register(engine.stop)
        import signal
        def shutdown_signal(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, shutdown_signal)
        server = ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8080'))),Handler)
        print('Network Monitor listening on',server.server_address,flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            engine.stop()
