"""Module 08 - Workflow Orchestration and Integration Core.

The control plane that lets AURUM AI work with n8n reliably: a central event store with an outbox and an inbox, a workflow
registry with versions, a generic trigger service, execution tracking, signed webhooks, callbacks, idempotency, retries,
dead letters, leases (locks), schedules, a module API gateway and health monitoring.

Boundaries (enforced in code, not only documented):
- n8n runs workflow logic; AURUM keeps the orchestration records and stays the source of truth for them.
- Module 08 has no trading logic. It never approves, never executes, never contacts MT5. Events that belong to Module 06,
  07 or 09 (risk decisions, approvals, execution) can only be published by their owning module, never by n8n or Module 08.
- Execution-handoff workflows can only be triggered for an approval that Module 07 has already approved.
"""
MODULE_VERSION = "8.0.0"
CONTRACT_VERSION = "1.0"          # n8n <-> AURUM contract (trigger, callback, event delivery)
SCHEMA_VERSION = "1.0"            # event envelope schema
SOURCE_MODULE = "module_08"
