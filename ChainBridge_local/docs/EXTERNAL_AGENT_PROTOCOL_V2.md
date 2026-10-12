# External Agent HTTP Protocol V2

V2 standardizes independently deployed execution Agents without removing the
existing V1 contract. A registration may continue to use V1 when its manifest
does not contain `protocolVersion`.

## Required relative endpoints

The registered Agent endpoint is the base for:

- `GET /health`
- `GET /manifest`
- `GET /schemas/input`
- `GET /schemas/output`
- `POST /task-offers`
- `POST /invoke`
- `POST /chat` when conversation is supported
- `GET /artifacts`
- `GET /artifacts/{artifactId}`

## V2 invariants

- `protocolVersion` is `2.0`.
- JSON hashing and ES256K signatures use RFC 8785 JCS bytes.
- The signed result contains the protocol version, Agent identity, capability,
  request identifier, completion time, output hash and complete workflow
  context when the invocation belongs to a workflow.
- The absolute UTC `deadline` controls timeout semantics. A late result cannot
  extend its own deadline.
- Errors use `{protocolVersion,error:{code,message,retryable,details,path}}`.
- Artifact downloads are limited to Runner-controlled workspace roots and do
  not expose absolute host paths.

## Compatibility

The Coordinator advertises `Accept-Agent-Protocol: 2.0`. For a V2 manifest it
sends `X-Agent-Protocol-Version: 2.0` and verifies that the same version is in
the signed result. A missing version is treated as V1 during migration.
