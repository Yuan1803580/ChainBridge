from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Agent


class DIDResolver(ABC):
    @abstractmethod
    def resolve(self, did: str) -> dict[str, Any] | None:
        raise NotImplementedError


class LocalDIDResolver(DIDResolver):
    def __init__(self, session: Session):
        self.session = session

    def resolve(self, did: str) -> dict[str, Any] | None:
        agent = self.session.scalar(select(Agent).where(Agent.did == did))
        if agent is None:
            return None
        key_id = f"{did}#operational-key-1"
        services = []
        if agent.endpoint:
            services.append({"id": f"{did}#agent-api", "type": "AgentService", "serviceEndpoint": agent.endpoint})
        return {
            "@context": ["https://www.w3.org/ns/did/v1"],
            "id": did,
            "verificationMethod": [
                {
                    "id": key_id,
                    "type": "EcdsaSecp256k1VerificationKey2019",
                    "controller": did,
                    "publicKeyHex": agent.operational_public_key,
                }
            ],
            "authentication": [key_id],
            "assertionMethod": [key_id],
            "service": services,
        }

