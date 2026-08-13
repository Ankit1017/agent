# ADR 0022: Browser events and owner-bound approvals

## Status

Accepted.

## Decision

Use REST for snapshots/mutations and a bounded version-1 WebSocket stream for observable events.
Bind approval authority to the task-origin client and reject on timeout, disconnect, task end, or
shutdown. Permit one task per workspace and two globally.

## Consequences

Reconnect combines retained events with persisted session snapshots. Slow clients resynchronize;
synchronous agent and approval waits stay off the event loop.
