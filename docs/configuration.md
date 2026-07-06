# Configuration

All settings come from three sources, in descending precedence:

**CLI flag  >  real environment variable  >  `.env` file  >  built-in default**

Copy the example file and edit it:

```bash
cp .env.example .env
```

`.env` is gitignored — your token never enters version control.

## Settings

| Variable | Meaning | CLI override | Default |
|---|---|---|---|
| `SKROOGE_FILE` | Source Skrooge `.sqlite` for `--import` | `--import FILE` | `Skrooge.sqlite` |
| `FIREFLY_URL` | Firefly III base URL, e.g. `https://firefly.example.com` | `--url` | `https://firefly.example.com` |
| `FIREFLY_TOKEN` | Firefly Personal Access Token | `--token` | *(required for API/export)* |
| `SKROOGE_TEMPLATE` | Template `.sqlite` for `--export` to a native Skrooge file | `--template` | falls back to `SKROOGE_FILE` |

Example `.env`:

```dotenv
SKROOGE_FILE=Skrooge.sqlite
FIREFLY_URL=https://firefly.example.com
FIREFLY_TOKEN=your-personal-access-token-here
```

The URL is used as-is with `/api/v1/...` appended; a trailing slash is trimmed.
Both `http://` and `https://` are accepted.

## Firefly III behind a reverse proxy or SSO/OIDC

The tool authenticates **only** with a Firefly Personal Access Token sent as an
`Authorization: Bearer` header. It has no interactive login, cookie, or OIDC
flow.

If your Firefly instance sits behind a proxy that enforces its own auth layer
(SSO/OIDC, forward-auth, an identity-aware proxy, etc.), that layer intercepts
requests **before** they reach Firefly. An API call carrying only a Firefly
token is unauthenticated *to the proxy*, so it is typically answered with a
`302` redirect to the identity provider instead of reaching the API — and the
tool fails immediately on the first request.

To use the API in that setup, the Firefly **API path must not be behind the
SSO/OIDC layer**. Options, cleanest first:

1. **Exclude `/api/` from the proxy auth**, or expose the API on a separate
   route/subdomain that is not gated by SSO. Firefly's own token auth then does
   the gatekeeping — which is exactly what this tool expects. SSO is meant for
   interactive browser users, not machine-to-machine API calls.
2. **Point `FIREFLY_URL` at Firefly directly** (its container/LAN address, or an
   SSH tunnel), bypassing the proxy entirely.

You can confirm a route is usable: a request with a valid token returns `200`
with **no redirects**, and the same request **without** a token returns `401`
(not a `302` to a login page). A `302` means the auth layer is still in the
path. See [troubleshooting](troubleshooting.md#firefly-behind-ssooidc).
