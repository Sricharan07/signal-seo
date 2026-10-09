import os
import ssl
from uuid import uuid4

import pytest
from signal_core.openbao_http import request
from signal_core.webflow import WebflowUnavailable
from signal_core.webflow_secrets import OpenBaoWebflowSecrets


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_real_tls_openbao_webflow_acl_cas_destroy_and_redaction():
    url, token = (
        os.environ["SIGNAL_TEST_WEBFLOW_BAO_URL"],
        os.environ["SIGNAL_TEST_WEBFLOW_BAO_TOKEN"],
    )
    verify = ssl.create_default_context(cadata=os.environ["SIGNAL_TEST_WEBFLOW_BAO_CA"])
    store = OpenBaoWebflowSecrets(url, token)
    assert (await store.client(verify=verify))["client_secret"].startswith("synthetic-")
    denied = await request(
        base_url=url,
        token=token,
        method="POST",
        path="/signal-webflow/data/client",
        payload={"options": {"cas": 1}, "data": {}},
        verify=verify,
    )
    assert denied.status_code == 403
    identifier = uuid4()
    secret = "synthetic-webflow-access-token-openbao-0098"
    reference = await store.store(identifier, secret, verify=verify)
    assert await store.read(reference, verify=verify) == secret
    with pytest.raises(WebflowUnavailable) as error:
        await store.store(identifier, "synthetic-webflow-replacement-token-0098", verify=verify)
    assert secret not in str(error.value) and token not in str(error.value)
    with pytest.raises(WebflowUnavailable):
        await store.read(reference, verify=False)
    with pytest.raises(WebflowUnavailable):
        await store.read("secret://webflow/../client", verify=verify)
    await store.destroy(reference, verify=verify)
    with pytest.raises(WebflowUnavailable):
        await store.read(reference, verify=verify)
    assert token not in repr(store)
