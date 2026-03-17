-------------------------------------------------------------------------------------------------------------------
QRZ
-------------------------------------------------------------------------------------------------------------------
Wherever RollingThunder currently does or will do a QRZ lookup, the call should look like this:

result = lookup_qrz_with_cache(r, call, client.lookup_callsign)

If that direct usage point is not yet wired anywhere real, then the feature is implemented but not yet integrated.

Something like:

# QRZ lookups are cached in Redis for 30 days under rt:qrz:<CALL>.

Tiny breadcrumb. Future-you will appreciate it when present-you has become archaeology.
========================================

