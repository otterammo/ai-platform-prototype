# AI Platform Prototype

A functional prototype of a declarative AI orchestration platform inspired by Kubernetes.

The prototype treats AI work as resources:

- `Workspace` defines a project boundary and storage root.
- `Mission` declares a desired outcome.
- `FleetTemplate` declares the Agent topology for a class of work.
- `Fleet` is instantiated from a Mission and FleetTemplate.
- `Capability`, `Tool`, and `Model` describe what Agents need and how the platform can satisfy it.
- `Agent` is created by the Fleet controller and executes through an embedded `Pilot`.
- `Knowledge` records describe workspace knowledge nodes separately from manifests.

Resources use the shape:

```yaml
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: implement-auth
  namespace: demo
spec:
  template: software-feature
  inputs:
    prd:
      ref: knowledge://prd.md
  outputs:
    code: true
status:
  phase: Pending
```

The registry-driven reconciliation flow is:

```text
Mission -> FleetTemplate -> Fleet -> Agents -> Capabilities -> Tools/Pilot/Model
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
platform --db sqlite:///./platform.db --root .platform get fleettemplates
platform --db sqlite:///./platform.db --root .platform get capabilities
platform --db sqlite:///./platform.db --root .platform get models
platform --db sqlite:///./platform.db --root .platform get tools
platform --db sqlite:///./platform.db --root .platform get knowledge -n demo
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

To use an OpenAI-compatible endpoint in the registry-driven path, create a `Model` resource and reference it through a Capability-compatible Pilot:

```yaml
kind: Model
metadata:
  name: gpt-coder
spec:
  config:
    provider: openai-compatible
    model: gpt-4.1-mini
    baseUrl: https://api.openai.com/v1
    apiKeyEnv: OPENAI_API_KEY
```

The runtime sends chat-completions-compatible requests to `{baseUrl}/chat/completions`.

Legacy Missions with `spec.objective` and `spec.brief` still work; they reconcile through the original single-agent path.
