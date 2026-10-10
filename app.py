"""Small intranet monitor. Bind to loopback; use an authenticated reverse proxy remotely."""
import concurrent.futures
import ipaddress
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DB = Path(os.environ.get('NETWORK_DB', 'data/network.db'))
INTERVAL = max(10, int(os.environ.get('POLL_INTERVAL', '30')))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def initialize():
    with connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS discovery (id INTEGER PRIMARY KEY CHECK(id=1), subnet TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS devices (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, ip TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'unknown',
          latency REAL, checked_at REAL);
        CREATE TABLE IF NOT EXISTS links (
          id INTEGER PRIMARY KEY, source INTEGER REFERENCES devices(id) ON DELETE CASCADE,
          target INTEGER REFERENCES devices(id) ON DELETE CASCADE, label TEXT NOT NULL,
          UNIQUE(source,target), CHECK(source != target));
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
          status TEXT NOT NULL, created_at REAL NOT NULL);
        ''')


def probe(address):
    if not shutil.which('ping'):
        return 'unavailable', None
    result = subprocess.run(['ping', '-n', '-c', '1', '-W', '2', address],
                            capture_output=True, text=True, timeout=5)
    import re
    match = re.search(r'time[=<]([\d.]+)', result.stdout)
    return ('up' if result.returncode == 0 else 'down',
            float(match.group(1)) if match else None)


def poll_once():
    with connect() as conn:
        devices = conn.execute('SELECT * FROM devices').fetchall()
    for device in devices:
        try:
            status, latency = probe(device['ip'])
        except (OSError, subprocess.TimeoutExpired):
            status, latency = 'unavailable', None
        with connect() as conn:
            current = conn.execute('SELECT status FROM devices WHERE id=?', (device['id'],)).fetchone()
            if current is None:
                continue
            now = time.time()
            if current['status'] != status:
                conn.execute('INSERT INTO events(device_id,status,created_at) VALUES(?,?,?)',
                             (device['id'], status, now))
            conn.execute('UPDATE devices SET status=?,latency=?,checked_at=? WHERE id=?',
                         (status, latency, now, device['id']))
            conn.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 10000)')


def discover(subnet):
    network = ipaddress.ip_network(subnet, strict=False)
    if network.version != 4 or network.num_addresses > 256:
        raise ValueError('Use an IPv4 subnet with at most 256 addresses (/24 or smaller)')
    def inspect(address):
        try:
            return str(address), probe(str(address))[0]
        except (OSError, subprocess.TimeoutExpired):
            return str(address), 'unavailable'
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for address, status in pool.map(inspect, network.hosts()):
            if status == 'up':
                with connect() as conn:
                    conn.execute("INSERT OR IGNORE INTO devices(name,ip,kind) VALUES(?,?,'other')",
                                 ('Discovered ' + address, address))


def monitor():
    last_discovery = 0
    while True:
        try:
            with connect() as conn:
                config = conn.execute('SELECT subnet FROM discovery WHERE id=1').fetchone()
            if config and time.time() - last_discovery >= 300:
                discover(config['subnet'])
                last_discovery = time.time()
            poll_once()
        except sqlite3.Error:
            import logging
            logging.exception('Monitoring cycle failed')
        time.sleep(INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, body, content_type='application/json'):
        data = json.dumps(body).encode() if content_type == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/api/state':
            with connect() as conn:
                devices = [dict(r) for r in conn.execute('SELECT * FROM devices ORDER BY name')]
                links = [dict(r) for r in conn.execute('SELECT * FROM links')]
                config = conn.execute('SELECT subnet FROM discovery WHERE id=1').fetchone()
                events = [dict(r) for r in conn.execute('SELECT e.*,d.name FROM events e JOIN devices d ON d.id=e.device_id ORDER BY e.id DESC LIMIT 100')]
            self.respond(200, dict(devices=devices, links=links, events=events,
                                   discovery_subnet=config['subnet'] if config else None, ping_available=bool(shutil.which('ping')), interval=INTERVAL))
        elif self.path in ('/', '/app.js', '/style.css'):
            name = 'index.html' if self.path == '/' else self.path[1:]
            mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name]
            self.respond(200, (Path(__file__).parent / 'web' / name).read_bytes(), mime)
        else:
            self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        # Reject browser requests originating from another website.
        if self.headers.get('Origin') and self.headers['Origin'] != 'http://' + self.headers.get('Host', ''):
            self.respond(403, {'error': 'Origin rejected'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
            with connect() as conn:
                if self.path == '/api/discovery':
                    subnet = str(data.get('subnet', '')).strip()
                    if subnet:
                        network = ipaddress.ip_network(subnet, strict=False)
                        if network.version != 4 or network.num_addresses > 256:
                            raise ValueError('Use an IPv4 /24 or smaller subnet')
                        if not shutil.which('ping'):
                            raise ValueError('Install iputils-ping before enabling discovery')
                        conn.execute('INSERT OR REPLACE INTO discovery(id,subnet) VALUES(1,?)', (str(network),))
                    else:
                        conn.execute('DELETE FROM discovery')
                elif self.path == '/api/devices':
                    address = str(ipaddress.ip_address(data.get('ip', '')))
                    if ipaddress.ip_address(address).version != 4:
                        raise ValueError('This version supports IPv4 addresses')
                    name = str(data.get('name', '')).strip()
                    kind = data.get('kind', 'other')
                    if not name or len(name) > 80 or kind not in ('switch','ap','firewall','router','server','other'):
                        raise ValueError('Provide a device name and valid type')
                    brand = str(data.get('brand', '')).strip()
                    model = str(data.get('model', '')).strip()
                    if len(brand) > 80 or len(model) > 80:
                        raise ValueError('Brand and model must be at most 80 characters')
                    conn.execute('INSERT INTO devices(name,ip,kind,brand,model) VALUES(?,?,?,?,?)', (name,address,kind,brand,model))
                elif self.path == '/api/links':
                    label = str(data.get('label', '')).strip()
                    if len(label) > 80:
                        raise ValueError('Link label is too long')
                    conn.execute('INSERT INTO links(source,target,label) VALUES(?,?,?)',
                                 (int(data['source']),int(data['target']),label))
                elif self.path == '/api/delete-device':
                    conn.execute('DELETE FROM devices WHERE id=?', (int(data['id']),))
                elif self.path == '/api/delete-link':
                    conn.execute('DELETE FROM links WHERE id=?', (int(data['id']),))
                else:
                    self.respond(404, {'error':'Not found'})
                    return
            self.respond(201, {'ok': True})
        except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
            self.respond(400, {'error': str(exc)})


if __name__ == '__main__':
    initialize()
    threading.Thread(target=monitor, daemon=True).start()
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8080'))), Handler)
    print('Network monitor listening on', server.server_address, flush=True)
    server.serve_forever()
