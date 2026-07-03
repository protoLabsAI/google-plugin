"""Test bootstrap — make the plugin importable with NO protoAgent host.

The host loads the plugin as a package; the suite registers the same shape as a
synthetic package (``google_plugin`` — NOT ``google``, which is a real namespace
package owned by protobuf/googleapis) so the modules' relative imports
(``from .auth import …``) resolve standalone. Executing ``__init__.py`` needs only
langchain-core + fastapi from requirements-dev.txt; host-only imports stay lazy.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "google_plugin"

if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)


class FakeRegistry:
    """Just enough registry to smoke-test register() with no host."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.tools: list = []
        self.routers: list = []  # (router, prefix)

    def register_tool(self, t):
        self.tools.append(t)

    def register_router(self, router, prefix: str | None = None):
        self.routers.append((router, prefix))


@pytest.fixture
def registry():
    return FakeRegistry()
