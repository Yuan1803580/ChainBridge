from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from .config import Settings


NATIVE_ETH = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class DexQuoteError(ValueError):
    pass


class SwapVerificationError(ValueError):
    pass


def to_base_units(value: Decimal, decimals: int) -> str:
    scaled = value * (Decimal(10) ** decimals)
    if scaled != scaled.to_integral_value():
        raise DexQuoteError(f"Amount has more than {decimals} decimal places")
    return str(int(scaled))


class ZeroExClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def quote(
        self,
        *,
        chain_id: int,
        taker: str,
        sell_token: str,
        buy_token: str,
        slippage_bps: int,
        sell_amount: str | None = None,
        buy_amount: str | None = None,
    ) -> dict[str, Any]:
        if self.settings.settlement_mode == "external_ccip" and not self.settings.zero_ex_api_key:
            raise DexQuoteError("ZERO_EX_API_KEY is required for external DEX quotes")
        if self.settings.settlement_mode == "external_ccip" and not self.settings.zero_ex_allowed_targets:
            raise DexQuoteError("ZERO_EX_ALLOWED_TARGETS is required for external DEX quotes")
        if self.settings.settlement_mode == "external_ccip" and not self.settings.zero_ex_allowed_selectors:
            raise DexQuoteError("ZERO_EX_ALLOWED_SELECTORS is required for external DEX quotes")
        if (
            self.settings.settlement_mode == "external_ccip"
            and sell_token.lower() != NATIVE_ETH
            and not self.settings.zero_ex_allowed_allowance_targets
        ):
            raise DexQuoteError("ZERO_EX_ALLOWED_ALLOWANCE_TARGETS is required for ERC-20 DEX quotes")
        if bool(sell_amount) == bool(buy_amount):
            raise DexQuoteError("Exactly one of sellAmount or buyAmount is required")
        for token in (sell_token, buy_token):
            if token.lower() != NATIVE_ETH and (not token.startswith("0x") or len(token) != 42):
                raise DexQuoteError("DEX token address is invalid")
        params: dict[str, str | int] = {
            "chainId": chain_id,
            "taker": taker,
            "recipient": taker,
            "sellToken": sell_token,
            "buyToken": buy_token,
            "slippageBps": slippage_bps,
        }
        params["sellAmount" if sell_amount else "buyAmount"] = sell_amount or buy_amount or ""
        headers = {"0x-version": "v2"}
        if self.settings.zero_ex_api_key:
            headers["0x-api-key"] = self.settings.zero_ex_api_key
        try:
            with httpx.Client(timeout=15, transport=self.transport) as client:
                response = client.get(
                    f"{self.settings.zero_ex_api_url}/swap/allowance-holder/quote",
                    params=params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise DexQuoteError("0x quote service is unavailable") from exc
        if response.status_code >= 400:
            detail = response.text[:500]
            if response.status_code in {400, 404, 422}:
                raise DexQuoteError(f"no_liquidity: {detail}")
            raise DexQuoteError(f"0x quote failed with HTTP {response.status_code}")
        payload = response.json()
        if payload.get("liquidityAvailable") is False:
            raise DexQuoteError("no_liquidity")
        return self._validate(payload, chain_id, taker, sell_token, buy_token)

    def _validate(
        self,
        payload: dict[str, Any],
        chain_id: int,
        taker: str,
        sell_token: str,
        buy_token: str,
    ) -> dict[str, Any]:
        transaction = payload.get("transaction")
        if not isinstance(transaction, dict):
            raise DexQuoteError("0x response is missing transaction data")
        target = str(transaction.get("to", "")).lower()
        data = str(transaction.get("data", ""))
        if not target.startswith("0x") or len(target) != 42 or not data.startswith("0x") or len(data) < 10:
            raise DexQuoteError("0x returned malformed transaction data")
        allowed = self.settings.zero_ex_allowed_targets
        if allowed and target not in allowed:
            raise DexQuoteError("0x returned an unapproved transaction target")
        selector = data[:10].lower()
        if self.settings.zero_ex_allowed_selectors and selector not in self.settings.zero_ex_allowed_selectors:
            raise DexQuoteError("0x returned an unapproved calldata selector")
        for field in ("sellAmount", "buyAmount", "minBuyAmount"):
            value = str(payload.get(field, ""))
            if not value.isdigit() or int(value) <= 0:
                raise DexQuoteError(f"0x response has invalid {field}")
        allowance = payload.get("allowanceTarget")
        issues = payload.get("issues")
        if allowance is None and isinstance(issues, dict) and isinstance(issues.get("allowance"), dict):
            allowance = issues["allowance"].get("spender")
        if sell_token.lower() != NATIVE_ETH and not allowance:
            raise DexQuoteError("0x response is missing the ERC-20 allowance target")
        if (
            allowance
            and self.settings.zero_ex_allowed_allowance_targets
            and str(allowance).lower() not in self.settings.zero_ex_allowed_allowance_targets
        ):
            raise DexQuoteError("0x returned an unapproved allowance target")
        expected_fields = {
            "chainId": str(chain_id),
            "taker": taker.lower(),
            "recipient": taker.lower(),
            "sellToken": sell_token.lower(),
            "buyToken": buy_token.lower(),
        }
        for field, expected in expected_fields.items():
            if field not in payload:
                continue
            actual = str(payload[field]).lower()
            if actual != expected.lower():
                raise DexQuoteError(f"0x response {field} does not match the requested route")
        for field in ("value", "gas"):
            if not str(transaction.get(field, "0")).isdigit():
                raise DexQuoteError(f"0x response has invalid transaction {field}")
        normalized_tx = {
            "to": target,
            "data": data,
            "value": str(transaction.get("value", "0")),
            "gas": str(transaction.get("gas", "0")),
        }
        if "gasPrice" in transaction:
            normalized_tx["gasPrice"] = str(transaction["gasPrice"])
        return {
            "provider": "0x",
            "chainId": chain_id,
            "taker": taker.lower(),
            "recipient": taker.lower(),
            "sellToken": sell_token.lower(),
            "buyToken": buy_token.lower(),
            "sellAmount": str(payload["sellAmount"]),
            "buyAmount": str(payload["buyAmount"]),
            "minBuyAmount": str(payload["minBuyAmount"]),
            "allowanceTarget": str(allowance).lower() if allowance else None,
            "transaction": normalized_tx,
            "expiresAt": (
                datetime.now(timezone.utc) + timedelta(seconds=self.settings.zero_ex_quote_ttl_seconds)
            ),
        }


class EvmRpcVerifier:
    def __init__(
        self,
        rpc_url: str,
        chain_id: int,
        transport: httpx.BaseTransport | None = None,
    ):
        self.rpc_url = rpc_url
        self.chain_id = chain_id
        self.transport = transport

    def _call(self, method: str, params: list[Any]) -> Any:
        if not self.rpc_url:
            raise SwapVerificationError("Source RPC is required to verify the funding swap")
        try:
            with httpx.Client(timeout=15, transport=self.transport) as client:
                response = client.post(
                    self.rpc_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SwapVerificationError("Source RPC is unavailable") from exc
        payload = response.json()
        if payload.get("error"):
            raise SwapVerificationError(f"Source RPC rejected {method}")
        return payload.get("result")

    def validate_configuration(self) -> None:
        chain_id = self._call("eth_chainId", [])
        if chain_id is None or int(chain_id, 16) != self.chain_id:
            raise SwapVerificationError("Source RPC chain ID does not match configuration")

    def verify_swap(self, quote: Any, transaction_hash: str) -> None:
        self.validate_configuration()
        transaction = self._call("eth_getTransactionByHash", [transaction_hash])
        receipt = self._call("eth_getTransactionReceipt", [transaction_hash])
        if not isinstance(transaction, dict) or not isinstance(receipt, dict):
            raise SwapVerificationError("Funding swap transaction is not confirmed")
        if str(receipt.get("status", "")).lower() != "0x1":
            raise SwapVerificationError("Funding swap transaction reverted")
        if str(transaction.get("from", "")).lower() != quote.taker.lower():
            raise SwapVerificationError("Funding swap sender does not match quote taker")
        expected = quote.transaction
        if str(transaction.get("to", "")).lower() != str(expected["to"]).lower():
            raise SwapVerificationError("Funding swap target does not match quote")
        if str(transaction.get("input", "")).lower() != str(expected["data"]).lower():
            raise SwapVerificationError("Funding swap calldata does not match quote")
        value = int(str(transaction.get("value", "0x0")), 16)
        if value != int(str(expected["value"])) or value > int(quote.max_sell_amount):
            raise SwapVerificationError("Funding swap value exceeds the selected quote")
        buy_token = str(quote.buy_token).lower()
        if buy_token != NATIVE_ETH:
            recipient_topic = "0x" + quote.taker.lower().removeprefix("0x").rjust(64, "0")
            received = 0
            for log in receipt.get("logs", []):
                topics = [str(topic).lower() for topic in log.get("topics", [])]
                if (
                    str(log.get("address", "")).lower() == buy_token
                    and len(topics) >= 3
                    and topics[0] == TRANSFER_TOPIC
                    and topics[2] == recipient_topic
                ):
                    received += int(str(log.get("data", "0x0")), 16)
            if received < int(quote.min_buy_amount):
                raise SwapVerificationError("Swap receipt does not prove the minimum token output")
