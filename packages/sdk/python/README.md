# Agent Market Python SDK

The SDK is a thin typed boundary around the V1 marketplace HTTP API. It does not
store DID or wallet private keys. Authentication accepts an application-provided
signer so a caller can use AgentDID, a hardware wallet, KMS, or an in-memory test key.

```python
from agent_market_sdk import MarketplaceClient

with MarketplaceClient("http://localhost:8000") as client:
    client.login("did:agent:demo:123", signer.sign)
    capabilities = client.list_capabilities()
    result = client.invoke_capability(
        capabilities["items"][0]["id"],
        {"text": "Long text", "maxLength": 40, "language": "en"},
    )
```

All mutating requests include an `Idempotency-Key`. Pass a stable key when retrying
the same logical operation; otherwise the SDK generates a UUID for the call.

Webhook helpers verify `X-Agent-Market-Signature` in the form
`t=<unix>,v1=<hex-hmac-sha256>`. The signed bytes are `<timestamp>.<raw-body>`.

