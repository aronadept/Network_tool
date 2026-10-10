# Network Monitor

Initial Ubuntu-friendly intranet monitoring application using Python 3.10+ and SQLite. No Python packages or external frontend assets are required.

## Run on Ubuntu

```sh
sudo apt-get update
sudo apt-get install -y python3 iputils-ping
cd /path/to/Network_tool
python3 app.py
```

The server listens on `127.0.0.1:8080`. To access it from your workstation, use an SSH tunnel (`ssh -L 8080:127.0.0.1:8080 user@server`) and open port 8080 locally. For shared intranet access, place an authenticated HTTPS reverse proxy in front of it. This prototype has no built-in login; do not expose its port directly to an untrusted network. Reverse proxies must preserve Host and use appropriate Origin handling for HTTPS.

Optional discovery: enter an authorized IPv4 subnet (/24 or smaller) in the dashboard. Every five minutes, up to 16 parallel ICMP probes discover responding addresses and add them without duplicating existing inventory. First scan starts on the next monitoring cycle. Blank the subnet to disable future scans; an in-progress scan finishes. Ping cannot identify device brand/type, discover ping-blocked devices, or infer physical links.

Add devices by IPv4 address and connect them manually with interface labels. ICMP checks run every 30 seconds; initial checks can take one polling interval. Status transitions appear in the event history. An ICMP failure means the device did not respond to ping; it does not prove all of its services are down. Devices blocking ICMP will show down. A missing ping executable is reported as unavailable, not offline.

Settings: `HOST` (default loopback), `PORT` (8080), `POLL_INTERVAL` (30, minimum 10), `NETWORK_DB` (data/network.db). SQLite data persists across restarts; back it up with SQLite backup tooling. History retains the latest 10,000 transitions globally. Polling is sequential; large networks need a concurrent monitoring worker in a future version.

## Current scope

Implemented: inventory, ping monitoring, latency, event history, persistent storage, manual topology diagram, periodic subnet discovery.

Not yet implemented: SNMP metrics, automatic LLDP/CDP topology discovery, notifications, accounts, production service deployment. These require device capabilities and deployment requirements to be established. Cloud testing does not establish reachability to your private intranet.

## Validate

```sh
python3 -m unittest discover -s tests -v
```
