# Architecture

Authentication should be implemented as a small service boundary with session management owned by the application layer.

## Runtime Flow

Agents should use platform context rather than loading raw knowledge files directly. The runtime receives assembled context with source provenance before invoking the model.
