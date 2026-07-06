# Documentation

Full documentation for **Skrooge2Firefly** — migrate a Skrooge SQLite file into
Firefly III, and export it back out again.

Start with the [project README](../README.md) for the overview and quick start.

## Guides

| Guide | What it covers |
|---|---|
| [Installation](installation.md) | Requirements, `uv sync`, creating a Firefly token |
| [Configuration](configuration.md) | `.env`, settings precedence, reverse-proxy / SSO-OIDC setups |
| [Importing](importing.md) | Skrooge → Firefly: dry-run, targets, reconciliation, resume, update, resilience |
| [Exporting](exporting.md) | Firefly → Skrooge: QIF vs native sqlite, self-verification |
| [Verification](verification.md) | The two parity checks and which mismatches are expected |
| [Data mapping](data-mapping.md) | How every Skrooge concept maps onto Firefly III |
| [CLI reference](cli-reference.md) | Every flag, default, and exit code |
| [Troubleshooting](troubleshooting.md) | 401s, SSO/OIDC redirects, timeouts, mismatches |
| [Architecture](architecture.md) | Module map and design, for contributors |
