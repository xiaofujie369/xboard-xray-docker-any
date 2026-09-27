The architecture and XBoard API conventions are based on
https://github.com/xiaofujie369/xboard-xray-docker-sync at commit
5b826230b44d8d7e579dff392e2998d817877a4f.

sync/status.py adapts the system metrics helpers from that project's
sync/xboard_report.py. The upstream README describes its license as MIT;
the referenced checkout did not contain a separate LICENSE file.

The Docker image builds sing-box v1.12.25 (commit
73bfb99ebce7923c485435e4faf8571b412065a9) with the authenticated-user session
limiter overlay in core/. The resulting version is 1.12.25-xbs1, not an official
SagerNet release. core/apply.py records the exact source changes and fails on
unexpected upstream source. The overlay and modified core are GPL-3.0-or-later;
the upstream license is reproduced in core/LICENSE. Dockerfile contains the
reproducible build steps. Distributors must also satisfy the GPL corresponding
source requirements for the core and its dependencies. Python synchronization
code is separate from the sing-box core.
