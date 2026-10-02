"""Keep all checklist fixtures independent of live account/admin configuration."""
import pytest


@pytest.fixture(autouse=True)
def no_live_configuration(monkeypatch):
    monkeypatch.delenv('COVENANT_TRADING_ACCOUNTS', raising=False)
    monkeypatch.delenv('COVENANT_OPERATOR_TOKEN', raising=False)
