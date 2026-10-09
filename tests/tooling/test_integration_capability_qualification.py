import pytest
from integration_secrets import OperatorError
from qualify_integration_application import provider_capabilities


def capabilities(enabled=False):
    return {
        "production_writes_enabled": False,
        "capabilities": [
            {"key": key, "availability": "internal_only" if enabled else "disabled"}
            for key in ("provider.slack", "provider.gsc", "provider.github")
        ]
        + [{"key": "database.command_acceptance", "availability": "disabled"}],
    }


def test_default_profile_still_requires_disabled_providers():
    assert provider_capabilities(capabilities()) == "DISABLED"
    with pytest.raises(OperatorError):
        provider_capabilities(capabilities(True))


def test_owner_profile_qualifies_only_internal_test_connectors_not_provider_connections():
    assert (
        provider_capabilities(capabilities(True), owner_connectors=True)
        == "INTERNAL_ONLY_TEST_CONNECTORS_WORK_DISABLED"
    )
    with pytest.raises(OperatorError):
        provider_capabilities(capabilities(), owner_connectors=True)


@pytest.mark.parametrize(
    "mutation", ["writes", "worker", "provider", "other", "missing", "duplicate"]
)
def test_qualification_rejects_authority_expansion_or_missing_denial(mutation):
    value = capabilities(True)
    if mutation == "writes":
        value["production_writes_enabled"] = True
    elif mutation == "worker":
        value["capabilities"][-1]["availability"] = "internal_only"
    elif mutation == "provider":
        value["capabilities"][0]["availability"] = "available"
    elif mutation == "other":
        value["capabilities"].append(
            {"key": "provider.unregistered", "availability": "internal_only"}
        )
    elif mutation == "missing":
        value["capabilities"].pop()
    else:
        value["capabilities"].append(value["capabilities"][0])
    with pytest.raises(OperatorError):
        provider_capabilities(value, owner_connectors=True)
