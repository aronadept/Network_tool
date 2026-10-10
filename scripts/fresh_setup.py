"""Create a clean default database; keep the former data in a private archive."""
import argparse
import getpass
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app


def reset_data(root):
    root=Path(root).resolve()
    data=root/'data'
    if data.is_symlink():
        raise ValueError('Refusing to reset a symlinked data directory')
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name)==os.getpid():
            continue
        try:
            args=(process/'cmdline').read_bytes().decode(errors='replace').split('\0')
            if 'app.py' in args or str(root/'app.py') in args:
                if (process/'cwd').resolve()==root:
                    raise ValueError('Stop the running app first (Ctrl+C or stop its service)')
        except (PermissionError,FileNotFoundError,ProcessLookupError):
            continue
    backup=None
    if data.exists():
        backup=Path(tempfile.mkdtemp(prefix='network-monitor-backup-',dir=root.parent))
        data.rename(backup/'data')
    data.mkdir(mode=0o700)
    return backup


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username',default='admin')
    args=parser.parse_args()
    if os.getuid()==0:
        parser.error('Run as the application user (for example aron), not root')
    if os.environ.get('NETWORK_DB'):
        parser.error('Unset NETWORK_DB first; fresh setup resets the default data directory only')
    password=getpass.getpass('New admin password (at least 12 characters): ')
    from security import password_hash
    password_hash(password)  # Validate before moving any data.
    if password!=getpass.getpass('Repeat password: '):
        parser.error('Passwords do not match')
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}',args.username):
        parser.error('Invalid username')
    try:
        backup=reset_data(ROOT)
    except ValueError as exc:
        parser.error(str(exc))
    if backup:
        print('Previous data archived privately at:',backup)
    app.DB=ROOT/'data/network.db'
    app.initialize()
    app.create_admin(args.username,password)
    print('Fresh setup complete: no hosts, services, links, history, discovery settings or old accounts.')
    print('Start: HOST=0.0.0.0 PORT=8080 .venv/bin/python app.py')


if __name__=='__main__':main()
