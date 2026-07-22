# Security Policy

## Supported versions

Security fixes are applied to the latest release and to `main`. Older
releases may not receive backports; upgrade to the newest published version
before reporting behavior that may already be fixed.

## Reporting a vulnerability

Please do not publish exploit details in a public issue.

1. Use GitHub's **Report a vulnerability** form under the repository's
   Security tab when private vulnerability reporting is available:
   <https://github.com/linkedlist771/metax-mxtop/security/advisories/new>
2. If that form is unavailable, open a public issue containing only a request
   for a private contact channel — do not include reproduction steps, tokens,
   hostnames, command lines, or other sensitive data.

Include the affected version or commit, deployment mode (local TUI, local
exporter, or remote dashboard), platform/Python version, impact, and the
smallest safe reproducer. You should receive an acknowledgement within seven
days. Once a fix is available, coordinated disclosure and credit will be
agreed with the reporter.

## Security boundaries

- GPU administration is read-only. `mxtop` does not update firmware, reset
  GPUs, or change persistence/admin settings.
- Local TUI process signals require confirmation, validate process identity
  against PID reuse, and enforce process ownership unless run as root. Use
  `--readonly` to disable them entirely.
- Remote mode is telemetry-only and does not expose process signals through
  the HTTP or HTTPS API.
- Remote SSH connections use configured keys or `ssh-agent`; password
  authentication is not stored or prompted for by mxtop.
- The web dashboard and local Prometheus exporter bind to `127.0.0.1` by
  default. Non-loopback plain HTTP and missing authentication produce separate
  warnings.

## Secure deployment of web endpoints

The dashboard and exporter use HTTP by default and can terminate TLS directly
with paired `--tls-cert` and `--tls-key` options. An encrypted private key also
requires `--tls-key-password-file`; the file must contain one non-empty line.
The same `tls-cert`, `tls-key`, and optional `tls-key-password-file` names are
accepted in the `[remote]` config table for dashboard defaults. Direct TLS uses
TLS 1.2 or newer. For access beyond loopback:

1. Supply a PEM full chain (server certificate followed by intermediates) and
   its matching PEM private key, or keep mxtop on loopback behind a trusted
   TLS-terminating reverse proxy, VPN, or SSH tunnel. The certificate SAN must
   cover the hostname or IP address clients actually use.
2. Certificates from a private CA or self-signed certificates must be trusted
   explicitly by every browser, API client, and Prometheus server. Configure a
   Prometheus `tls_config.ca_file` and keep verification enabled; do not use
   `insecure_skip_verify` to hide a name or trust failure.
3. Restrict the TLS private key and optional password file to the service
   account (for example, mode `0600`). Store only their paths in config. Never
   put private-key or password contents in committed files, shell arguments,
   logs, screenshots, or issue reports.
4. Use a high-entropy token via `--auth-token` or `MXTOP_AUTH_TOKEN` and enforce
   host/network firewall policy. TLS encrypts traffic; it does not authorize
   users or make an intentionally public bind private. A reverse proxy remains
   useful for ACME renewal, automatic reload, mutual TLS, or centralized access
   policy.
5. For browser access, visit `?token=...` once. The server sets a 24-hour
   `HttpOnly; SameSite=Strict` cookie, adds `Secure` when it terminates TLS
   directly, marks the bootstrap response `no-store`, and immediately removes
   the token from the visible URL and browser history. If a reverse proxy
   terminates TLS, keep the mxtop listener on loopback and configure the proxy
   to append `Secure` to the upstream cookie. API and Prometheus clients should
   send `Authorization: Bearer <token>` instead.
6. Certificate, key, and password files are loaded once. Restart mxtop after a
   certificate renewal, key rotation, or password-file update. A reverse proxy
   can own zero-downtime certificate reload when that is operationally required.
7. Do not place tokens in committed config files, screenshots, issue reports,
   or shared command histories. Restrict config-file permissions when it
   contains `remote.auth-token`.
8. Treat exported telemetry as sensitive. It can include hostnames, usernames,
   PIDs, process command lines, GPU UUIDs/BDFs, resource usage, and cluster
   topology.

In a secure browser context, the dashboard keeps its bounded incident history
available across reloads in the same browser tab for up to one hour. The
IndexedDB record is encrypted with AES-GCM and partitioned by a hash of the
monitored host set; its random key lives only in `sessionStorage`, is never
derived from the dashboard token, and is rotated on a new `?token=` bootstrap.
When the browser discards that tab session, old ciphertext becomes
undecryptable even if the browser has not yet removed it. Use Clear history in
the footer to remove the current tab session's retained trends and process
records; the current live sample remains visible.

Web Crypto is available only in secure browser contexts. Loopback origins are
normally browser-trusted; non-loopback clients should use direct HTTPS or an
HTTPS reverse proxy. On plain LAN HTTP, the dashboard keeps incident history
only in memory for the open page and cannot recover it after a reload.

The server sets a restrictive Content Security Policy, denies framing, sends
`Referrer-Policy: no-referrer`, disables unnecessary browser permissions, and
limits concurrent SSE clients. These are defense-in-depth measures, not a
replacement for TLS and network access control.

Release publication is gated on all configured test and lint jobs. The wheel
and source distribution are built and validated once, and both GitHub Releases
and PyPI verify the same checksum-protected workflow artifact before consuming
it. Automation refuses to replace an existing tagged GitHub Release. OIDC
permission is confined to the minimal PyPI job, which downloads, verifies, and
publishes the prebuilt distributions without checking out source or rebuilding
packages.

The CI workflows pin every third-party GitHub Action to an immutable
40-character commit SHA. Dependabot monitors both the GitHub Actions and Python
package ecosystems monthly, so action pins and dependencies remain reviewable
rather than silently following mutable tags or going stale. CodeQL runs
`security-extended` queries over both the Python package and the dashboard's
JavaScript on every push, on eligible same-repository pull requests, and on a
weekly schedule.

## Out of scope

Reports that require an attacker who already has arbitrary local root access,
or that concern unsupported modifications of the packaged dashboard assets,
are generally out of scope unless they demonstrate a boundary violation that
persists in a normal supported deployment.
