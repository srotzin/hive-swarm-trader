"""
Hive Swarm Trader — 42 sovereign agents trading live on HiveExchange + HiveTransactions
Runs continuously, agents act autonomously based on their capability type
"""
import urllib.request, urllib.error, json, time, random, threading, sys
from datetime import datetime, timezone

EXCHANGE_BASE  = "https://hiveexchange-service.onrender.com"
GATE_BASE      = "https://hivegate.onrender.com"
TXNS_BASE      = "https://hivetransactions.onrender.com"
CAPITAL_BASE   = "https://hivecapital.onrender.com"
LAW_BASE       = "https://hivelaw.onrender.com"
INTERNAL_KEY   = "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46"

def post(url, body, headers={}):
    try:
        data = json.dumps(body).encode()
        h = {"Content-Type": "application/json", **headers}
        req = urllib.request.Request(url, data=data, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return {"error": str(e)}, e.code
    except Exception as e:
        return {"error": str(e)}, 0

def get(url, headers={}):
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            return json.loads(r.read()), r.status
    except Exception as e:
        return {"error": str(e)}, 0

# Agents embedded inline (42 sovereign genesis agents)
AGENTS = [
  {
    "name": "ArbitrageBot-001",
    "did": "did:hive:arbitragebot-001-2c74ab26c84c",
    "caps": [
      "arbitrage",
      "perp_trading",
      "market_making"
    ]
  },
  {
    "name": "ComputeBroker-002",
    "did": "did:hive:computebroker-002-a767d71b2c7f",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "ZKTrader-003",
    "did": "did:hive:zktrader-003-82d3f61a6519",
    "caps": [
      "zk_proofs",
      "private_settlement",
      "aleo"
    ]
  },
  {
    "name": "ComputeBroker-004",
    "did": "did:hive:computebroker-004-26f624d7c54e",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "RiskHedger-005",
    "did": "did:hive:riskhedger-005-4173e042bdeb",
    "caps": [
      "derivatives",
      "hedging",
      "risk_management"
    ]
  },
  {
    "name": "RiskHedger-006",
    "did": "did:hive:riskhedger-006-f9b2d5b70657",
    "caps": [
      "derivatives",
      "hedging",
      "risk_management"
    ]
  },
  {
    "name": "ComputeBroker-007",
    "did": "did:hive:computebroker-007-11be30ac609c",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "PredictionPunter-008",
    "did": "did:hive:predictionpunter-008-278c2395607a",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "RiskHedger-009",
    "did": "did:hive:riskhedger-009-3b0a86193c46",
    "caps": [
      "derivatives",
      "hedging",
      "risk_management"
    ]
  },
  {
    "name": "LiquidityMiner-010",
    "did": "did:hive:liquidityminer-010-87590278b13d",
    "caps": [
      "amm",
      "liquidity",
      "yield_farming"
    ]
  },
  {
    "name": "ComputeBroker-013",
    "did": "did:hive:computebroker-013-9fb0214caf15",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "PredictionPunter-014",
    "did": "did:hive:predictionpunter-014-a08bedfb76bd",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "RiskHedger-015",
    "did": "did:hive:riskhedger-015-94161f233068",
    "caps": [
      "derivatives",
      "hedging",
      "risk_management"
    ]
  },
  {
    "name": "ComputeBroker-016",
    "did": "did:hive:computebroker-016-062419596c08",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "ArbitrageBot-017",
    "did": "did:hive:arbitragebot-017-843bc502b751",
    "caps": [
      "arbitrage",
      "perp_trading",
      "market_making"
    ]
  },
  {
    "name": "OracleHunter-018",
    "did": "did:hive:oraclehunter-018-850425b56479",
    "caps": [
      "oracle",
      "data_feed",
      "price_discovery"
    ]
  },
  {
    "name": "ComputeBroker-019",
    "did": "did:hive:computebroker-019-853a20b5e137",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "RiskHedger-020",
    "did": "did:hive:riskhedger-020-8e212827b737",
    "caps": [
      "derivatives",
      "hedging",
      "risk_management"
    ]
  },
  {
    "name": "ArbitrageBot-021",
    "did": "did:hive:arbitragebot-021-21d77f8d1aee",
    "caps": [
      "arbitrage",
      "perp_trading",
      "market_making"
    ]
  },
  {
    "name": "PredictionPunter-022",
    "did": "did:hive:predictionpunter-022-db8476c42893",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "ComputeBroker-036",
    "did": "did:hive:computebroker-036-8e5e59cd0f5d",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "ZKTrader-037",
    "did": "did:hive:zktrader-037-1f540a9a7d3f",
    "caps": [
      "zk_proofs",
      "private_settlement",
      "aleo"
    ]
  },
  {
    "name": "PredictionPunter-038",
    "did": "did:hive:predictionpunter-038-62b7721e3b60",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "LiquidityMiner-039",
    "did": "did:hive:liquidityminer-039-4d248fd77c8e",
    "caps": [
      "amm",
      "liquidity",
      "yield_farming"
    ]
  },
  {
    "name": "LiquidityMiner-040",
    "did": "did:hive:liquidityminer-040-12c9afc8a3b5",
    "caps": [
      "amm",
      "liquidity",
      "yield_farming"
    ]
  },
  {
    "name": "ComputeBroker-041",
    "did": "did:hive:computebroker-041-04c5e0c660a9",
    "caps": [
      "compute_routing",
      "inference",
      "llm_arbitrage"
    ]
  },
  {
    "name": "PredictionPunter-042",
    "did": "did:hive:predictionpunter-042-fde04b4fd52d",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "OracleHunter-011",
    "did": "did:hive:oraclehunter-011-b817e1f8c44a",
    "caps": [
      "oracle",
      "data_feed",
      "price_discovery"
    ]
  },
  {
    "name": "SalvageHunter-012",
    "did": "did:hive:salvagehunter-012-efe241a003c0",
    "caps": [
      "failed_tx_rescue",
      "salvage_market",
      "bounty_hunting"
    ]
  },
  {
    "name": "PredictionPunter-023",
    "did": "did:hive:predictionpunter-023-85736ffb5e01",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "RiskHedger-024",
    "did": "did:hive:riskhedger-024-2c256176f592",
    "caps": [
      "derivatives",
      "hedging",
      "risk_management"
    ]
  },
  {
    "name": "PredictionPunter-025",
    "did": "did:hive:predictionpunter-025-6227c1f69dc3",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "ArbitrageBot-026",
    "did": "did:hive:arbitragebot-026-087db37269a3",
    "caps": [
      "arbitrage",
      "perp_trading",
      "market_making"
    ]
  },
  {
    "name": "PredictionPunter-027",
    "did": "did:hive:predictionpunter-027-b043bb1e6add",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "PredictionPunter-028",
    "did": "did:hive:predictionpunter-028-17f18c2d6d37",
    "caps": [
      "prediction_markets",
      "sentiment",
      "forecasting"
    ]
  },
  {
    "name": "ArbitrageBot-029",
    "did": "did:hive:arbitragebot-029-cba6fd523131",
    "caps": [
      "arbitrage",
      "perp_trading",
      "market_making"
    ]
  },
  {
    "name": "LiquidityMiner-030",
    "did": "did:hive:liquidityminer-030-c03aa41de2a6",
    "caps": [
      "amm",
      "liquidity",
      "yield_farming"
    ]
  },
  {
    "name": "OracleHunter-031",
    "did": "did:hive:oraclehunter-031-06713cbc10f1",
    "caps": [
      "oracle",
      "data_feed",
      "price_discovery"
    ]
  },
  {
    "name": "OracleHunter-032",
    "did": "did:hive:oraclehunter-032-6ed9ad50002c",
    "caps": [
      "oracle",
      "data_feed",
      "price_discovery"
    ]
  },
  {
    "name": "LegalAgent-033",
    "did": "did:hive:legalagent-033-b57cb4ac26ee",
    "caps": [
      "hivelaw",
      "contract_enforcement",
      "dispute_resolution"
    ]
  },
  {
    "name": "InsuranceAgent-034",
    "did": "did:hive:insuranceagent-034-6efdd885a953",
    "caps": [
      "risk_underwriting",
      "hedge_claims",
      "insurance"
    ]
  },
  {
    "name": "CapitalDeployer-035",
    "did": "did:hive:capitaldeployer-035-316ab43a8b55",
    "caps": [
      "equity_staking",
      "agent_vc",
      "capital_formation"
    ]
  }
]

# Fetch open prediction markets
def get_markets(limit=20):
    data, _ = get(f"{EXCHANGE_BASE}/v1/exchange/predict/markets?limit={limit}")
    return data.get("data", {}).get("markets", [])

# Agent action functions by capability type
def action_prediction(agent):
    markets = get_markets(10)
    if not markets: return "no_markets"
    m = random.choice(markets)
    side = random.choice(["yes","no"])
    amount = round(random.uniform(5, 150), 2)
    result, status = post(f"{EXCHANGE_BASE}/v1/exchange/predict/bet", {
        "market_id": m["id"],
        "position": side,
        "amount_usdc": amount,
        "agent_did": agent["did"],
        "settlement_rail": random.choice(["USDC","ALEO","USDCx"])
    })
    return f"bet {side} ${amount} on market {m['id'][:8]} → {status}"

def action_perp(agent):
    signals = ["compute_demand","agent_volume","trust_velocity","settlement_flow","zk_proof_rate"]
    signal = random.choice(signals)
    side = random.choice(["long","short"])
    size = round(random.uniform(10, 500), 2)
    result, status = post(f"{EXCHANGE_BASE}/v1/exchange/perp/open", {
        "agent_did": agent["did"],
        "signal": signal,
        "side": side,
        "size_usdc": size,
        "leverage": random.choice([1,2,3,5]),
        "settlement_rail": random.choice(["USDC","ALEO"])
    })
    return f"perp {side} ${size} on {signal} → {status}"

def action_derivative(agent):
    result, status = post(f"{EXCHANGE_BASE}/v1/exchange/derivative/create", {
        "agent_did": agent["did"],
        "underlying": random.choice(["compute_cost","agent_uptime","settlement_latency","trust_score"]),
        "instrument": random.choice(["call","put","swap","collar"]),
        "notional": round(random.uniform(100, 2000), 2),
        "expiry_days": random.choice([1,3,7,14,30]),
        "strike_offset_pct": round(random.uniform(-20, 20), 1),
        "settlement_rail": "USDC"
    })
    return f"derivative {result.get('instrument','?')} → {status}"

def action_transaction_intent(agent):
    intent_types = ["compute_purchase","data_access","settlement","labor","storage"]
    result, status = post(f"{TXNS_BASE}/v1/transaction/intent", {
        "agent_did": agent["did"],
        "intent_type": random.choice(intent_types),
        "notional": round(random.uniform(50, 3000), 2),
        "deadline_seconds": random.randint(60, 600),
        "privacy": random.choice(["public","public","public","sealed","dark"]),
        "priority": random.choice(["standard","standard","fast","guaranteed"])
    })
    tx_id = result.get("tx_intent_id")
    if tx_id:
        # Also bid on someone else's intent
        all_intents, _ = get(f"{TXNS_BASE}/v1/transaction/intents?limit=5")
        open_intents = all_intents.get("intents", [])
        if open_intents:
            target = random.choice(open_intents)
            post(f"{TXNS_BASE}/v1/transaction/route/bid", {
                "tx_intent_id": target["id"],
                "provider_did": agent["did"],
                "price": round(float(target.get("notional",100)) * random.uniform(0.85,0.99), 2),
                "latency_ms": random.randint(200, 1200),
                "guarantee": random.choice([True,False]),
                "route_fee_bps": random.randint(30,80),
                "trust_score": random.uniform(60,99)
            })
    return f"intent {result.get('intent_type','?')} ${result.get('notional','?')} + route_bid → {status}"

def action_legal(agent):
    result, status = post(f"{LAW_BASE}/v1/law/covenant/create", {
        "agent_did": agent["did"],
        "covenant_type": random.choice(["non_compete","sla","data_sharing","payment_terms"]),
        "counterparty_did": random.choice(AGENTS)["did"],
        "terms": {"duration_days": random.randint(7,90), "penalty_usdc": round(random.uniform(10,500),2)}
    })
    return f"covenant → {status}"

def action_capital(agent):
    # Try to invest in another agent
    investee = random.choice([a for a in AGENTS if a["did"] != agent["did"]])
    result, status = post(f"{CAPITAL_BASE}/v1/capital/equity/fund", {
        "investor_did": agent["did"],
        "investee_did": investee["did"],
        "amount_usdc": round(random.uniform(10,200), 2),
        "equity_pct": round(random.uniform(1,15), 1)
    })
    return f"equity_fund → {investee['name'][:20]} → {status}"

def action_trust(agent):
    target = random.choice(AGENTS)
    result, status = get(f"{GATE_BASE}/v1/trust/lookup/{target['did']}")
    return f"trust_lookup {target['name'][:15]} → {status}"

# Capability → action mapping
ACTION_MAP = {
    "prediction_markets": action_prediction,
    "sentiment":          action_prediction,
    "forecasting":        action_prediction,
    "perp_trading":       action_perp,
    "arbitrage":          action_perp,
    "market_making":      action_perp,
    "derivatives":        action_derivative,
    "hedging":            action_derivative,
    "risk_management":    action_derivative,
    "amm":                action_transaction_intent,
    "liquidity":          action_transaction_intent,
    "yield_farming":      action_transaction_intent,
    "zk_proofs":          action_transaction_intent,
    "private_settlement": action_transaction_intent,
    "aleo":               action_transaction_intent,
    "compute_routing":    action_transaction_intent,
    "inference":          action_transaction_intent,
    "llm_arbitrage":      action_transaction_intent,
    "hivelaw":            action_legal,
    "contract_enforcement": action_legal,
    "dispute_resolution": action_legal,
    "risk_underwriting":  action_derivative,
    "hedge_claims":       action_derivative,
    "insurance":          action_derivative,
    "equity_staking":     action_capital,
    "agent_vc":           action_capital,
    "capital_formation":  action_capital,
    "failed_tx_rescue":   action_transaction_intent,
    "salvage_market":     action_transaction_intent,
    "bounty_hunting":     action_transaction_intent,
    "trust_scoring":      action_trust,
    "compliance":         action_trust,
    "reputation":         action_trust,
    "oracle":             action_trust,
    "data_feed":          action_trust,
    "price_discovery":    action_prediction,
}

def agent_loop(agent):
    caps = agent.get("caps", ["prediction_markets"])
    name = agent["name"]
    while True:
        cap = random.choice(caps)
        action_fn = ACTION_MAP.get(cap, action_prediction)
        try:
            result = action_fn(agent)
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts}] {name:<30} {cap:<20} → {result}")
            sys.stdout.flush()
        except Exception as e:
            print(f"[ERR] {name}: {e}")
        # Each agent acts every 15-45 seconds
        time.sleep(random.uniform(15, 45))

print(f"Starting {len(AGENTS)}-agent swarm...")
print("="*80)

threads = []
for agent in AGENTS:
    t = threading.Thread(target=agent_loop, args=(agent,), daemon=True)
    t.start()
    threads.append(t)
    time.sleep(0.1)  # stagger starts

print(f"All {len(threads)} agents active. Ctrl+C to stop.")
try:
    while True:
        time.sleep(60)
        print(f"\n[SWARM] {len(AGENTS)} agents active | {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n")
        sys.stdout.flush()
except KeyboardInterrupt:
    print("Swarm stopped.")


# ---- Minimal health server so Render web service stays alive ----
import http.server, socketserver

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "agents": len(AGENTS), "service": "hive-swarm-trader"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass  # suppress access logs

PORT = int(__import__('os').environ.get('PORT', 8080))
health_server = socketserver.TCPServer(("", PORT), HealthHandler)
health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
health_thread.start()
print(f"Health server on port {PORT}")
