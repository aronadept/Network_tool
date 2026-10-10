"""Run an unmodified Nagios Core process and exchange config/status/commands."""
import os
import pwd
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

WAKE = threading.Event()
LOCK = threading.Lock()
PROCESS = None
HEALTH = {'status': 'starting', 'message': 'Waiting for Nagios Core', 'updated_at': None}


def binary():
    requested = os.environ.get('NAGIOS_BIN')
    if requested:
        return requested
    installed = shutil.which('nagios4') or shutil.which('nagios')
    if installed:
        return installed
    local = Path(__file__).parent / '.tools/nagios/bin/nagios'
    for candidate in (local, Path('/usr/sbin/nagios4')):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def directory(db):
    return db.resolve().parent / 'nagios'


def objects(devices, services, db):
    from app import INTERVAL
    python = shlex.quote(sys.executable)
    plugin = shlex.quote(str(Path(__file__).with_name('plugin.py').resolve()))
    prefix = f'env NETWORK_DB={shlex.quote(str(db.resolve()))} {python} {plugin}'
    lines = [f'''define command {{
 command_name network-host
 command_line {prefix} host $ARG1$
}}
define command {{
 command_name network-service
 command_line {prefix} service $ARG1$
}}
define command {{
 command_name notification-disabled
 command_line /bin/true
}}
define contact {{
 contact_name dashboard
 alias Dashboard events only
 host_notifications_enabled 0
 service_notifications_enabled 0
 host_notification_period 24x7
 service_notification_period 24x7
 host_notification_options n
 service_notification_options n
 host_notification_commands notification-disabled
 service_notification_commands notification-disabled
}}
define timeperiod {{
 timeperiod_name 24x7
 alias Every day
 sunday 00:00-24:00
 monday 00:00-24:00
 tuesday 00:00-24:00
 wednesday 00:00-24:00
 thursday 00:00-24:00
 friday 00:00-24:00
 saturday 00:00-24:00
}}
define host {{
 host_name monitor-local
 alias Monitoring engine
 address 127.0.0.1
 check_command network-host!0
 max_check_attempts 1
 active_checks_enabled 0
 notifications_enabled 0
 contacts dashboard
 notification_period 24x7
}}
# Older Core versions require at least one service even for host-only monitoring.
# This internal disabled object is excluded from the dashboard by its host name.
define service {{
 host_name monitor-local
 service_description engine-placeholder
 check_command notification-disabled
 max_check_attempts 1
 active_checks_enabled 0
 passive_checks_enabled 0
 check_period 24x7
 notifications_enabled 0
 contacts dashboard
 notification_period 24x7
}}''']
    for d in devices:
        lines.append(f'''define host {{
 host_name device-{int(d['id'])}
 alias device-{int(d['id'])}
 address {d['ip']}
 check_command network-host!{int(d['id'])}
 check_interval {INTERVAL/60:.6f}
 retry_interval 0.166667
 max_check_attempts 3
 check_period 24x7
 notifications_enabled 0
 contacts dashboard
 notification_period 24x7
}}''')
    for c in services:
        lines.append(f'''define service {{
 host_name device-{int(c['device_id'])}
 service_description check-{int(c['id'])}
 check_command network-service!{int(c['id'])}
 check_interval {c['interval']/60:.6f}
 retry_interval 0.166667
 max_check_attempts {int(c['max_attempts'])}
 check_period 24x7
 notifications_enabled 0
 contacts dashboard
 notification_period 24x7
}}''')
    return '\n'.join(lines) + '\n'


def prepare(db):
    import app
    root = directory(db)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ('spool', 'archives'):
        (root / name).mkdir(mode=0o700, exist_ok=True)
    with app.connect() as conn:
        hosts = conn.execute('SELECT * FROM devices').fetchall()
        services = conn.execute('SELECT * FROM services').fetchall()
    object_file = root / 'objects.cfg'
    temporary = root / 'objects.pending'
    temporary.write_text(objects(hosts, services, db))
    username = pwd.getpwuid(os.getuid()).pw_name
    group = __import__('grp').getgrgid(os.getgid()).gr_name
    config = f'''log_file={root}/nagios.log
cfg_file={temporary}
object_cache_file={root}/objects.cache
status_file={root}/status.dat
status_update_interval=5
nagios_user={username}
nagios_group={group}
check_external_commands=1
command_file={root}/nagios.cmd
query_socket={root}/nagios.qh
lock_file={root}/nagios.pid
temp_file={root}/nagios.tmp
temp_path={root}
check_result_path={root}/spool
log_archive_path={root}/archives
state_retention_file={root}/retention.dat
retain_state_information=1
retention_update_interval=1
use_retained_program_state=0
use_retained_scheduling_info=0
execute_service_checks=1
execute_host_checks=1
accept_passive_service_checks=0
accept_passive_host_checks=0
enable_notifications=0
check_for_updates=0
check_service_freshness=0
check_host_freshness=0
service_check_timeout=15
host_check_timeout=15
interval_length=60
service_inter_check_delay_method=n
host_inter_check_delay_method=n
max_concurrent_checks=8
log_rotation_method=d
use_syslog=0
log_initial_states=1
log_service_retries=1
log_host_retries=1
'''
    pending = root / 'nagios.pending.cfg'
    pending.write_text(config)
    result = subprocess.run([binary(), '-v', str(pending)], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Nagios config validation failed: ' + (result.stdout + result.stderr)[-1400:])
    temporary.replace(object_file)
    final = root / 'nagios.cfg'
    stage = root / 'nagios.cfg.new'
    stage.write_text(config.replace(f'cfg_file={temporary}', f'cfg_file={object_file}'))
    stage.replace(final)
    return final


def status_blocks(text):
    current = None
    data = {}
    for line in text.splitlines():
        line = line.strip()
        if line in ('hoststatus {', 'servicestatus {', 'programstatus {'):
            current = line.split()[0]
            data = {}
        elif line == '}' and current:
            yield current, data
            current = None
        elif current and '=' in line:
            key, value = line.split('=', 1)
            data[key] = value


def sync(db):
    import app
    status = directory(db) / 'status.dat'
    if not status.exists():
        return
    updated = status.stat().st_mtime
    if time.time() - updated > 30:
        raise RuntimeError('Nagios results are stale; inspect data/nagios/nagios.log')
    for kind, data in status_blocks(status.read_text()):
        if not data.get('has_been_checked') == '1':
            continue
        match = re.fullmatch(r'device-(\d+)', data.get('host_name', ''))
        if not match:
            continue
        if kind == 'hoststatus':
            state = 'OK' if data.get('current_state') == '0' else 'CRITICAL'
            # Missing tools and plugin exceptions must not masquerade as an offline device.
            if data.get('plugin_output', '').startswith('UNKNOWN'):
                state = 'UNKNOWN'
            table, identifier = 'devices', int(match.group(1))
        elif kind == 'servicestatus':
            check = re.fullmatch(r'check-(\d+)', data.get('service_description', ''))
            if not check:
                continue
            table, identifier = 'services', int(check.group(1))
            state = {'0':'OK','1':'WARNING','2':'CRITICAL','3':'UNKNOWN'}.get(data.get('current_state'), 'UNKNOWN')
        else:
            continue
        app.record_result(table, identifier, state, data.get('plugin_output', '')[:500],
                          int(data.get('last_check', '0')), int(data.get('current_attempt','1')),
                          'HARD' if data.get('state_type') == '1' else 'SOFT')
    with LOCK:
        HEALTH.update(status='running', message='Nagios Core is monitoring hosts and services', updated_at=updated)


def loop(db):
    global PROCESS
    if not binary():
        with LOCK:
            HEALTH.update(status='error', message='Nagios Core is missing. Install nagios4-core or set NAGIOS_BIN.')
        return
    if os.getuid() == 0:
        with LOCK:
            HEALTH.update(status='error', message='Run the app as a normal user; Nagios Core must not run as root.')
        return
    root = directory(db)
    output = None
    try:
        while True:
            try:
                if PROCESS is None or WAKE.is_set():
                    WAKE.clear()
                    config = prepare(db)
                    if PROCESS is None:
                        output = (root / 'engine-output.log').open('a')
                        PROCESS = subprocess.Popen([binary(), str(config)], stdout=output, stderr=output)
                    else:
                        PROCESS.send_signal(signal.SIGHUP)
                if PROCESS.poll() is not None:
                    raise RuntimeError('Nagios Core stopped; inspect data/nagios/engine-output.log')
                sync(db)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                with LOCK:
                    HEALTH.update(status='error', message=str(exc))
            time.sleep(2)
    finally:
        if PROCESS and PROCESS.poll() is None:
            PROCESS.terminate()
        if output:
            output.close()


def stop():
    if PROCESS and PROCESS.poll() is None:
        PROCESS.terminate()
        try:
            PROCESS.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROCESS.kill()
            PROCESS.wait()
