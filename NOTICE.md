The architecture and XBoard API conventions are based on
https://github.com/xiaofujie369/xboard-xray-docker-sync at commit
5b826230b44d8d7e579dff392e2998d817877a4f.

sync/status.py adapts the system metrics helpers from that project's
sync/xboard_report.py. The upstream README describes its license as MIT;
the referenced checkout did not contain a separate LICENSE file.

The Docker image builds unmodified sing-box source from SagerNet/sing-box.
sing-box carries its own license; refer to the upstream repository when distributing
compiled core images. This project's Python synchronization code is separate from
the sing-box core.
