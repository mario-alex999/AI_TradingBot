from __future__ import annotations

import warnings
from typing import Any

import pytest

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")


@pytest.fixture(scope="session")
def m07_template_root(tmp_path_factory: Any) -> Any:
    return tmp_path_factory.mktemp("m07-template")


@pytest.fixture
def h(tmp_path: Any, m07_template_root: Any) -> Any:
    from m07_harness import H
    world = H(tmp_path, m07_template_root)
    yield world
    world.engine.dispose()
