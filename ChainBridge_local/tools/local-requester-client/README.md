# Local Requester Client

Independent local requester for the four-chain Agent workflow experiment. It owns an encrypted EVM keystore and signs Chain A transactions locally. The Platform and Coordinator receive only the requester address and transaction evidence.

## Setup

```powershell
cd tools\local-requester-client
npm ci
Copy-Item .env.example .env
```

Set `COORDINATOR_API_TOKEN` in `.env`. `PLATFORM_API_TOKEN` is optional in local development because the client can use the seeded requester development identity. Set `REQUESTER_WALLET_PASSWORD` for automated experiments; interactive commands prompt without echoing the password.

## Interactive flow

```powershell
npm run requester -- wallet:create
npm run requester -- deployment:check
npm run requester -- faucet --token 100 --native 1
npm run requester -- plan --scenario scenario.example.json
npm run requester -- fund
npm run requester -- run
npm run requester -- monitor
npm run requester -- evidence
```

`fund` signs exactly three transactions: exact-budget ERC20 approval, `TaskEscrow.lockBudget`, and `GasTreasuryA.depositSourceGas`. It independently recomputes `termsHash`, checks the pinned deployment, waits for receipts, and then submits the three hashes to the Platform.

## Automated local experiment

```powershell
$env:REQUESTER_WALLET_PASSWORD = "use-a-long-local-test-password"
npm run requester -- experiment --scenario scenario.example.json --auto-confirm
```

`--auto-confirm` is rejected outside `LOCAL_EXPERIMENT`. Evidence is written to `outputs/`; wallet secrets, API tokens, runtime envelopes, and signatures are redacted.
