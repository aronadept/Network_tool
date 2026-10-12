"""Remove every monitored device while retaining accounts and application setup."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def clear_inventory():
    if not app.DB.is_file():
        raise ValueError(f'App database not found: {app.DB.resolve()}')
    with app.connect() as conn:
        tables = {row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {'devices', 'services', 'links', 'events', 'discovery', 'discovery_results', 'users'}
        if not expected.issubset(tables):
            raise ValueError('This is not a complete Network Monitor database')
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('devices', 'services', 'links', 'events', 'discovery_results')}
        # Clearing the discovery subnet also stops new scan cycles after restart.
        conn.execute('DELETE FROM discovery')
        conn.execute('DELETE FROM discovery_results')
        conn.execute('DELETE FROM devices')  # FK cascade removes checks, links, events and profiles.
        for table in ('devices', 'services', 'links', 'events', 'discovery_results', 'device_profiles'):
            if table in tables and conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                raise RuntimeError(f'Failed to clear {table}; transaction rolled back')
    return counts


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run as the application user (for example aron), not root')
    counts = clear_inventory()
    print(f"Cleared {counts['devices']} devices, {counts['services']} services, "
          f"{counts['links']} links, {counts['events']} events and "
          f"{counts['discovery_results']} discovery results.")
    print('Discovery disabled. Login accounts and sessions remain available.')
    print('Start the app service to load the empty inventory into Nagios Core.')


if __name__ == '__main__':
    main()
