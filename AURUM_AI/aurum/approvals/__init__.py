"""Module 07 - Human Approval and Trade Management Engine.

Controls the step from an AI-generated trade idea (Module 05) to an execution request for Module 09. Every request needs a
current Module 06 risk assessment and the explicit decision of a signed-in person. Silence, timeouts, opening a
notification and AI confidence never count as approval, and a Module 06 BLOCK can never be approved.

**Module 07 never places, modifies or closes an order and has no MT5 connection.** It publishes execution and
trade-management requests for Module 09, which must validate them independently before acting.
"""
MODULE_VERSION = "7.0.0"
SCHEMA_VERSION = "1.0"           # version of the event and payload contracts
SOURCE_MODULE = "module_07"
