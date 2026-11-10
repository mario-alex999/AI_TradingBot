from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(scope="session")
def m08_template_root(tmp_path_factory: Any) -> Any:
    return tmp_path_factory.mktemp("m08-template")


@pytest.fixture
def h(tmp_path: Any, m08_template_root: Any) -> Any:
    from m08_harness import H
    world = H(tmp_path, m08_template_root)
    yield world
    world.engine.dispose()


@pytest.fixture
def ha(h: Any) -> Any:
    """A world whose harmless test workflow is registered, validated and ACTIVE."""
    h.activate_test_workflow()
    return h
