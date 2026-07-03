"""Plugin-level tests — manifest coherence, register() wiring, tool wrappers, view rules.

These test the ACTUAL registered surface: routers are mounted exactly as the host
mounts them (page at /plugins/google, data at /api/plugins/google) and the manifest's
views[].path is asserted against what the page router really serves.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import google_plugin as plugin
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google_plugin import gcal, gmail, view
from google_plugin.auth import Creds

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


# ── Manifest coherence ────────────────────────────────────────────────────────


def test_manifest_version_matches_pyproject():
    assert MANIFEST["version"] == PYPROJECT["project"]["version"]


def test_manifest_basics():
    assert MANIFEST["id"] == "google"
    assert MANIFEST["enabled"] is False  # enabling is the operator's trust decision
    assert isinstance(MANIFEST["config_section"], str)
    assert set(MANIFEST["secrets"]) <= set(MANIFEST["config"])
    assert MANIFEST["min_protoagent_version"]


def test_init_has_no_top_level_relative_imports():
    # pytest imports a repo-root __init__.py as a nameless top-level module during
    # package setup — a top-level `from . import x` breaks the whole suite there.
    # Relative imports in __init__.py must live inside functions.
    tree = ast.parse((ROOT / "__init__.py").read_text())
    offenders = [n.lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.level > 0]
    assert not offenders, f"top-level relative imports in __init__.py at lines {offenders}"


def test_no_stdlib_shadowing_module_names():
    # A root-level calendar.py breaks stdlib email (which imports calendar) for any
    # Python started from this directory. Keep service modules off stdlib names.
    stdlib = set(sys.stdlib_module_names)
    for p in ROOT.glob("*.py"):
        assert p.stem == "__init__" or p.stem not in stdlib, f"{p.name} shadows a stdlib module"


# ── register() wiring ─────────────────────────────────────────────────────────


def test_register_wires_tools_and_routers(registry):
    registry.config = {"client_id": "cid", "client_secret": "cs", "refresh_token": "rt"}
    plugin.register(registry)
    assert {t.name for t in registry.tools} == {
        "gmail_list_unread",
        "gmail_search",
        "gmail_get_thread",
        "gmail_create_draft",
        "calendar_list_upcoming",
        "calendar_event_detail",
    }
    assert [p for _, p in registry.routers] == [None, "/api/plugins/google"]
    assert plugin._creds().configured()


def test_every_tool_has_a_description():
    # An f-string "docstring" leaves __doc__ None → the tool ships undescribed.
    for t in plugin.TOOLS:
        assert t.description and len(t.description) > 20, t.name


def test_tools_hint_when_unconfigured(monkeypatch):
    monkeypatch.setattr(plugin, "_CREDS", Creds("", "", ""))
    out = plugin.gmail_list_unread.invoke({})
    assert "isn't configured" in out and "GOOGLE_CLIENT_ID" in out


def test_list_unread_quotes_the_label(monkeypatch):
    # An unquoted multi-word label would split into label:Priority + free-text "Inbox".
    monkeypatch.setattr(plugin, "_CREDS", Creds("c", "s", "r"))
    seen = {}
    monkeypatch.setattr(gmail, "list_messages", lambda creds, q, mx, **kw: seen.update(q=q) or [])
    plugin.gmail_list_unread.invoke({"label": "Priority Inbox"})
    assert seen["q"] == 'label:"Priority Inbox" is:unread'


def test_draft_tool_requires_target(monkeypatch):
    monkeypatch.setattr(plugin, "_CREDS", Creds("c", "s", "r"))
    out = plugin.gmail_create_draft.invoke({"body": "hi"})
    assert "thread_id" in out and "subject" in out


# ── Console view (the four rules) ─────────────────────────────────────────────


def _mounted_app() -> FastAPI:
    """Mount the routers exactly as the host does for register()'s two calls."""
    page, data = view.build_router(plugin._creds, gmail, gcal)
    app = FastAPI()
    app.include_router(page, prefix="/plugins/google")
    app.include_router(data, prefix="/api/plugins/google")
    return app


def test_manifest_view_path_is_served_and_public():
    c = TestClient(_mounted_app())
    (view_decl,) = MANIFEST["views"]
    assert view_decl["path"] in MANIFEST["public_paths"]
    r = c.get(view_decl["path"])
    assert r.status_code == 200 and "<title>Google Workspace</title>" in r.text
    # The page must NOT be served under the gated /api prefix.
    assert c.get("/api" + view_decl["path"]).status_code == 404


def test_data_routes_are_gated_prefix_and_degrade_unconfigured(monkeypatch):
    monkeypatch.setattr(plugin, "_CREDS", Creds("", "", ""))
    c = TestClient(_mounted_app())
    assert c.get("/api/plugins/google/status").json() == {"configured": False, "has_client": False}
    assert c.get("/api/plugins/google/unread").json() == {"messages": []}
    assert c.get("/api/plugins/google/upcoming").json() == {"events": []}


def test_page_is_four_rules_compliant():
    page = view.PAGE
    assert 'location.pathname.split("/plugins/")[0]' in page  # slug-aware base
    assert "/_ds/plugin-kit.css" in page and "/_ds/plugin-kit.js" in page  # DS kit
    assert "apiFetch" in page  # authed fetch via the kit
    assert ":root{" not in page and ":root {" not in page  # no hand-rolled theme map
    assert 'addEventListener("message"' not in page  # kit owns the handshake
