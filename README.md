# HL Agro IT Tool — powered by Nagios Core

**HL Agro Products Pvt Ltd.** · Designed by **Deepanshu Sharma**. Uses the supplied HL Agro vector logo on the login page, navigation and browser icon.

Ubuntu intranet monitoring console for mixed-brand network devices. Nagios Core handles scheduling, retries and SOFT/HARD states; this Python app provides login, inventory/discovery, service configuration, problems and topology. It runs a dedicated Core process using configuration under `data/nagios/`, without changing an existing system Nagios configuration.

## Install on another Ubuntu server or container

These steps create a fresh installation with no previous devices, services or credentials. Use the `feature/network-monitor-mvp` branch for the current app. The examples use a dedicated `hl-agro` application user; Nagios Core must run as this user rather than root. For an existing installation under `aron`, use the upgrade section below instead.

### 1. Install prerequisites and create the application user

Run from a root terminal on the new Ubuntu machine:

```sh
apt-get update
apt-get install -y python3 python3-venv iputils-ping git nagios4-core
adduser --disabled-password --gecos "" hl-agro
su - hl-agro
```

If `hl-agro` already exists, skip `adduser`. If `nagios4-core` is unavailable, install the other prerequisites and follow [If the Core package is unavailable](#if-the-core-package-is-unavailable) after cloning.

### 2. Download the app, install dependencies and create an admin

Run as `hl-agro`:

```sh
git clone --branch feature/network-monitor-mvp --single-branch https://github.com/aronadept/Network_tool.git
cd /home/hl-agro/Network_tool
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py create-admin --username admin
HOST=0.0.0.0 PORT=8080 .venv/bin/python app.py
```

Enter a new admin password twice when prompted. Open `http://SERVER-IP:8080` using the new machine's LAN address. Sign in and confirm **Monitoring Engine → Status: RUNNING**. The dashboard should have no hosts initially; add your devices and SNMP credentials through the console. A private GitHub repository requires an account with repository access; do not place tokens in clone URLs. If the clone directory already exists, inspect that installation before continuing rather than cloning over it.

The server needs network access to the monitored devices: ICMP for ping/discovery, UDP 161 for SNMP, and the ports used by configured TCP/HTTP checks. Browsers need access to TCP 8080 for the direct test listener. Keep the existing network/firewall policy and allow the required traffic for your intranet as appropriate. For shared deployment, use the HTTPS reverse-proxy settings described below.

### 3. Enable automatic startup with systemd

Stop the foreground app with Ctrl+C, then run `exit` to return to the root terminal. Install the supplied template for this new-machine path:

```sh
cp /home/hl-agro/Network_tool/deploy/network-monitor-new-install.service /etc/systemd/system/network-monitor.service
systemctl daemon-reload
systemctl enable --now network-monitor
systemctl status network-monitor --no-pager
```

Expect `active (running)` from systemd and `RUNNING` in the web console's Monitoring Engine page. The template uses `/home/hl-agro/Network_tool`, user/group `hl-agro`, port 8080 and private file permissions. If your username, directory or port differs, edit the installed unit before starting it. Containers without systemd need a container supervisor or the foreground command instead; `systemctl` will not work there.

### Logs, restart and later updates

As root:

```sh
journalctl -u network-monitor -n 50 --no-pager
systemctl restart network-monitor
systemctl stop network-monitor
systemctl start network-monitor
```

For subsequent code updates, stop the service as root, update as the application user, then start it again:

```sh
systemctl stop network-monitor
su - hl-agro
cd /home/hl-agro/Network_tool
git pull --ff-only origin feature/network-monitor-mvp
.venv/bin/python -m pip install -r requirements.txt
exit
systemctl start network-monitor
systemctl status network-monitor --no-pager
```

Refresh the browser with Ctrl+Shift+R after updates. These update commands preserve app data. Do not run `fresh_setup.py --discard-old-data` for a normal upgrade. To migrate an existing installation's inventory and credentials, back up and restore the whole stopped `data/` directory, including `.credential-key`, with ownership assigned to the new application user; see [Persistence and settings](#persistence-and-settings).

## Fresh Nagios-inspired console

The interface is rebuilt with a compact Nagios-inspired operations layout: grouped left navigation, an always-visible host/service status strip, clickable dashboard totals, dedicated Host Status and Service Status tables, host groups, problems, maintenance, event history, network topology, configuration and engine diagnostics. Host and service names open individual status/configuration/history pages. Tables support filtering and sorting. Automatic refresh can be paused; manual refresh remains available. All pages adapt to smaller screens. No CDN, example hosts or simulated monitoring values are included.

For the requested **completely fresh installation**, stop the old app (Ctrl+C, or stop its systemd service). If your terminal is root, switch to the application user with `su - aron`. Then run:

```sh
cd /home/aron/Network_tool
git pull --ff-only origin feature/network-monitor-mvp
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/fresh_setup.py --discard-old-data --username admin
HOST=0.0.0.0 PORT=8080 .venv/bin/python app.py
```

Enter a new admin password twice when asked. `--discard-old-data` permanently removes the previous default `data/` directory, including inventory, services, connections, events, discovery results/settings, accounts, sessions, SNMP credentials, encryption key and generated Nagios configuration/state/logs. It creates an empty database and the new admin account; no prior data is loaded or archived. Browser refresh: Ctrl+Shift+R. Passwords are validated before removing data. The command refuses a symlinked data directory or a detected running app/Core process. Run with `NETWORK_DB` unset; custom database locations are not reset. Older external backups and system-wide Nagios installations are separate from this app and are not changed.

Without `--discard-old-data`, the same setup tool creates an empty installation but keeps a private archive outside the checkout. For a new Ubuntu server without a venv or Nagios package, follow the prerequisites below first, then use the fresh setup command instead of `create-admin`.

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

Install `git gcc make libssl-dev` using your OS package manager, then run `sh scripts/build-nagios.sh` as your normal user. It builds unmodified Core 4.5.14 from a pinned upstream Git revision with two compiler jobs and keeps its license/source. The app detects `.tools/nagios/bin/nagios`. Python dependencies still need the venv installation above. `NAGIOS_BIN` can select another installed binary. Generated configuration and a real service check were validated against Core 4.4.6 and 4.5.14. A disabled internal placeholder service supports host-only/empty inventory on older Core versions; it is excluded from dashboard service counts.

## Workflow

- **Dashboard / Host Status / Host Groups**: live health totals and unhandled problems; separate searchable, sortable host table and group drilldown. Click a host to view check output, retries, latency, configuration, services, connections and its latest 100 events. Edit names, brands, models and groups after discovery.
- **Service Status / Configuration**: separate monitoring and setup screens, sortable/filterable service status, clickable service details and service-specific event history. Each host has an automatic ping check. Add TCP, HTTP/HTTPS or SNMP checks with an interval, timeout, retry count and optional numeric warning/critical thresholds. TCP/HTTP values use response milliseconds; SNMP uses the raw OID value. HTTP accepts 200–399 and does not follow redirects. HTTPS certificate verification is enforced, including the certificate's IP identity.
- **SNMP**: save per-host v2c community or v3 SHA-256/AES-128 authPriv credentials, then add an uptime/system-name template or custom numeric OID. SNMP v2c communities travel unencrypted; prefer v3 where supported. SNMPv3 devices using other algorithms are not supported yet. CPU/memory/interface OIDs vary by model; the app does not claim to infer them. Encryption protects stored credentials; the key and SQLite file are local files accessible to the app user. Never commit or share them.
- **Problems**: OK, WARNING, CRITICAL, UNKNOWN and PENDING states with SOFT/HARD retry state. Acknowledge with a note. Maintenance suppresses the host and its services from the dashboard active-problem list while checks continue. **Acknowledgement and maintenance are dashboard-local in this release**; they are not exported as Core external commands. Native outbound Core notifications are disabled. State transitions are recorded as in-app events; email/Slack notifications are not implemented.
- **Discovery**: an authorized IPv4 `/16` through `/32` subnet, up to 32 concurrent ICMP probes, repeating five minutes after completion. Responding addresses appear in a separate review list; scanning never adds inventory or Core objects. Select devices and click Add selected to enroll them, at which point Core configuration reloads. Existing IPs are marked Added and skipped. Scan results persist separately, with last-seen time and latency; the list shows results for the configured subnet. Results from previous cycles can remain listed with their last-seen timestamp, so appearance does not guarantee current reachability. Scanning a sparse /16 can take over an hour. The separate worker keeps discovery independent from Core monitoring. Blank the subnet to cancel after in-flight probes finish. Ping-blocked devices need manual/bulk addition.
- **Topology**: saved connections in a layered map; zoom, Fit map, pan and click a node for its full host detail page. It does not automatically discover LLDP/CDP connections yet. Unconnected devices appear in a separate grid.

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

The real Core integration test runs when a Nagios binary is found; otherwise it is reported as skipped. Tests include real local HTTP/TCP checks and SNMP v2c/v3 authPriv UDP fixtures. Cloud validation does not prove private-intranet reachability, physical-device SNMPv3 compatibility, or Ubuntu distro packaging. This release has admin login (all users are administrators), service configuration, Core check states, encrypted SNMP secrets, dashboard-local problem handling, discovery and manual topology. Automatic LLDP/CDP, interface-rate graphs, notification delivery, RBAC and advanced discovery classification remain future work. It is not a full Nagios XI replacement.

See THIRD_PARTY.md for the Core source/license relationship.

Click a device name in Host status to open its detail page: live host state, check attempts/output, latency, SNMP configuration status, service configuration and status, saved topology connections, and the latest 100 host/service events. Credentials are never returned. Details refresh every five seconds; only collected or configured information is displayed.

## Fetch device inventory with SNMP

Open a host detail page, choose **SNMP credentials** (the device is preselected), and save its v2c or v3 credentials. Return to the device and click **Fetch device details**. The server reads standard system hostname, description, object ID, uptime, location/contact and IF-MIB interface descriptions, operational states and reported speeds on UDP 161. Details are persisted as a timestamped snapshot separate from the manual inventory; names, brands, models, service checks and topology connections are not overwritten. Fetching does not create service checks.

Requests use saved encrypted credentials and are limited to 25 seconds and 64 interface entries. Unsupported fields are left unavailable with partial-result messages. Failed fetches keep the previous successful snapshot. Automatic LLDP links, model identification, CPU/RAM metrics and traffic-rate graphs are not implemented by this action. Interface speeds are reported IF-MIB values; very fast interfaces can exceed the legacy ifSpeed range. Only the selected host is queried, and credentials are never returned to the browser.

## Import devices from an older Nagios ZIP backup

Copy the ZIP to the machine running this app. The importer reads host definitions without extracting the archive or executing its plugins/scripts. It imports IPv4 addresses, names, a first hostgroup (when configured), and suggested types/brands based on host names. Repeated IPs across snapshots are deduplicated; existing inventory is never overwritten. Top-level `Nagios/server/` definitions are preferred over dated/older snapshots when names differ for one IP. The preview includes the union of custom hosts from all snapshots, which may include retired devices; review it before applying.

As the application user in the app directory, preview the backup:

```sh
cd /home/hl-agro/Network_tool
.venv/bin/python scripts/import_nagios.py /path/to/Nagios.zip
```

Then stop `network-monitor` as root (`systemctl stop network-monitor`). As the application user, add the reviewed hosts:

```sh
cd /home/hl-agro/Network_tool
.venv/bin/python scripts/import_nagios.py /path/to/Nagios.zip --apply
```

Start the service as root (`systemctl start network-monitor`) and check Host Status. Use `/home/aron/Network_tool` and user `aron` for the original container. A custom database requires the same `NETWORK_DB` setting used by the app. Keep any backup ZIP outside the repository; it may contain secrets. The importer itself does not print/import credentials.

By default, hosts under `objects/` and `localhost` are treated as bundled examples and excluded. If your real host definitions are under `objects/`, preview with `--include-example-hosts` and use that same flag with `--apply`; loopback, invalid/DNS-only and multicast addresses remain excluded. Templates without concrete addresses are not resolved. The importer adds inventory and automatic host ping checks only; old service commands, plugin binaries, credentials, accounts and topology are not migrated. Configure compatible service checks and SNMP credentials through the console afterwards. Running the import again skips already-present IPs.

## Clear every monitored device

If an old import should be removed entirely, stop the service as root. Then run the clearing command as the application user, and start the service again as root:

```sh
systemctl stop network-monitor
su - aron -c 'cd /home/aron/Network_tool && .venv/bin/python scripts/clear_inventory.py'
systemctl start network-monitor
```

For an installation using `hl-agro`, change the username and home directory in the command. `clear_inventory.py` removes **all** monitored devices, their service checks, saved connections, state history and SNMP inventory snapshots. It also clears and disables discovery so old scan results cannot be selected later. Login accounts and sessions stay available. This operation affects the configured `NETWORK_DB` (default: `data/network.db`); use the same setting as the service if customized. It does not delete backup ZIP files or backups stored elsewhere.
