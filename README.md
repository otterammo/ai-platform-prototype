# AI Platform Prototype

A functional prototype of a declarative AI orchestration platform inspired by Kubernetes.

The prototype treats AI work as resources:

- `Workspace` defines a project boundary and storage root.
- `Mission` declares a desired outcome.
- `Fleet` is created by the Mission controller.
- `Agent` is created by the Fleet controller and executes the Mission through a model abstraction.

Resources use the shape:

```yaml
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: implement-auth
  namespace: demo
spec:
  objective: Implement authentication
  brief:
    ref: knowledge://prd.md
status:
  phase: Pending
```

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Run The Prototype

Apply the demo resources:

```bash
platform --db sqlite:///./platform.db --root .platform apply examples/demo/resources.yaml
```

Run one reconciliation pass:

```bash
platform --db sqlite:///./platform.db --root .platform reconcile
```

Inspect resources and events:

```bash
platform --db sqlite:///./platform.db --root .platform get missions
platform --db sqlite:///./platform.db --root .platform describe mission implement-auth -n demo
platform --db sqlite:///./platform.db --root .platform events
```

Run the API:

```bash
platform --db sqlite:///./platform.db --root .platform serve
```

Useful endpoints:

- `POST /resources/apply`
- `GET /resources`
- `GET /resources/{kind}/{name}?namespace=demo`
- `DELETE /resources/{kind}/{name}?namespace=demo`
- `POST /reconcile`
- `GET /events`
- `GET /artifacts`

Deleting a Mission or Workspace removes the corresponding resource and artifact records from SQLite, but it does not delete artifact files from disk.

## Model Providers

The default provider is `stub`, which produces deterministic markdown artifacts without network access.

To use an OpenAI-compatible endpoint, set a model config on the Workspace or Mission:

```yaml
model:
  provider: openai-compatible
  model: gpt-4.1-mini
  baseUrl: https://api.openai.com/v1
  apiKeyEnv: OPENAI_API_KEY
```

The runtime sends chat-completions-compatible requests to `{baseUrl}/chat/completions`.
