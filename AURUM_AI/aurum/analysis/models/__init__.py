"""Module 03 result models (plain dataclasses; ``to_dict()`` gives JSON-friendly output)."""
from aurum.analysis.models.analysis_result import (DISCLAIMER, ConfluenceReport, OteZone, PremiumDiscount,
                                                   ScoreComponent, TechnicalScore, TimeframeAnalysis)
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.fair_value_gap import BalancedPriceRange, FairValueGap
from aurum.analysis.models.liquidity_pool import InducementCandidate, LiquidityEvent, LiquidityPool
from aurum.analysis.models.order_block import OrderBlock
from aurum.analysis.models.structure_event import ChochStatus, Displacement, StructureEvent, StructureState
from aurum.analysis.models.support_resistance import SupportResistanceZone
from aurum.analysis.models.swing import Swing

__all__ = ["DISCLAIMER", "BalancedPriceRange", "CandleSeries", "ChochStatus", "ConfluenceReport", "Displacement",
           "FairValueGap", "InducementCandidate", "LiquidityEvent", "LiquidityPool", "OrderBlock", "OteZone",
           "PremiumDiscount", "ScoreComponent", "StructureEvent", "StructureState", "SupportResistanceZone",
           "Swing", "TechnicalScore", "TimeframeAnalysis"]
