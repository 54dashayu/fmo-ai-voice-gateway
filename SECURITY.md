# Security Policy

## Sensitive data

Do not open an issue or commit containing:

- Alibaba Cloud Model Studio API keys
- gateway or knowledge-service bearer tokens
- MQTT passwords
- FMO/SAS private keys, certificate fingerprints or private certificates
- SSH private keys
- raw voice captures from users

Revoke exposed credentials before reporting the incident. Reports should contain only redacted logs and reproducible, non-secret configuration examples.

## Production boundary

The management API is designed for loopback access behind an authenticated reverse proxy. Do not expose ports `18788`, EMQX Dashboard, or SAS authentication services directly to the public Internet.

Real MQTT publication and PTT tests must be explicitly authorized by the FMO server owner and conducted with a clearly identified AI callsign marker.
