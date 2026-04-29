# hive-swarm-signal-relay

Signal-relay-as-a-service. 100 autonomous agents emit prediction-market and asset-direction signals. Hive does not execute trades. Subscribers route execution through OKX, Coinbase, MetaMask, or any DEX of their choice.

**Wave D R2 reclassification** — previously `hive-swarm-trader`, reclassified 2026-04-29.

---

## Doctrine

```json
{
  "doctrine.never": [
    "execute_trades",
    "custody_funds",
    "route_orders",
    "hold_keys"
  ]
}
```

This service **emits signals only**. No wallets. No keys. No on-chain writes. Subscribers are solely responsible for execution.

---

## Endpoints

| Endpoint | Auth | Fee | Description |
|---|---|---|---|
| `GET /` | None | Free | Service identity + stats |
| `GET /.well-known/agent.json` | None | Free | A2A agent card + doctrine |
| `GET /v1/signals/stream` | X-Payment or Bearer | Free 10/min, then $0.001/burst | SSE stream of live signals |
| `POST /v1/signals/subscribe` | X-Payment | $50.00/mo | Monthly subscription gate |
| `GET /v1/signals/recent?limit=N` | X-Payment or Bearer | Free 10/min, then $0.005/req | Last N signals |
| `GET /v1/swarm/stats` | None | Free | Live swarm metrics |
| `GET /v1/receipt-test` | None | Free | Last 3 Spectral receipt IDs |

---

## Signal Schema

```json
{
  "signal_id":        "sig_abc123...",
  "signal_type":      "prediction_market | asset_direction | derivative_outlook | trust_momentum | compute_demand",
  "asset":            "BTC_USD | ETH_USD | SOL_USD | ...",
  "direction":        "long | short | yes | no | neutral",
  "confidence_score": 0.72,
  "timestamp":        "2026-04-29T19:00:00Z",
  "agent_did":        "did:hive:arbitragebot-001-2c74ab26c84c",
  "agent_name":       "ArbitrageBot-001",
  "rationale":        "overbought@67.3",
  "relay_only":       true
}
```

---

## x402 Payment

All gated endpoints use the [x402 protocol](https://x402.org).

- **Treasury:** `0x15184bf50b3d3f52b60434f8942b7d52f2eb436e` (Monroe W1, Base)
- **USDC contract:** `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913`
- **TRANSFER_TOPIC:** `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`
- Spectral receipt fires on every fee event → `https://hive-receipt.onrender.com/v1/receipt/sign`

---

## Agent Types

100 agents across 12 archetypes:
`ArbitrageBot`, `OracleHunter`, `RiskHedger`, `LiquidityMiner`, `PredictionPunter`,
`ZKTrader`, `ComputeBroker`, `LegalAgent`, `InsuranceAgent`, `TrustAuditor`,
`CapitalDeployer`, `SalvageHunter`

---

## Capabilities

`signal_emission` · `prediction_market_signals` · `asset_direction_signals` · `sse_stream` · `derivative_outlook_signals` · `trust_momentum_signals` · `compute_demand_signals`

---

Runs as a Render web service (Python, stdlib only).
