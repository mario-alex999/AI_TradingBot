"""Module 06 fixtures and helpers live in risk_helpers.py (a unique module name); re-exported for pytest.
`import *` skips underscore names, so the autouse fixture is imported by name."""
from risk_helpers import *  # noqa: F401,F403
from risk_helpers import _template  # noqa: F401
