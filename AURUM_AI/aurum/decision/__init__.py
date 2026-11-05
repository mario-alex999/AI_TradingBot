"""Module 05: AI analysis and decision engine.

Turns Module 01-04 outputs into trade candidates for a human to approve or reject. It never places, modifies or closes
an order: "approved" records a person's decision and sends nothing to MT5. Prices are computed by deterministic code;
the AI reviewer can only lower a candidate, never create or change one.
"""
ENGINE_VERSION = "5.0.0"
DISCLAIMER = ("Trade candidates for human review on a demo account. Confidence is agreement of evidence, not a "
              "probability of profit. AURUM AI does not place trades.")
