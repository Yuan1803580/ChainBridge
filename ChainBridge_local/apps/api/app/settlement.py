from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from .chain_protocol import opaque_id_to_bytes32, quote_terms_hash
from .models import Order, Settlement
from .services import canonical_hash


QUOTE_RATE = Decimal("2000")
QUOTE_TTL_SECONDS = 15 * 60


def _decimal_string(value: Decimal, places: int | None = None) -> str:
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places))
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class SettlementAdapter(ABC):
    @abstractmethod
    def create_intent(self, session: Session, order: Order, details: dict[str, Any] | None = None) -> Settlement:
        raise NotImplementedError

    @abstractmethod
    def quote_settlement(self, session: Session, order: Order, details: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def lock_funds(
        self,
        session: Session,
        intent: Settlement,
        order: Order | None = None,
        details: dict[str, Any] | None = None,
    ) -> Settlement:
        raise NotImplementedError

    @abstractmethod
    def get_status(self, session: Session, intent_id: str) -> Settlement | None:
        raise NotImplementedError

    @abstractmethod
    def release_funds(
        self, session: Session, intent: Settlement, payable_amount: Decimal | None = None
    ) -> Settlement:
        raise NotImplementedError

    @abstractmethod
    def refund(self, session: Session, intent: Settlement) -> Settlement:
        raise NotImplementedError

    @abstractmethod
    def handle_webhook(self, session: Session, payload: dict[str, Any]) -> None:
        raise NotImplementedError


class MockSettlementAdapter(SettlementAdapter):
    def create_intent(self, session: Session, order: Order, details: dict[str, Any] | None = None) -> Settlement:
        details = details or {}
        intent = Settlement(
            order_id=order.id,
            amount=order.amount,
            currency=order.currency,
            status="created",
            payer_account=details.get("payerAccount"),
            payee_account=details.get("payeeAccount"),
            asset_id=details.get("assetId"),
            source_chain=details.get("sourceChain"),
            destination_chain=details.get("destinationChain"),
        )
        session.add(intent)
        session.flush()
        order.settlement_id = intent.id
        return intent

    def quote_settlement(self, session: Session, order: Order, details: dict[str, Any]) -> dict[str, Any]:
        source_amount = Decimal(str(details["source_amount"]))
        max_slippage_bps = int(details["max_slippage_bps"])
        settlement_decimals = int(details["settlement_decimals"])
        quote_deadline = datetime.now(timezone.utc) + timedelta(seconds=QUOTE_TTL_SECONDS)
        quoted_amount = source_amount * QUOTE_RATE
        min_amount = quoted_amount * Decimal(10000 - max_slippage_bps) / Decimal(10000)
        quote_payload = {
            "sourceAsset": details["source_asset"],
            "settlementToken": details["settlement_token"],
            "sourceAmount": _decimal_string(source_amount),
            "quotedSettlementAmount": _decimal_string(quoted_amount, settlement_decimals),
            "minSettlementAmount": _decimal_string(min_amount, settlement_decimals),
            "maxSlippageBps": max_slippage_bps,
            "quoteDeadline": quote_deadline.isoformat().replace("+00:00", "Z"),
            "rate": _decimal_string(QUOTE_RATE),
        }
        quote_hash = canonical_hash(
            {
                **quote_payload,
                "sourceDecimals": int(details["source_decimals"]),
                "settlementDecimals": settlement_decimals,
            }
        )
        previous_quote = (order.terms_snapshot or {}).get("settlementQuote")
        base_terms_hash = (
            previous_quote.get("baseTermsHash")
            if isinstance(previous_quote, dict) and previous_quote.get("baseTermsHash")
            else order.terms_hash
        )
        onchain_order_id = opaque_id_to_bytes32(order.id)
        quote_payload["quoteHash"] = quote_hash
        quote_payload["baseTermsHash"] = base_terms_hash
        quote_payload["onchainOrderId"] = onchain_order_id
        quote_payload["termsHash"] = quote_terms_hash(onchain_order_id, base_terms_hash, quote_hash)
        order.terms_snapshot = {**order.terms_snapshot, "settlementQuote": quote_payload}
        order.terms_hash = quote_payload["termsHash"]
        session.flush()
        return quote_payload

    def lock_funds(
        self,
        session: Session,
        intent: Settlement,
        order: Order | None = None,
        details: dict[str, Any] | None = None,
    ) -> Settlement:
        if intent.status != "created":
            raise ValueError("Settlement must be created before funds can be locked")
        details = details or {}
        quote_hash = details.get("quote_hash")
        if quote_hash is not None:
            if order is None:
                raise ValueError("Order is required for quote-protected funding")
            stored_quote = (order.terms_snapshot or {}).get("settlementQuote")
            if not isinstance(stored_quote, dict):
                raise ValueError("Settlement quote must be created before quote-protected funding")
            supplied_deadline = details.get("quote_deadline")
            stored_deadline_text = stored_quote.get("quoteDeadline")
            if supplied_deadline is None or not isinstance(stored_deadline_text, str):
                raise ValueError("Quote deadline is required")
            stored_deadline = datetime.fromisoformat(stored_deadline_text.replace("Z", "+00:00"))
            if _aware(supplied_deadline) != _aware(stored_deadline):
                raise ValueError("Quote deadline does not match the order terms")
            if _aware(stored_deadline) <= datetime.now(timezone.utc):
                raise ValueError("Settlement quote has expired")
            if quote_hash != stored_quote.get("quoteHash"):
                raise ValueError("Settlement quote hash does not match the order terms")
            if details.get("quoted_settlement_amount") != stored_quote.get("quotedSettlementAmount"):
                raise ValueError("Quoted settlement amount does not match the order terms")
            if details.get("min_settlement_amount") != stored_quote.get("minSettlementAmount"):
                raise ValueError("Minimum settlement amount does not match the order terms")
            if details.get("max_slippage_bps") != stored_quote.get("maxSlippageBps"):
                raise ValueError("Max slippage does not match the order terms")
            if Decimal(str(intent.amount)) < Decimal(str(details["min_settlement_amount"])):
                raise ValueError("Locked settlement amount is below the quote minimum")

        intent.status = "locked"
        intent.confirmation_count = 1
        intent.transaction_hash = canonical_hash(
            {
                "type": "mock-lock",
                "settlementId": intent.id,
                "orderId": intent.order_id,
                "quoteHash": quote_hash,
                "amount": _decimal_string(Decimal(str(intent.amount))),
            }
        )
        return intent

    def get_status(self, session: Session, intent_id: str) -> Settlement | None:
        return session.get(Settlement, intent_id)

    def release_funds(
        self, session: Session, intent: Settlement, payable_amount: Decimal | None = None
    ) -> Settlement:
        if intent.status != "locked":
            raise ValueError("Settlement must be locked before release")
        payable_amount = Decimal(str(intent.amount)) if payable_amount is None else Decimal(str(payable_amount))
        if payable_amount <= 0 or payable_amount > Decimal(str(intent.amount)):
            raise ValueError("Payable amount must be greater than zero and no more than the locked budget")
        intent.status = "release_pending"
        session.flush()
        intent.ccip_message_id = canonical_hash(
            {
                "type": "mock-ccip-release",
                "settlementId": intent.id,
                "orderId": intent.order_id,
                "amount": _decimal_string(payable_amount),
            }
        )
        intent.status = "released"
        return intent

    def refund(self, session: Session, intent: Settlement) -> Settlement:
        if intent.status not in {"locked", "refund_pending"}:
            raise ValueError("Only locked funds can be refunded")
        intent.status = "refund_pending"
        session.flush()
        intent.status = "refunded"
        return intent

    def handle_webhook(self, session: Session, payload: dict[str, Any]) -> None:
        return None


class ExternalCCIPSettlementAdapter(MockSettlementAdapter):
    """Control-plane adapter; wallets/relayers execute transactions and report verified events."""

    def quote_settlement(self, session: Session, order: Order, details: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("Fixed-rate settlement quotes are disabled; use the order dex-quotes endpoint")

    def lock_funds(
        self,
        session: Session,
        intent: Settlement,
        order: Order | None = None,
        details: dict[str, Any] | None = None,
    ) -> Settlement:
        super().lock_funds(session, intent, order, details)
        intent.status = "lock_pending"
        intent.confirmation_count = 0
        intent.transaction_hash = None
        return intent

    def release_funds(
        self, session: Session, intent: Settlement, payable_amount: Decimal | None = None
    ) -> Settlement:
        if intent.status != "locked":
            raise ValueError("Settlement must be locked before release")
        payable_amount = Decimal(str(intent.amount)) if payable_amount is None else Decimal(str(payable_amount))
        if payable_amount <= 0 or payable_amount > Decimal(str(intent.amount)):
            raise ValueError("Payable amount must be greater than zero and no more than the locked budget")
        intent.status = "release_pending"
        intent.ccip_message_id = None
        return intent

    def refund(self, session: Session, intent: Settlement) -> Settlement:
        if intent.status not in {"locked", "refund_pending"}:
            raise ValueError("Only locked funds can be refunded")
        intent.status = "refund_pending"
        return intent
