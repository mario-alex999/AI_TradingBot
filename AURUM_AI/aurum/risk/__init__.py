"""Module 06 - Advanced Risk Management Engine.

Decides whether a proposed trade is financially and technically safe enough to go to human approval (Module 07), sizes it,
and enforces account, portfolio and market-condition limits. It reads stored data only. It never places, modifies or closes
an order and never connects to MetaTrader 5: tests/risk/test_safety.py scans this package for execution calls.
"""
ENGINE_VERSION = "6.0.0"
SOURCE_MODULE = "module_06_risk"
