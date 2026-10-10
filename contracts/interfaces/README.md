# Settlement Contract Interfaces

These Solidity files define the future on-chain boundary only. They intentionally do
not implement custody, token transfers, CCIP router calls, or administrative policy.
Agent Market V1 continues to use the backend mock settlement adapter.

`IMarketplaceEscrow` mirrors the platform settlement states and reserves CAIP-10 and
CAIP-19 identifiers as strings for off-chain reconciliation. The ABI also uses native
addresses and integer token amounts for a future EVM implementation. API UUID values
must be normalized and hashed before use as `bytes32` identifiers; signed order terms
must be canonicalized before computing `termsHash`.

`ICCIPSettlementAdapter` separates cross-chain transport from escrow state. A future
implementation must at minimum verify the router, source chain selector, remote
sender, token allowlist, settlement identity, and message replay status before
releasing or crediting funds. CCIP failure requires retry or compensating refund logic;
it must not be represented as a database-style rollback.

Before production implementation, add:

- role-based administration and delayed configuration changes;
- reentrancy guards and safe ERC-20 transfer handling;
- token decimal, amount, route, and fee limits;
- source/destination sender allowlists and CCIP replay protection;
- pause, recovery, dispute, upgrade, and invariant tests;
- reconciliation between chain events and immutable platform order events.

