"""Optional AI review (off by default): Claude writes an advisory read of one instrument's stored analysis.

Advisory only. This package reads analysis results and writes its own tables. It has no access to the MT5 gateway and
cannot place, modify or close a trade. Spending is capped by a monthly budget enforced in the database.
"""
