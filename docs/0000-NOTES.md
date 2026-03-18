-------------------------------------------------------------------------------------------------------------------
QRZ
-------------------------------------------------------------------------------------------------------------------
Wherever RollingThunder currently does or will do a QRZ lookup, the call should look like this:

result = lookup_qrz_with_cache(r, call, client.lookup_callsign)

If that direct usage point is not yet wired anywhere real, then the feature is implemented but not yet integrated.

Something like:

# QRZ lookups are cached in Redis for 30 days under rt:qrz:<CALL>.

Tiny breadcrumb. Future-you will appreciate it when present-you has become archaeology.

Whichever service will perform the QRZ lookup should get:

[Service]
EnvironmentFile=/etc/rollingthunder/qrz.env

For example, if the lookup happens in a service unit like:

/etc/systemd/system/rt-some-worker.service

then the service section might look like:

[Service]
User=spiff
WorkingDirectory=/opt/rollingthunder
EnvironmentFile=/etc/rollingthunder/qrz.env
EnvironmentFile=-/etc/rollingthunder/redis.env
ExecStart=/opt/rollingthunder/.venv/bin/python /opt/rollingthunder/some_worker.py
Restart=always
========================================

