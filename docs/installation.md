# Installation

## Requirements

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — used for dependency management and running the CLI.
- A **Firefly III** instance you can reach over HTTP(S).
- A Firefly III **Personal Access Token** (see below).

## Install

```bash
git clone <your-fork-or-clone-url> Skrooge2Firefly
cd Skrooge2Firefly
uv sync            # creates the virtualenv and installs everything
```

`uv sync` installs the runtime dependencies (`requests`, `python-dotenv`) plus
the dev tools (`pytest`, `ruff`, `mypy`). Nothing is installed globally; every
command below runs inside the project environment via `uv run`.

Verify the install:

```bash
uv run skrooge-firefly --help
```

## Creating a Firefly III Personal Access Token

1. Log in to Firefly III.
2. Go to **Options → Profile → OAuth**.
3. Under **Personal Access Tokens**, click **Create new token**, name it
   (e.g. `skrooge-migration`), and create it.
4. Copy the token **immediately** — Firefly shows it only once. It is a long
   JWT string.

Put it in your `.env` as `FIREFLY_TOKEN` (see [configuration](configuration.md)).

## Next steps

- [Configuration](configuration.md) — point the tool at your instance.
- [Importing](importing.md) — the Skrooge → Firefly path.
- [Exporting](exporting.md) — the Firefly → Skrooge path.
