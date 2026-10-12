import importlib.util
from pathlib import Path
import tempfile
import unittest
import app

spec = importlib.util.spec_from_file_location('clear_inventory', Path(__file__).resolve().parents[1] / 'scripts/clear_inventory.py')
clear = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clear)


class ClearInventory(unittest.TestCase):
    def test_clears_all_devices_and_discovery_while_preserving_login(self):
        old = app.DB
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.DB = Path(folder) / 'network.db'
                app.initialize()
                app.create_admin('admin', 'Test-only-password-123')
                with app.connect() as conn:
                    host = conn.execute("INSERT INTO devices(name,ip,kind) VALUES('Old host','192.168.1.1','switch')").lastrowid
                    peer = conn.execute("INSERT INTO devices(name,ip,kind) VALUES('Second','192.168.1.2','ap')").lastrowid
                    conn.execute("INSERT INTO services(device_id,name,kind,port) VALUES(?,'HTTP','http',80)", (host,))
                    conn.execute("INSERT INTO links(source,target,label) VALUES(?,?,'uplink')", (host, peer))
                    conn.execute("INSERT INTO events(device_id,status,created_at) VALUES(?,'CRITICAL',100)", (host,))
                    conn.execute("INSERT INTO discovery(id,subnet) VALUES(1,'192.168.1.0/24')")
                    conn.execute("INSERT INTO discovery_results(ip,subnet,last_seen) VALUES('192.168.1.3','192.168.1.0/24',100)")
                    conn.execute('INSERT INTO device_profiles(device_id,payload) VALUES(?,?)', (host, '{}'))
                counts = clear.clear_inventory()
                self.assertEqual(counts['devices'], 2)
                self.assertEqual(counts['services'], 1)
                with app.connect() as conn:
                    for table in ('devices', 'services', 'links', 'events', 'device_profiles',
                                  'discovery', 'discovery_results'):
                        self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
                    self.assertEqual(conn.execute('SELECT username FROM users').fetchone()[0], 'admin')
                self.assertEqual(clear.clear_inventory()['devices'], 0)
        finally:
            app.DB = old

    def test_does_not_create_missing_database(self):
        old = app.DB
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.DB = Path(folder) / 'missing.db'
                with self.assertRaisesRegex(ValueError, 'database not found'):
                    clear.clear_inventory()
                self.assertFalse(app.DB.exists())
        finally:
            app.DB = old
