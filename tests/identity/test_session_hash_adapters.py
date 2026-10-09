"""The nine public-domain adapters preserve the canonical token/error contract."""

import importlib
from hashlib import sha256

import pytest
from signal_core.authorization import InvalidSession
from signal_core.bing_binding import BingBindingError
from signal_core.gsc_binding import GscBindingError

ADAPTERS = (
    ("session_management", "_session_hash", InvalidSession, (), True),
    ("origin_verification", "_session_hash", InvalidSession, (), True),
    ("site_onboarding", "_session_hash", InvalidSession, (), True),
    ("bing_binding", "_session_hash", BingBindingError, ("BING_SESSION_REJECTED",), False),
    ("gsc_binding", "_session_hash", GscBindingError, ("GSC_SESSION_REJECTED",), False),
    ("github_read_binding", "_token_hash", InvalidSession, (), False),
    ("github_pr_extension", "_token_hash", InvalidSession, (), False),
    ("technical_recipe_service", "_token_hash", InvalidSession, (), False),
    ("candidate_build_service", "_token_hash", InvalidSession, (), False),
)
TOKEN = "synthetic-" + "a" * 33


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a[0])
def test_canonical_hash_and_factory_are_shared(adapter):
    module, name, _, _, _ = adapter
    function = getattr(importlib.import_module("signal_core." + module), name)
    assert function.__module__ == "signal_core.session_tokens"
    assert function(TOKEN) == sha256(TOKEN.encode("ascii")).digest()


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a[0])
@pytest.mark.parametrize(
    "value", (None, 1, True, b"synthetic-token", [], {}, "", "a" * 42, "a" * 44, "!" * 43)
)
def test_invalid_values_keep_each_domains_exact_rejection(adapter, value):
    module, name, error, arguments, _ = adapter
    function = getattr(importlib.import_module("signal_core." + module), name)
    with pytest.raises(error) as rejected:
        function(value)
    assert type(rejected.value) is error
    assert rejected.value.args == arguments
    assert rejected.value.__cause__ is None
    assert rejected.value.__suppress_context__ is True


class BrokenEncoding(str):
    def encode(self, *args, **kwargs):
        raise TypeError("synthetic-encoding-failure")


class UnexpectedFailure(str):
    def encode(self, *args, **kwargs):
        raise RuntimeError("synthetic-unexpected-failure")


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a[0])
def test_type_error_translation_is_not_broadened(adapter):
    module, name, error, arguments, translate = adapter
    function = getattr(importlib.import_module("signal_core." + module), name)
    with pytest.raises(error if translate else TypeError) as rejected:
        function(BrokenEncoding(TOKEN))
    assert rejected.value.args == (arguments if translate else ("synthetic-encoding-failure",))
    with pytest.raises(RuntimeError, match="synthetic-unexpected-failure"):
        function(UnexpectedFailure(TOKEN))
