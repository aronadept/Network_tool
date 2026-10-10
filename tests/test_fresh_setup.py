import importlib.util
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
import app

spec=importlib.util.spec_from_file_location('fresh_setup',Path(__file__).resolve().parents[1]/'scripts/fresh_setup.py')
fresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(fresh)

class FreshSetup(unittest.TestCase):
    def test_fresh_database_is_empty_and_previous_data_is_archived(self):
        old=app.DB
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project';root.mkdir()
            app.DB=root/'data/network.db';app.initialize()
            app.create_admin('oldadmin','Fixture-password-123')
            with app.connect() as c:c.execute("INSERT INTO devices(name,ip,kind) VALUES('Previous router','127.0.0.1','router')")
            backup=fresh.reset_data(root)
            self.assertTrue((backup/'data/network.db').is_file())
            self.assertEqual(backup.stat().st_mode & 0o777,0o700)
            app.initialize()
            with app.connect() as c:
                for table in ('devices','services','links','events','users','sessions','discovery','discovery_results'):
                    self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
            app.DB=backup/'data/network.db'
            with app.connect() as c:self.assertEqual(c.execute('SELECT name FROM devices').fetchone()[0],'Previous router')
            app.DB=old
    def test_discard_removes_previous_database_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project';root.mkdir()
            data=root/'data';data.mkdir()
            (data/'network.db').write_bytes(b'old database')
            (data/'.credential-key').write_bytes(b'old key')
            (data/'nagios').mkdir()
            (data/'nagios/status.dat').write_text('old states')
            self.assertIsNone(fresh.reset_data(root, discard_old_data=True))
            self.assertEqual(list(data.iterdir()),[])
            self.assertEqual(list(Path(folder).iterdir()),[root])

    def test_running_app_prevents_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project';root.mkdir()
            (root/'data').mkdir()
            process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)','app.py'],cwd=root)
            try:
                with self.assertRaisesRegex(ValueError,'Stop the running app'):
                    fresh.reset_data(root)
                self.assertTrue((root/'data').is_dir())
            finally:
                process.terminate();process.wait(timeout=5)

    def test_symlink_data_is_not_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project';root.mkdir()
            target=Path(folder)/'external';target.mkdir()
            (root/'data').symlink_to(target)
            with self.assertRaises(ValueError):fresh.reset_data(root)
            self.assertTrue(target.exists())
