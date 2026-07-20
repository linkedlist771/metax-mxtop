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
- Remote mode is telemetry-only and does not expose process signals over HTTP.
- Remote SSH connections use configured keys or `ssh-agent`; password
  authentication is not stored or prompted for by mxtop.
- The web dashboard and local Prometheus exporter bind to `127.0.0.1` by
  default. Binding to a non-loopback address without a token prints a warning.

## Secure deployment of HTTP endpoints

The dashboard and exporter use plain HTTP. `--auth-token` protects access but
**does not encrypt traffic**. For access across an untrusted network:

1. Keep mxtop bound to loopback and expose it through a TLS-terminating reverse
   proxy, VPN, or SSH tunnel; or bind it only to a trusted private interface.
2. Use a high-entropy token via `--auth-token` or `MXTOP_AUTH_TOKEN`. Prefer an
   environment variable or protected config file over shell history.
3. For browser access, visit `?token=...` once. The server sets a 24-hour
   `HttpOnly; SameSite=Strict` cookie, marks the bootstrap response `no-store`,
   and the dashboard immediately removes the token from the visible URL and
   browser history. API and Prometheus clients should send
   `Authorization: Bearer <token>` instead.
4. Do not place tokens in committed config files, screenshots, issue reports,
   or shared command histories. Restrict config-file permissions when it
   contains `remote.auth-token`.
5. Treat exported telemetry as sensitive. It can include hostnames, usernames,
   PIDs, process command lines, GPU UUIDs/BDFs, resource usage, and cluster
   topology.

The server sets a restrictive Content Security Policy, denies framing, sends
`Referrer-Policy: no-referrer`, disables unnecessary browser permissions, and
limits concurrent SSE clients. These are defense-in-depth measures, not a
replacement for TLS and network access control.

The release workflow pins every third-party GitHub Action to an immutable
40-character commit SHA. Dependabot monitors the GitHub Actions ecosystem for
updates so these pins remain reviewable rather than silently following mutable
tags.

## Out of scope

Reports that require an attacker who already has arbitrary local root access,
or that concern unsupported modifications of the packaged dashboard assets,
are generally out of scope unless they demonstrate a boundary violation that
persists in a normal supported deployment.
