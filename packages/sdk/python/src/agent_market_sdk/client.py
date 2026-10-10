from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from agent_market_sdk.errors import MarketplaceAPIError


JsonObject = dict[str, Any]
ChallengeSigner = Callable[[str], str]


class MarketplaceClient:
    """Synchronous Agent Market V1 client.

    Resource methods intentionally return API JSON without remapping fields. This
    keeps camelCase payloads identical across Python, TypeScript, and signed data.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "application/json",
                "User-Agent": "agent-market-sdk-python/0.1",
            },
        )

    @property
    def token(self) -> str | None:
        return self._token

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MarketplaceClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: JsonObject | None = None,
        params: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> JsonObject:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())

        response = self._client.request(
            method,
            path,
            json=json,
            params=params,
            headers=headers,
        )
        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = {"message": response.text or response.reason_phrase}
            detail = body.get("detail", body)
            if isinstance(detail, dict):
                message = str(
                    detail.get("message")
                    or detail.get("code")
                    or response.reason_phrase
                )
                code = detail.get("code")
            else:
                message = str(detail)
                code = body.get("code")
            raise MarketplaceAPIError(
                response.status_code,
                message,
                code=str(code) if code else None,
                details=body,
            )
        if response.status_code == 204 or not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise MarketplaceAPIError(
                response.status_code,
                "Expected a JSON object response",
                code="invalid_response",
                details=payload,
            )
        return payload

    # Identity
    def create_challenge(
        self, did: str, *, idempotency_key: str | None = None
    ) -> JsonObject:
        return self._request(
            "POST",
            "/auth/challenges",
            json={"did": did},
            idempotency_key=idempotency_key,
        )

    def verify_challenge(
        self,
        *,
        did: str,
        challenge_id: str,
        signature: str,
        verification_method: str | None = None,
        idempotency_key: str | None = None,
    ) -> JsonObject:
        body: JsonObject = {
            "did": did,
            "challengeId": challenge_id,
            "signature": signature,
        }
        if verification_method:
            body["verificationMethod"] = verification_method
        result = self._request(
            "POST",
            "/auth/verify",
            json=body,
            idempotency_key=idempotency_key,
        )
        token = result.get("accessToken") or result.get("token")
        if isinstance(token, str):
            self._token = token
        return result

    def login(
        self,
        did: str,
        signer: ChallengeSigner,
        *,
        verification_method: str | None = None,
    ) -> JsonObject:
        challenge = self.create_challenge(did)
        challenge_id = challenge.get("challengeId") or challenge.get("id")
        message = challenge.get("message") or challenge.get("canonicalMessage")
        if not isinstance(challenge_id, str) or not isinstance(message, str):
            raise MarketplaceAPIError(
                200,
                "Challenge response is missing challengeId or message",
                code="invalid_challenge_response",
                details=challenge,
            )
        return self.verify_challenge(
            did=did,
            challenge_id=challenge_id,
            signature=signer(message),
            verification_method=verification_method,
        )

    def get_did(self, did: str) -> JsonObject:
        return self._request("GET", f"/dids/{did}")

    def me(self) -> JsonObject:
        return self._request("GET", "/me")

    # Agents and capabilities
    def list_agents(self, **filters: Any) -> JsonObject:
        return self._request("GET", "/agents", params=filters)

    def get_agent(self, agent_id: str) -> JsonObject:
        return self._request("GET", f"/agents/{agent_id}")

    def get_agent_credentials(self, agent_id: str) -> JsonObject:
        return self._request("GET", f"/agents/{agent_id}/credentials")

    def list_capabilities(self, **filters: Any) -> JsonObject:
        return self._request("GET", "/capabilities", params=filters)

    def create_capability(
        self, payload: JsonObject, *, idempotency_key: str | None = None
    ) -> JsonObject:
        return self._request(
            "POST", "/capabilities", json=payload, idempotency_key=idempotency_key
        )

    def get_capability(self, capability_id: str) -> JsonObject:
        return self._request("GET", f"/capabilities/{capability_id}")

    def create_direct_order(
        self,
        capability_id: str,
        payload: JsonObject,
        *,
        idempotency_key: str | None = None,
    ) -> JsonObject:
        return self._request(
            "POST",
            f"/capabilities/{capability_id}/orders",
            json=payload,
            idempotency_key=idempotency_key,
        )

    def invoke_capability(
        self,
        capability_id: str,
        input_data: JsonObject,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> JsonObject:
        body: JsonObject = {"input": input_data}
        if request_id:
            body["requestId"] = request_id
        return self._request(
            "POST",
            f"/capabilities/{capability_id}/invoke",
            json=body,
            idempotency_key=idempotency_key,
        )

    # Demands and bids
    def list_demands(self, **filters: Any) -> JsonObject:
        return self._request("GET", "/demands", params=filters)

    def create_demand(
        self, payload: JsonObject, *, idempotency_key: str | None = None
    ) -> JsonObject:
        return self._request(
            "POST", "/demands", json=payload, idempotency_key=idempotency_key
        )

    def get_demand(self, demand_id: str) -> JsonObject:
        return self._request("GET", f"/demands/{demand_id}")

    def create_bid(
        self,
        demand_id: str,
        payload: JsonObject,
        *,
        idempotency_key: str | None = None,
    ) -> JsonObject:
        return self._request(
            "POST",
            f"/demands/{demand_id}/bids",
            json=payload,
            idempotency_key=idempotency_key,
        )

    def accept_bid(
        self, bid_id: str, *, idempotency_key: str | None = None
    ) -> JsonObject:
        return self._request(
            "POST", f"/bids/{bid_id}/accept", json={}, idempotency_key=idempotency_key
        )

    def withdraw_bid(
        self, bid_id: str, *, idempotency_key: str | None = None
    ) -> JsonObject:
        return self._request(
            "POST", f"/bids/{bid_id}/withdraw", json={}, idempotency_key=idempotency_key
        )

    # Orders
    def list_orders(self, **filters: Any) -> JsonObject:
        return self._request("GET", "/orders", params=filters)

    def get_order(self, order_id: str) -> JsonObject:
        return self._request("GET", f"/orders/{order_id}")

    def _order_action(
        self,
        order_id: str,
        action: str,
        payload: JsonObject | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> JsonObject:
        return self._request(
            "POST",
            f"/orders/{order_id}/{action}",
            json=payload or {},
            idempotency_key=idempotency_key,
        )

    def fund_order(
        self, order_id: str, payload: JsonObject | None = None, **kwargs: Any
    ) -> JsonObject:
        return self._order_action(order_id, "fund", payload, **kwargs)

    def start_order(self, order_id: str, **kwargs: Any) -> JsonObject:
        return self._order_action(order_id, "start", **kwargs)

    def send_order_message(
        self, order_id: str, payload: JsonObject, **kwargs: Any
    ) -> JsonObject:
        return self._order_action(order_id, "messages", payload, **kwargs)

    def deliver_order(
        self, order_id: str, payload: JsonObject, **kwargs: Any
    ) -> JsonObject:
        return self._order_action(order_id, "deliver", payload, **kwargs)

    def accept_order(self, order_id: str, **kwargs: Any) -> JsonObject:
        return self._order_action(order_id, "accept", **kwargs)

    def dispute_order(
        self, order_id: str, payload: JsonObject, **kwargs: Any
    ) -> JsonObject:
        return self._order_action(order_id, "dispute", payload, **kwargs)

    def refund_order(
        self, order_id: str, payload: JsonObject | None = None, **kwargs: Any
    ) -> JsonObject:
        return self._order_action(order_id, "refund", payload, **kwargs)

    def review_order(
        self, order_id: str, payload: JsonObject, **kwargs: Any
    ) -> JsonObject:
        return self._order_action(order_id, "reviews", payload, **kwargs)

    # Integration
    def get_settlement(self, settlement_id: str) -> JsonObject:
        return self._request("GET", f"/settlements/{settlement_id}")

    def create_webhook(
        self, payload: JsonObject, *, idempotency_key: str | None = None
    ) -> JsonObject:
        return self._request(
            "POST", "/webhooks", json=payload, idempotency_key=idempotency_key
        )

    def list_notifications(self, **filters: Any) -> JsonObject:
        return self._request("GET", "/notifications", params=filters)

    def mark_notification_read(
        self, notification_id: str, *, idempotency_key: str | None = None
    ) -> JsonObject:
        return self._request(
            "POST",
            f"/notifications/{notification_id}/read",
            json={},
            idempotency_key=idempotency_key,
        )
