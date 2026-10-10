from __future__ import annotations


REQUIRED_ROUTES = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/meta"),
    ("GET", "/api/v1/openapi.json"),
    ("POST", "/api/v1/auth/challenges"),
    ("POST", "/api/v1/auth/verify"),
    ("GET", "/api/v1/dids/{did:path}"),
    ("GET", "/api/v1/me"),
    ("GET", "/api/v1/agents"),
    ("GET", "/api/v1/agents/{agent_id}"),
    ("GET", "/api/v1/agents/{agent_id}/credentials"),
    ("GET", "/api/v1/capabilities"),
    ("POST", "/api/v1/capabilities"),
    ("GET", "/api/v1/capabilities/{capability_id}"),
    ("POST", "/api/v1/capabilities/{capability_id}/orders"),
    ("POST", "/api/v1/capabilities/{capability_id}/invoke"),
    ("GET", "/api/v1/demands"),
    ("POST", "/api/v1/demands"),
    ("GET", "/api/v1/demands/{demand_id}"),
    ("POST", "/api/v1/demands/{demand_id}/bids"),
    ("POST", "/api/v1/bids/{bid_id}/accept"),
    ("POST", "/api/v1/bids/{bid_id}/withdraw"),
    ("GET", "/api/v1/orders"),
    ("GET", "/api/v1/orders/{order_id}"),
    ("POST", "/api/v1/orders/{order_id}/confirm"),
    ("POST", "/api/v1/orders/{order_id}/reject"),
    ("POST", "/api/v1/orders/{order_id}/settlement-quote"),
    ("POST", "/api/v1/orders/{order_id}/fund"),
    ("POST", "/api/v1/orders/{order_id}/start"),
    ("POST", "/api/v1/orders/{order_id}/messages"),
    ("POST", "/api/v1/orders/{order_id}/deliver"),
    ("POST", "/api/v1/orders/{order_id}/accept"),
    ("POST", "/api/v1/orders/{order_id}/dispute"),
    ("POST", "/api/v1/orders/{order_id}/refund"),
    ("POST", "/api/v1/orders/{order_id}/resolve"),
    ("POST", "/api/v1/orders/{order_id}/reviews"),
    ("GET", "/api/v1/settlements/{settlement_id}"),
    ("POST", "/api/v1/integration/settlement-events"),
    ("POST", "/api/v1/webhooks"),
    ("GET", "/api/v1/notifications"),
    ("POST", "/api/v1/notifications/{notification_id}/read"),
}


def test_all_contract_routes_are_registered(client):
    actual = {
        (method, route.path)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
    }
    assert REQUIRED_ROUTES <= actual


def test_error_envelope_and_camel_case_contract(client):
    missing = client.get("/api/v1/capabilities/not-a-real-id")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "HTTP_404", "message": "Capability not found"}}

    capability = client.get("/api/v1/capabilities").json()["items"][0]
    assert "basePrice" in capability
    assert "base_price" not in capability
    assert isinstance(capability["basePrice"], str)
