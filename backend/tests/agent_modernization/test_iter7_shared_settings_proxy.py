"""Iter 7 — extract shared `_SettingsProxy`.

The agent has two byte-identical proxy classes (one in ``graph.py`` and one
in ``tools/intent_classification.py``). They allow tests to override
configuration via attribute assignment without mutating global settings.

Best practice: define the helper once and reuse. This file pins that
contract.
"""

from __future__ import annotations


def test_settings_proxy_is_shared_across_agent_modules() -> None:
    from app.agent import _settings_proxy as shared
    from app.agent import graph as graph_mod
    from app.agent.tools import intent_classification as intent_mod

    # The class itself is shared (single source of truth).
    assert type(graph_mod.settings) is shared.SettingsProxy
    assert type(intent_mod.settings) is shared.SettingsProxy


def test_settings_proxy_overrides_take_precedence_over_base() -> None:
    from app.agent._settings_proxy import SettingsProxy

    class _Base:
        environment = "base"
        only_on_base = "yes"

    proxy = SettingsProxy(_Base())
    assert proxy.environment == "base"
    proxy.environment = "override"
    assert proxy.environment == "override"
    # Unrelated attributes still read through to base.
    assert proxy.only_on_base == "yes"


def test_settings_proxy_handles_none_base_for_test_isolation() -> None:
    from app.agent._settings_proxy import SettingsProxy

    proxy = SettingsProxy(None)
    proxy.foo = 7
    assert proxy.foo == 7
    # Missing attributes raise AttributeError (no silent None passthrough).
    try:
        _ = proxy.does_not_exist
    except AttributeError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected AttributeError for missing attr on None base")
