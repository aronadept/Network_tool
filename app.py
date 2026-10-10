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
DISCOVERY_WORKERS = max(1, min(64, int(os.environ.get('DISCOVERY_WORKERS', '32'))))
DISCOVERY_WAKE = threading.Event()
DISCOVERY_LOCK = threading.Lock()
DISCOVERY_GENERATION = 0
DISCOVERY_PROGRESS = {'status': 'idle', 'checked': 0, 'total': 0, 'found': 0, 'subnet': None}

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
            return str(address), probe(str(address))[0]
        except (OSError, subprocess.TimeoutExpired):
            return str(address), 'unavailable'
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
                address, status = future.result()
                if cancelled():
                    break
                if status == 'up':
                    with connect() as conn:
                        conn.execute("INSERT OR IGNORE INTO devices(name,ip,kind) VALUES(?,?,'other')",
                                     ('Discovered ' + address, address))
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


def monitor():
    while True:
        try:
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
            with DISCOVERY_LOCK:
                progress = dict(DISCOVERY_PROGRESS)
            self.respond(200, dict(discovery_progress=progress, devices=devices, links=links, events=events,
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
                        network = discovery_network(subnet)
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
            if self.path == '/api/discovery':
                global DISCOVERY_GENERATION
                with DISCOVERY_LOCK:
                    DISCOVERY_GENERATION += 1
                DISCOVERY_WAKE.set()
            self.respond(201, {'ok': True})
        except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
            self.respond(400, {'error': str(exc)})


if __name__ == '__main__':
    initialize()
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=discovery_loop, daemon=True).start()
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8080'))), Handler)
    print('Network monitor listening on', server.server_address, flush=True)
    server.serve_forever()
