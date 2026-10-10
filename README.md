# Network Monitor — powered by Nagios Core

Ubuntu intranet monitoring console for mixed-brand network devices. Nagios Core handles scheduling, retries and SOFT/HARD states; this Python app provides login, inventory/discovery, service configuration, problems and topology. It runs a dedicated Core process using configuration under `data/nagios/`, without changing an existing system Nagios configuration.

## Upgrade and run in your Ubuntu container

Stop the previous app with Ctrl+C first. Keep a backup of existing data before upgrading. Run these as root in the container:

```sh
apt-get update
apt-get install -y python3 python3-venv iputils-ping nagios4-core
cd /home/aron/Network_tool
# With the old app stopped, keep a private copy outside the checkout:
if [ -d data ]; then cp -a data /home/aron/network-monitor-data-backup-$(date +%Y%m%d-%H%M%S); fi
git pull --ff-only origin feature/network-monitor-mvp
chown -R aron:aron /home/aron/Network_tool
su - aron
```

Then, as **aron** (not root):

```sh
cd /home/aron/Network_tool
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py create-admin --username admin
HOST=0.0.0.0 PORT=8080 .venv/bin/python app.py
```

The account command asks for a password twice (12–256 characters). There is no default password. Use the same command to reset it; existing sessions for that account are invalidated. Open your container's IPv4 address on port 8080 and sign in. Watch the engine banner: it must say RUNNING. The UI can run when Core is missing, but checks will remain pending and an engine error is displayed. Core is intentionally refused under root. For installations under other usernames/paths, adapt the commands. The app migrates the prior SQLite schema while preserving inventory, links and event history.

For normal shared use, serve behind an HTTPS reverse proxy; set `PUBLIC_ORIGIN=https://your-monitor-host` and `COOKIE_SECURE=1`, preserve the original Host, and keep the app listener on loopback. HTTP is suitable only for temporary tests on a trusted network; passwords and sessions otherwise traverse the LAN without TLS. No external CDN is used.

### If the Core package is unavailable

Install `git gcc make libssl-dev` using your OS package manager, then run `sh scripts/build-nagios.sh` as your normal user. It builds unmodified Core 4.5.14 from a pinned upstream Git revision with two compiler jobs and keeps its license/source. The app detects `.tools/nagios/bin/nagios`. Python dependencies still need the venv installation above. `NAGIOS_BIN` can select another installed binary. Generated configuration was validated against Core 4.5.14; verify older distro packages on your server.

## Workflow

- **Dashboard**: host state, service counts, unhandled HARD problems, host inventory and recent transitions. Group filter and Edit action allow names, brands, models and groups to be corrected after discovery.
- **Services**: each host has an automatic ping check. Add TCP, HTTP/HTTPS or SNMP checks with an interval, timeout, retry count and optional numeric warning/critical thresholds. TCP/HTTP values use response milliseconds; SNMP uses the raw OID value. HTTP accepts 200–399 and does not follow redirects. HTTPS certificate verification is enforced, including the certificate's IP identity.
- **SNMP**: save per-host v2c community or v3 SHA-256/AES-128 authPriv credentials, then add an uptime/system-name template or custom numeric OID. SNMP v2c communities travel unencrypted; prefer v3 where supported. SNMPv3 devices using other algorithms are not supported yet. CPU/memory/interface OIDs vary by model; the app does not claim to infer them. Encryption protects stored credentials; the key and SQLite file are local files accessible to the app user. Never commit or share them.
- **Problems**: OK, WARNING, CRITICAL, UNKNOWN and PENDING states with SOFT/HARD retry state. Acknowledge with a note. Maintenance suppresses the host and its services from the dashboard active-problem list while checks continue. **Acknowledgement and maintenance are dashboard-local in this release**; they are not exported as Core external commands. Native outbound Core notifications are disabled. State transitions are recorded as in-app events; email/Slack notifications are not implemented.
- **Discovery**: an authorized IPv4 `/16` through `/32` subnet, up to 32 concurrent ICMP probes, repeating five minutes after completion. Newly responding addresses are added and Core configuration reloads automatically. Scanning a sparse /16 can take over an hour. The separate worker keeps discovery independent from Core monitoring. Blank the subnet to cancel after in-flight probes finish. Ping-blocked devices need manual/bulk addition.
- **Topology**: saved connections in a layered map; zoom, Fit map, pan and device details. It does not automatically discover LLDP/CDP connections yet. Unconnected devices appear in a separate grid.

Core checks retry every 10 seconds. First check results can take a few seconds after adding objects. Editing inventory/services reloads the generated, validated Core configuration. Plugin commands receive only numeric database IDs; credentials are read/decrypted locally, never put in Core command lines or returned to the browser. Do not edit generated files in `data/nagios/`; update objects through the app.

## Persistence and settings

`NETWORK_DB` defaults to `data/network.db`; `HOST` defaults to `127.0.0.1`, `PORT` to 8080, `POLL_INTERVAL` (host checks) to 30 seconds, and `DISCOVERY_WORKERS` to 32 (1–64). All paths should be local trusted filesystem paths. Keep the data directory private to the app user. Back up **both SQLite and `data/.credential-key`**: restoring the DB alone cannot restore encrypted SNMP secrets. Use SQLite's backup API for a running database; do not copy just the DB file while WAL transactions are active. For a stopped app, archive the whole `data/` directory, including hidden files. Event history retains the latest 10,000 state transitions globally. Sessions expire after 12 hours.

For automatic startup on an Ubuntu container with systemd, adapt `deploy/network-monitor.service`, create your account first, then as root:

```sh
cp deploy/network-monitor.service /etc/systemd/system/network-monitor.service
systemctl daemon-reload
systemctl enable --now network-monitor
systemctl status network-monitor
```

The example uses user aron and `/home/aron/Network_tool`. Containers without systemd should use their supervisor or run the foreground command. Only the app's own Core process is managed; existing Nagios installations are untouched.

## Validation and current limits

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The real Core integration test runs when a Nagios binary is found; otherwise it is reported as skipped. Tests include real local HTTP/TCP checks and SNMP v2c/v3 authPriv UDP fixtures. Cloud validation does not prove private-intranet reachability, physical-device SNMPv3 compatibility, or Ubuntu distro packaging. This release has admin login (all users are administrators), service configuration, Core check states, encrypted SNMP secrets, dashboard-local problem handling, discovery and manual topology. Automatic LLDP/CDP, interface-rate graphs, notification delivery, RBAC and discovery approval workflows remain future work. It is not a full Nagios XI replacement.

See THIRD_PARTY.md for the Core source/license relationship.
