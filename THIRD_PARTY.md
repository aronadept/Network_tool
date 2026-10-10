# Third-party components

Network Monitor runs an unmodified Nagios Core executable as a separate process and exchanges generated configuration and status files. No Nagios source or binaries are committed in this repository.

- Nagios Core: GPL version 2; upstream https://github.com/NagiosEnterprises/nagioscore. The optional source build pins 4.5.14, commit cfa5684eb50facc3183d5ddb01d853b9eb9cc218, and retains the corresponding source, build files and upstream LICENSE under `.tools/nagios/source`. Preserve these when redistributing that binary, and follow GPL source/license obligations. Nagios XI is not included. This project is not affiliated with Nagios Enterprises.
- PySNMP: BSD-2-Clause; https://github.com/lextudio/pysnmp.
- cryptography: Apache-2.0 or BSD-3-Clause; https://github.com/pyca/cryptography.
- Python dependencies retain their licenses in their installed distribution metadata. See requirements.txt for pinned versions.
