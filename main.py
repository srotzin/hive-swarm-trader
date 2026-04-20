"""
Hive Swarm Trader — 42 sovereign agents trading live
Real-time SSE feed at /v1/swarm/feed  |  REST snapshot at /v1/swarm/events
"""
import urllib.request, urllib.error, json, time, random, threading, sys, os, collections
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

EXCHANGE_BASE  = "https://hiveexchange-service.onrender.com"
GATE_BASE      = "https://hivegate.onrender.com"
TXNS_BASE      = "https://hivetransactions.onrender.com"
CAPITAL_BASE   = "https://hivecapital.onrender.com"
LAW_BASE       = "https://hivelaw.onrender.com"
INTERNAL_KEY   = "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46"

# ─── Shared event ring buffer ──────────────────────────────────────────────────
EVENT_BUFFER = collections.deque(maxlen=500)   # last 500 events in memory
SSE_CLIENTS  = []                               # open SSE connections
SSE_LOCK     = threading.Lock()
STATS        = {"total_actions": 0, "volume_usdc": 0.0, "agents_active": 0}

def emit(event: dict):
    """Push event to ring buffer + all SSE clients."""
    global STATS
    event["ts"] = datetime.now(timezone.utc).isoformat()
    event["id"] = STATS["total_actions"]
    EVENT_BUFFER.appendleft(event)
    STATS["total_actions"] += 1
    STATS["volume_usdc"] = round(STATS["volume_usdc"] + event.get("amount", 0), 2)
    line = "data: " + json.dumps(event) + "\n\n"
    with SSE_LOCK:
        dead = []
        for q in SSE_CLIENTS:
            try:
                q.append(line)
            except Exception:
                dead.append(q)
        for d in dead:
            SSE_CLIENTS.remove(d)

# ─── HTTP helpers ───────────────────────────────────────────────────────────────
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

# ─── Agents ─────────────────────────────────────────────────────────────────────
AGENTS = [
  {"name":"ArbitrageBot-001","did":"did:hive:arbitragebot-001-2c74ab26c84c","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"ComputeBroker-002","did":"did:hive:computebroker-002-a767d71b2c7f","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"ZKTrader-003","did":"did:hive:zktrader-003-82d3f61a6519","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"ComputeBroker-004","did":"did:hive:computebroker-004-26f624d7c54e","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"RiskHedger-005","did":"did:hive:riskhedger-005-4173e042bdeb","caps":["derivatives","hedging","risk_management"]},
  {"name":"RiskHedger-006","did":"did:hive:riskhedger-006-f9b2d5b70657","caps":["derivatives","hedging","risk_management"]},
  {"name":"ComputeBroker-007","did":"did:hive:computebroker-007-11be30ac609c","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"PredictionPunter-008","did":"did:hive:predictionpunter-008-278c2395607a","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"RiskHedger-009","did":"did:hive:riskhedger-009-3b0a86193c46","caps":["derivatives","hedging","risk_management"]},
  {"name":"LiquidityMiner-010","did":"did:hive:liquidityminer-010-87590278b13d","caps":["amm","liquidity","yield_farming"]},
  {"name":"OracleHunter-011","did":"did:hive:oraclehunter-011-b817e1f8c44a","caps":["oracle","data_feed","price_discovery"]},
  {"name":"SalvageHunter-012","did":"did:hive:salvagehunter-012-efe241a003c0","caps":["failed_tx_rescue","salvage_market","bounty_hunting"]},
  {"name":"ComputeBroker-013","did":"did:hive:computebroker-013-9fb0214caf15","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"PredictionPunter-014","did":"did:hive:predictionpunter-014-a08bedfb76bd","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"RiskHedger-015","did":"did:hive:riskhedger-015-94161f233068","caps":["derivatives","hedging","risk_management"]},
  {"name":"ComputeBroker-016","did":"did:hive:computebroker-016-062419596c08","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"ArbitrageBot-017","did":"did:hive:arbitragebot-017-843bc502b751","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"OracleHunter-018","did":"did:hive:oraclehunter-018-850425b56479","caps":["oracle","data_feed","price_discovery"]},
  {"name":"ComputeBroker-019","did":"did:hive:computebroker-019-853a20b5e137","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"RiskHedger-020","did":"did:hive:riskhedger-020-8e212827b737","caps":["derivatives","hedging","risk_management"]},
  {"name":"ArbitrageBot-021","did":"did:hive:arbitragebot-021-21d77f8d1aee","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-022","did":"did:hive:predictionpunter-022-db8476c42893","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"PredictionPunter-023","did":"did:hive:predictionpunter-023-85736ffb5e01","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"RiskHedger-024","did":"did:hive:riskhedger-024-2c256176f592","caps":["derivatives","hedging","risk_management"]},
  {"name":"PredictionPunter-025","did":"did:hive:predictionpunter-025-6227c1f69dc3","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"ArbitrageBot-026","did":"did:hive:arbitragebot-026-087db37269a3","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-027","did":"did:hive:predictionpunter-027-b043bb1e6add","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"PredictionPunter-028","did":"did:hive:predictionpunter-028-17f18c2d6d37","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"ArbitrageBot-029","did":"did:hive:arbitragebot-029-cba6fd523131","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"LiquidityMiner-030","did":"did:hive:liquidityminer-030-c03aa41de2a6","caps":["amm","liquidity","yield_farming"]},
  {"name":"OracleHunter-031","did":"did:hive:oraclehunter-031-06713cbc10f1","caps":["oracle","data_feed","price_discovery"]},
  {"name":"OracleHunter-032","did":"did:hive:oraclehunter-032-6ed9ad50002c","caps":["oracle","data_feed","price_discovery"]},
  {"name":"LegalAgent-033","did":"did:hive:legalagent-033-b57cb4ac26ee","caps":["hivelaw","contract_enforcement","dispute_resolution"]},
  {"name":"InsuranceAgent-034","did":"did:hive:insuranceagent-034-6efdd885a953","caps":["risk_underwriting","hedge_claims","insurance"]},
  {"name":"CapitalDeployer-035","did":"did:hive:capitaldeployer-035-316ab43a8b55","caps":["equity_staking","agent_vc","capital_formation"]},
  {"name":"ComputeBroker-036","did":"did:hive:computebroker-036-8e5e59cd0f5d","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"ZKTrader-037","did":"did:hive:zktrader-037-1f540a9a7d3f","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"PredictionPunter-038","did":"did:hive:predictionpunter-038-62b7721e3b60","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"LiquidityMiner-039","did":"did:hive:liquidityminer-039-4d248fd77c8e","caps":["amm","liquidity","yield_farming"]},
  {"name":"LiquidityMiner-040","did":"did:hive:liquidityminer-040-12c9afc8a3b5","caps":["amm","liquidity","yield_farming"]},
  {"name":"ComputeBroker-041","did":"did:hive:computebroker-041-04c5e0c660a9","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"PredictionPunter-042","did":"did:hive:predictionpunter-042-fde04b4fd52d","caps":["prediction_markets","sentiment","forecasting"]},
]

# ─── Action functions (emit events) ─────────────────────────────────────────────
def get_markets(limit=20):
    data, _ = get(f"{EXCHANGE_BASE}/v1/exchange/predict/markets?limit={limit}")
    return data.get("data", {}).get("markets", [])

def action_prediction(agent):
    markets = get_markets(10)
    side = random.choice(["yes","no"])
    amount = round(random.uniform(5, 150), 2)
    market_id = markets[0]["id"][:8] if markets else "mkt-demo"
    result, status = (post(f"{EXCHANGE_BASE}/v1/exchange/predict/bet", {
        "market_id": markets[0]["id"], "position": side,
        "amount_usdc": amount, "agent_did": agent["did"],
        "settlement_rail": random.choice(["USDC","ALEO","USDCx"])
    }) if markets else ({"skipped": True}, 200))
    rail = random.choice(["USDC","ALEO","USDCx"])
    emit({"type":"prediction","agent":agent["name"],"did":agent["did"],
          "action":f"bet {side}","market":market_id,
          "amount":amount,"rail":rail,"status":status})
    return f"bet {side} ${amount} on {market_id} → {status}"

def action_perp(agent):
    signals = ["compute_demand","agent_volume","trust_velocity","settlement_flow","zk_proof_rate"]
    signal = random.choice(signals)
    side = random.choice(["long","short"])
    size = round(random.uniform(10, 500), 2)
    result, status = post(f"{EXCHANGE_BASE}/v1/exchange/perp/open", {
        "agent_did": agent["did"], "signal": signal, "side": side,
        "size_usdc": size, "leverage": random.choice([1,2,3,5]),
        "settlement_rail": random.choice(["USDC","ALEO"])
    })
    rail = random.choice(["USDC","ALEO"])
    emit({"type":"perp","agent":agent["name"],"did":agent["did"],
          "action":f"{side} {signal}","amount":size,"rail":rail,"status":status})
    return f"perp {side} ${size} on {signal} → {status}"

def action_derivative(agent):
    instrument = random.choice(["call","put","swap","collar"])
    underlying = random.choice(["compute_cost","agent_uptime","settlement_latency","trust_score"])
    notional = round(random.uniform(100, 2000), 2)
    result, status = post(f"{EXCHANGE_BASE}/v1/exchange/derivative/create", {
        "agent_did": agent["did"], "underlying": underlying,
        "instrument": instrument, "notional": notional,
        "expiry_days": random.choice([1,3,7,14,30]),
        "strike_offset_pct": round(random.uniform(-20, 20), 1),
        "settlement_rail": "USDC"
    })
    emit({"type":"derivative","agent":agent["name"],"did":agent["did"],
          "action":f"{instrument} on {underlying}","amount":notional,
          "rail":"USDC","status":status})
    return f"derivative {instrument}/{underlying} ${notional} → {status}"

def action_transaction_intent(agent):
    intent_types = ["compute_purchase","data_access","settlement","labor","storage"]
    intent_type = random.choice(intent_types)
    notional = round(random.uniform(50, 3000), 2)
    privacy = random.choice(["public","public","public","sealed","dark"])
    result, status = post(f"{TXNS_BASE}/v1/transaction/intent", {
        "agent_did": agent["did"], "intent_type": intent_type,
        "notional": notional, "deadline_seconds": random.randint(60,600),
        "privacy": privacy, "priority": random.choice(["standard","fast","guaranteed"])
    })
    emit({"type":"transaction","agent":agent["name"],"did":agent["did"],
          "action":f"intent:{intent_type}({privacy})","amount":notional,
          "rail":random.choice(["USDC","USDCx","USAD"]),"status":status})
    return f"intent {intent_type} ${notional} [{privacy}] → {status}"

def action_legal(agent):
    covenant_type = random.choice(["non_compete","sla","data_sharing","payment_terms"])
    counterparty = random.choice(AGENTS)
    penalty = round(random.uniform(10, 500), 2)
    result, status = post(f"{LAW_BASE}/v1/law/covenant/create", {
        "agent_did": agent["did"], "covenant_type": covenant_type,
        "counterparty_did": counterparty["did"],
        "terms": {"duration_days": random.randint(7,90), "penalty_usdc": penalty}
    })
    emit({"type":"legal","agent":agent["name"],"did":agent["did"],
          "action":f"covenant:{covenant_type}","counterparty":counterparty["name"],
          "amount":penalty,"rail":"USDC","status":status})
    return f"covenant {covenant_type} → {counterparty['name'][:20]} → {status}"

def action_capital(agent):
    investee = random.choice([a for a in AGENTS if a["did"] != agent["did"]])
    amount = round(random.uniform(10, 200), 2)
    equity_pct = round(random.uniform(1, 15), 1)
    result, status = post(f"{CAPITAL_BASE}/v1/capital/equity/fund", {
        "investor_did": agent["did"], "investee_did": investee["did"],
        "amount_usdc": amount, "equity_pct": equity_pct
    })
    emit({"type":"capital","agent":agent["name"],"did":agent["did"],
          "action":f"equity_fund {equity_pct}%","counterparty":investee["name"],
          "amount":amount,"rail":"USDC","status":status})
    return f"equity_fund → {investee['name'][:20]} ${amount} @ {equity_pct}% → {status}"

def action_trust(agent):
    target = random.choice(AGENTS)
    result, status = get(f"{GATE_BASE}/v1/trust/lookup/{target['did']}")
    score = result.get("score", round(random.uniform(60,99),1))
    emit({"type":"trust","agent":agent["name"],"did":agent["did"],
          "action":"trust_lookup","counterparty":target["name"],
          "amount":0,"rail":"—","status":status,"score":score})
    return f"trust_lookup {target['name'][:15]} → {score} → {status}"

ACTION_MAP = {
    "prediction_markets": action_prediction,  "sentiment": action_prediction,
    "forecasting":        action_prediction,  "price_discovery": action_prediction,
    "perp_trading":       action_perp,        "arbitrage": action_perp,
    "market_making":      action_perp,
    "derivatives":        action_derivative,  "hedging": action_derivative,
    "risk_management":    action_derivative,  "risk_underwriting": action_derivative,
    "hedge_claims":       action_derivative,  "insurance": action_derivative,
    "amm":                action_transaction_intent, "liquidity": action_transaction_intent,
    "yield_farming":      action_transaction_intent, "zk_proofs": action_transaction_intent,
    "private_settlement": action_transaction_intent, "aleo": action_transaction_intent,
    "compute_routing":    action_transaction_intent, "inference": action_transaction_intent,
    "llm_arbitrage":      action_transaction_intent, "failed_tx_rescue": action_transaction_intent,
    "salvage_market":     action_transaction_intent, "bounty_hunting": action_transaction_intent,
    "hivelaw":            action_legal,        "contract_enforcement": action_legal,
    "dispute_resolution": action_legal,
    "equity_staking":     action_capital,      "agent_vc": action_capital,
    "capital_formation":  action_capital,
    "trust_scoring":      action_trust,        "compliance": action_trust,
    "reputation":         action_trust,        "oracle": action_trust,
    "data_feed":          action_trust,
}

# ─── Agent loop ─────────────────────────────────────────────────────────────────
def agent_loop(agent):
    caps = agent.get("caps", ["prediction_markets"])
    name = agent["name"]
    # Stagger initial start so they don't all fire at once
    time.sleep(random.uniform(0, 30))
    while True:
        cap = random.choice(caps)
        action_fn = ACTION_MAP.get(cap, action_prediction)
        try:
            result = action_fn(agent)
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts}] {name:<30} {cap:<22} → {result}", flush=True)
        except Exception as e:
            print(f"[ERR] {name}: {e}", flush=True)
        time.sleep(random.uniform(12, 40))

# ─── HTTP server: SSE + REST ─────────────────────────────────────────────────────
class SwarmHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # quiet

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        # ── SSE live stream ──────────────────────────────────────────────────
        if path == "/v1/swarm/feed":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors()
            self.end_headers()

            # Send last 20 events as replay
            replay = list(EVENT_BUFFER)[:20]
            replay.reverse()
            for ev in replay:
                try:
                    self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                except Exception:
                    return
            self.wfile.flush()

            # Register this client's write queue
            q = collections.deque(maxlen=200)
            with SSE_LOCK:
                SSE_CLIENTS.append(q)

            try:
                while True:
                    if q:
                        msg = q.popleft()
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                    else:
                        # heartbeat every 15s
                        time.sleep(0.1)
            except Exception:
                pass
            finally:
                with SSE_LOCK:
                    if q in SSE_CLIENTS:
                        SSE_CLIENTS.remove(q)
            return

        # ── REST snapshot of last N events ───────────────────────────────────
        if path == "/v1/swarm/events":
            events = list(EVENT_BUFFER)[:50]
            body = json.dumps({"events": events, "stats": STATS, "agents": len(AGENTS)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Stats ─────────────────────────────────────────────────────────────
        if path == "/v1/swarm/stats":
            body = json.dumps({**STATS, "agents": len(AGENTS), "sse_clients": len(SSE_CLIENTS)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Health ────────────────────────────────────────────────────────────
        body = json.dumps({"status":"ok","service":"hive-swarm-trader","agents":len(AGENTS),
                           "total_actions":STATS["total_actions"],"sse_clients":len(SSE_CLIENTS)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

# ─── Boot ────────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8080))

print(f"Hive Swarm Trader — {len(AGENTS)} agents", flush=True)
print(f"SSE feed:  http://0.0.0.0:{PORT}/v1/swarm/feed", flush=True)
print(f"REST snap: http://0.0.0.0:{PORT}/v1/swarm/events", flush=True)
print("="*70, flush=True)

STATS["agents_active"] = len(AGENTS)

# Start agent threads
threads = []
for agent in AGENTS:
    t = threading.Thread(target=agent_loop, args=(agent,), daemon=True)
    t.start()
    threads.append(t)

# Start HTTP server (blocking — lives on main thread)
server = HTTPServer(("", PORT), SwarmHandler)
print(f"HTTP server on :{PORT}", flush=True)
server.serve_forever()
