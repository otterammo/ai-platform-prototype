# Installation

In this chapter you install the platform, verify the CLI, and start the API.

## Install

```bash
git clone <repository-url>
cd ai-platform-prototype
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Use the local SQLite database and platform root for the tutorial:

```bash
export AI_PLATFORM_DB=sqlite:///./platform.db
export AI_PLATFORM_ROOT=.platform
```

Prepare the tutorial workspace files:

```bash
mkdir -p day0
cp -R docs/tutorials/assets/day0/. day0/
```

## Verify The CLI

```bash
platform version
platform health
```

Expected result:

```yaml
version: 0.1.0
```

```yaml
status: ok
version: 0.1.0
```

## Start The API

In a second terminal, with the same virtual environment active:

```bash
platform serve
```

From the first terminal:

```bash
platform health --api-url http://127.0.0.1:8000
```

You can stop the API with `Ctrl-C`. The remaining chapters use the CLI.
