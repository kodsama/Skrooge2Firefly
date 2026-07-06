# Troubleshooting

## Authentication failed (401): check FIREFLY_TOKEN

The token is missing, wrong, or expired. Confirm `FIREFLY_TOKEN` in `.env`, or
pass `--token`. Regenerate the token in Firefly (**Options → Profile → OAuth →
Personal Access Tokens**) if needed.

## Firefly behind SSO/OIDC

**Symptom:** the tool fails on the very first request; with `-v` you see a
redirect (`302`) to a login page, or JSON parsing errors on an HTML body.

**Cause:** a reverse proxy or identity-aware proxy is enforcing SSO/OIDC in
front of Firefly. It intercepts the API request (which carries only a Firefly
token) and bounces it to the identity provider before it reaches Firefly.

**Fix:** the Firefly **API path must not sit behind the SSO layer** — exclude
`/api/` from proxy auth, or expose the API on a route/subdomain that isn't
gated. See [configuration](configuration.md#firefly-iii-behind-a-reverse-proxy-or-ssooidc).

**Quick check** for any candidate URL:

```bash
# With a valid token → HTTP 200, zero redirects:
curl -s -o /dev/null -w '%{http_code} redirects=%{num_redirects}\n' \
  -H "Authorization: Bearer $FIREFLY_TOKEN" https://your-host/api/v1/about

# Without a token → HTTP 401 (NOT a 302 to a login page):
curl -s -o /dev/null -w '%{http_code}\n' https://your-host/api/v1/currencies
```

A `302` means the auth layer is still in the path.

## The run stopped after 15 failures

A safety breaker: 15 consecutive transaction failures are treated as "the server
is down" and the run aborts rather than hammering it. Check the server, then
re-run the same command — the API import resumes from `.import-state.json`.

## Timeouts / slow or overloaded server

- Raise `--timeout` (default 30s).
- Lower `--concurrency` (default 1) — or keep it at 1 for a small instance.
- Transient errors (429/5xx, Cloudflare 52x, dropped connections, empty-200
  bodies) are retried automatically with backoff, up to 8 times per request.

## Interrupted import

Just run the same `--import` command again. Created records are recorded in
`.import-state.json` and existing ones are reconciled by `external_id` / name,
so nothing is duplicated and the run picks up where it left off.

## A dry-run reports issues (exit 1)

`--dry-run` fails if any record would be rejected by Firefly — most commonly an
amount that rounds to `0.00`. The offending records are logged. Sub-cent Skrooge
float artefacts are skipped automatically; a genuine `0.00` issue is worth
inspecting in the source.

## Verification shows mismatches

- **`SKIP (mixed-currency)`** and other *informational* lines are expected and
  do not fail the run — Firefly revalues foreign holdings at its own rates. See
  [verification](verification.md#informational-non-failing-mismatches).
- **`BALANCE MISMATCH`** from the independent latest-balance check is the one to
  investigate — it compares Skrooge's own balance to Firefly's without going
  through the import mapping.

## Export self-verification FAILED

The file was written but its per-account counts/sums didn't match Firefly. Re-run
the export. If it persists, run with `-v` to see which accounts diverge, and
confirm the `--template` is a valid Skrooge file for the `sqlite` format.

## `Unknown --only sections`

`--only` accepts a comma-separated subset of exactly:
`accounts,transactions,budgets,subscriptions`.
