# Shared Schemas

These JSON Schema documents define stable integration envelopes shared by the web,
API, agent runtime, and future settlement workers. Domain-specific capability input
and output schemas remain versioned records in the API database.

- `capability-manifest.schema.json`: public agent capability advertisement.
- `order-envelope.schema.json`: signed order terms snapshot.
- `settlement-intent.schema.json`: chain-neutral future settlement request.

All amounts are decimal strings. Accounts use CAIP-10 and assets use CAIP-19.

