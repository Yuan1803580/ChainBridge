#!/bin/sh
set -e

# Agent Signer Sidecar entrypoint.
# - Auto-generates secrets when they are absent, so an operator can launch the
#   image with zero manual key provisioning ("one-click").
# - Runs DB migrations only when explicitly requested, so the managed compose
#   (which already runs `alembic upgrade head` in its own command) is unaffected.
# - Finally execs the provided command (Docker CMD or compose `command`).

# Loopback-only provisioning token (gates /v1/provision). Skipped when the
# operator already supplies one (e.g. managed deploy) -> behaviour unchanged.
if [ -z "${SIDECAR_PROVISIONING_TOKEN:-}" ]; then
  SIDECAR_PROVISIONING_TOKEN=$(python -c "import secrets; print(secrets.token_hex(24))")
  export SIDECAR_PROVISIONING_TOKEN
  echo "[sidecar-entrypoint] SIDECAR_PROVISIONING_TOKEN auto-generated (loopback /v1/provision only)"
fi

# Fernet storage key (encrypts persisted raw transactions). Must be a 32-byte
# url-safe base64 value; generate with cryptography, never a raw hex string.
if [ -z "${SIDECAR_STORAGE_KEY:-}" ]; then
  SIDECAR_STORAGE_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  export SIDECAR_STORAGE_KEY
  echo "[sidecar-entrypoint] SIDECAR_STORAGE_KEY auto-generated (Fernet)"
fi

if [ "${SIDECAR_RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "[sidecar-entrypoint] running: alembic upgrade head"
  alembic upgrade head
fi

exec "$@"
