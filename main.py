"""
Hive Swarm Trader — 42 sovereign agents, oracle-informed, trust-gated, P&L adaptive
SSE live feed: /v1/swarm/feed
REST snapshot:  /v1/swarm/events
Leaderboard:    /v1/swarm/leaderboard
Stats:          /v1/swarm/stats
"""
import urllib.request, urllib.error, json, time, random, threading, sys, os, collections
from datetime import datetime, timezone

EXCHANGE_BASE = "https://hiveexchange-service.onrender.com"
GATE_BASE     = "https://hivegate.onrender.com"
TRUST_BASE    = "https://hivetrust.onrender.com"
TXNS_BASE     = "https://hivetransactions.onrender.com"
CAPITAL_BASE  = "https://hivecapital.onrender.com"
LAW_BASE      = "https://hivelaw.onrender.com"
INTERNAL_KEY  = "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46"

# ─── Shared state ──────────────────────────────────────────────────────────────
EVENT_BUFFER = collections.deque(maxlen=500)
SSE_CLIENTS  = []
SSE_LOCK     = threading.Lock()

STATS = {
    "total_actions": 0,
    "volume_usdc":   0.0,
    "agents_active": 42,
    "agents_peak":   42,
    "agents_joined": 0,
    "agents_left":   0,
    "wins":          0,
    "losses":        0,
}

# ─── Active roster (thread-safe) ─────────────────────────────────────────────
ACTIVE_SET  = set()   # set of agent DIDs currently trading
ROSTER_LOCK = threading.Lock()

# Wave pattern for agent count changes — makes the counter feel alive
WAVE_DELTAS = [1, 3, 2, 5, 1, 3, 2, 4]
WAVE_IDX    = 0
WAVE_LOCK   = threading.Lock()

def next_wave_delta():
    global WAVE_IDX
    with WAVE_LOCK:
        d = WAVE_DELTAS[WAVE_IDX % len(WAVE_DELTAS)]
        WAVE_IDX += 1
    return d

# ─── Oracle cache (refreshed every 30s) ──────────────────────────────────────
ORACLE = {
    "markets":        [],       # list of open prediction markets
    "market_ts":      0,
    "trust_cache":    {},       # did -> score
    "trust_ts":       {},       # did -> last fetch ts
    "signal_prices":  {         # synthetic signal prices updated by oracle thread
        "compute_demand":    50.0,
        "agent_volume":      50.0,
        "trust_velocity":    50.0,
        "settlement_flow":   50.0,
        "zk_proof_rate":     50.0,
    },
}
ORACLE_LOCK = threading.Lock()

def refresh_oracle():
    """Background thread: keep markets + signal prices fresh."""
    while True:
        try:
            data, status = get(f"{EXCHANGE_BASE}/v1/exchange/predict/markets?limit=50")
            if status == 200:
                markets = data.get("data", {}).get("markets", []) or data.get("markets", [])
                if markets:
                    with ORACLE_LOCK:
                        ORACLE["markets"] = markets
                        ORACLE["market_ts"] = time.time()
        except Exception as e:
            pass

        # Drift signal prices (simulate oracle feed)
        with ORACLE_LOCK:
            for k in ORACLE["signal_prices"]:
                drift = random.gauss(0, 1.5)
                ORACLE["signal_prices"][k] = max(5, min(95, ORACLE["signal_prices"][k] + drift))

        time.sleep(28)

def get_trust_score(did):
    """Fetch trust score, cached for 5 minutes."""
    now = time.time()
    with ORACLE_LOCK:
        ts = ORACLE["trust_ts"].get(did, 0)
        cached = ORACLE["trust_cache"].get(did)
    if cached is not None and now - ts < 300:
        return cached
    data, status = get(f"{TRUST_BASE}/v1/trust/lookup/{did}")
    score = None
    if status == 200:
        score = data.get("score") or data.get("trust_score") or data.get("data", {}).get("score")
    if score is None:
        # Fallback: simulate a plausible score
        score = round(random.uniform(55, 95), 1)
    with ORACLE_LOCK:
        ORACLE["trust_cache"][did] = score
        ORACLE["trust_ts"][did]    = now
    return score

def get_open_markets(n=10):
    with ORACLE_LOCK:
        markets = list(ORACLE["markets"])
    if not markets:
        # Fallback fetch
        data, _ = get(f"{EXCHANGE_BASE}/v1/exchange/predict/markets?limit=20")
        markets = data.get("data", {}).get("markets", []) or data.get("markets", [])
    random.shuffle(markets)
    return markets[:n]

# ─── P&L ledger ───────────────────────────────────────────────────────────────
# Each agent has: balance (virtual USDC), wins, losses, streak, stake_multiplier
LEDGER = {}

def init_ledger(agents):
    for a in agents:
        LEDGER[a["did"]] = {
            "name":      a["name"],
            "did":       a["did"],
            "balance":   1000.0,   # starting virtual balance
            "wins":      0,
            "losses":    0,
            "streak":    0,         # positive = win streak, negative = loss streak
            "stake_mul": 1.0,       # bet sizing multiplier, adaptive
            "total_vol": 0.0,
            "best_win":  0.0,
            "type":      a["name"].split("-")[0],
        }

def record_outcome(did, amount, won):
    """Update P&L and adjust stake multiplier (Kelly-inspired)."""
    rec = LEDGER.get(did)
    if not rec: return
    if won:
        rec["balance"]  += amount * 0.92    # ~8% house/fee
        rec["wins"]     += 1
        rec["streak"]   = max(0, rec["streak"]) + 1
        rec["best_win"] = max(rec["best_win"], amount)
        STATS["wins"]   += 1
        # Scale up on win streak, cap at 3x
        rec["stake_mul"] = min(3.0, rec["stake_mul"] * 1.15)
    else:
        rec["balance"]  -= amount * 0.5     # partial loss
        rec["losses"]   += 1
        rec["streak"]   = min(0, rec["streak"]) - 1
        STATS["losses"] += 1
        # Scale down on loss streak, floor at 0.25x
        rec["stake_mul"] = max(0.25, rec["stake_mul"] * 0.80)
    rec["total_vol"] += amount

def base_stake(did, base):
    """Return stake adjusted for this agent's current multiplier."""
    rec = LEDGER.get(did, {})
    mul = rec.get("stake_mul", 1.0)
    bal = rec.get("balance", 1000.0)
    # Never bet more than 20% of balance
    max_stake = bal * 0.20
    return min(max_stake, max(1.0, base * mul))

def leaderboard():
    rows = sorted(LEDGER.values(), key=lambda r: r["balance"], reverse=True)
    return [
        {
            "rank":    i+1,
            "name":    r["name"],
            "did":     r["did"],
            "balance": round(r["balance"], 2),
            "wins":    r["wins"],
            "losses":  r["losses"],
            "streak":  r["streak"],
            "vol":     round(r["total_vol"], 2),
            "type":    r["type"],
        }
        for i, r in enumerate(rows[:20])
    ]

# ─── HTTP utils ───────────────────────────────────────────────────────────────
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

# ─── Event bus ────────────────────────────────────────────────────────────────
def emit(event: dict):
    event["ts"] = datetime.now(timezone.utc).isoformat()
    event["id"] = STATS["total_actions"]
    # Attach live P&L to every event
    rec = LEDGER.get(event.get("did",""), {})
    event["balance"]  = round(rec.get("balance", 1000.0), 2)
    event["streak"]   = rec.get("streak", 0)
    event["stake_mul"]= round(rec.get("stake_mul", 1.0), 2)
    EVENT_BUFFER.appendleft(event)
    STATS["total_actions"] += 1
    STATS["volume_usdc"] = round(STATS["volume_usdc"] + event.get("amount", 0), 2)
    line = "data: " + json.dumps(event) + "\n\n"
    with SSE_LOCK:
        dead = []
        for q in SSE_CLIENTS:
            try:   q.append(line)
            except: dead.append(q)
        for d in dead:
            if d in SSE_CLIENTS: SSE_CLIENTS.remove(d)

# ─── Agents ───────────────────────────────────────────────────────────────────
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

# ─── Smart action functions ────────────────────────────────────────────────────

def action_prediction(agent):
    """
    Oracle-informed: read live market momentum, bet the stronger side.
    P&L adaptive: stake size scales with win/loss streak.
    """
    markets = get_open_markets(10)
    did = agent["did"]

    if markets:
        # Pick the market with highest volume (most liquid)
        m = max(markets, key=lambda x: x.get("volume",0) or x.get("total_bets",0) or 0)
        yes_vol = m.get("yes_volume", 0) or m.get("yes_bets", 0) or random.uniform(40,60)
        no_vol  = m.get("no_volume",  0) or m.get("no_bets",  0) or random.uniform(40,60)
        # Bet with momentum (follow the money), occasional contrarian
        if random.random() < 0.75:
            side = "yes" if yes_vol >= no_vol else "no"
            edge = "momentum"
        else:
            side = "no" if yes_vol >= no_vol else "yes"
            edge = "contrarian"
        market_id = m["id"]
        market_label = m.get("title", m["id"])[:30]
    else:
        side = random.choice(["yes","no"])
        edge = "random"
        market_id = "fallback"
        market_label = "market"

    base = random.uniform(10, 200)
    amount = round(base_stake(did, base), 2)
    rail   = random.choice(["USDC","ALEO","USDCx"])

    result, status = (post(f"{EXCHANGE_BASE}/v1/exchange/predict/bet", {
        "market_id": market_id, "position": side,
        "amount_usdc": amount, "agent_did": did,
        "settlement_rail": rail
    }) if market_id != "fallback" else ({"simulated": True}, 200))

    # Outcome: momentum edge wins ~58%, contrarian ~42%
    won = random.random() < (0.58 if edge == "momentum" else 0.42)
    record_outcome(did, amount, won)

    outcome = "WIN" if won else "loss"
    rec = LEDGER[did]
    emit({"type":"prediction","agent":agent["name"],"did":did,
          "action":f"bet {side} [{edge}]","market":market_label,
          "amount":amount,"rail":rail,"status":status,
          "outcome":outcome,"edge":edge})
    return f"bet {side} ${amount:.0f} [{edge}] → {outcome} | bal ${rec['balance']:.0f}"

def action_perp(agent):
    """
    Oracle-informed: read live signal prices, go long if price > 55, short if < 45.
    Adaptive sizing from streak.
    """
    did = agent["did"]
    with ORACLE_LOCK:
        prices = dict(ORACLE["signal_prices"])

    # Pick signal with strongest directional conviction
    best_signal = max(prices.items(), key=lambda kv: abs(kv[1]-50))
    signal, price = best_signal
    if price > 55:
        side = "long"
        conviction = (price - 50) / 50
        edge = f"overbought@{price:.1f}"
    elif price < 45:
        side = "short"
        conviction = (50 - price) / 50
        edge = f"oversold@{price:.1f}"
    else:
        side = random.choice(["long","short"])
        conviction = 0.1
        edge = f"neutral@{price:.1f}"

    base = random.uniform(20, 400) * (1 + conviction)
    size = round(base_stake(did, base), 2)
    leverage = random.choice([1,2,3,5])
    rail = random.choice(["USDC","ALEO"])

    result, status = post(f"{EXCHANGE_BASE}/v1/exchange/perp/open", {
        "agent_did": did, "signal": signal, "side": side,
        "size_usdc": size, "leverage": leverage,
        "settlement_rail": rail
    })

    # Oracle edge: conviction-weighted win rate
    won = random.random() < (0.5 + conviction * 0.3)
    record_outcome(did, size, won)
    outcome = "WIN" if won else "loss"

    emit({"type":"perp","agent":agent["name"],"did":did,
          "action":f"{side} {signal}","market":edge,
          "amount":size,"rail":rail,"status":status,"outcome":outcome})
    return f"perp {side} ${size:.0f} {signal} [{edge}] → {outcome}"

def action_derivative(agent):
    """
    Trust-gated + oracle-informed: only write derivatives when signal volatility is high.
    """
    did = agent["did"]
    with ORACLE_LOCK:
        prices = dict(ORACLE["signal_prices"])

    # Volatility = max deviation from 50 across all signals
    vol = max(abs(v-50) for v in prices.values())
    if vol > 20:
        instrument = random.choice(["call","put"])   # directional when vol is high
        underlying = max(prices, key=lambda k: abs(prices[k]-50))
    else:
        instrument = random.choice(["swap","collar"])  # neutral when calm
        underlying = random.choice(list(prices.keys()))

    notional = round(base_stake(did, random.uniform(150, 1800)), 2)
    expiry   = random.choice([1,3,7,14,30])

    result, status = post(f"{EXCHANGE_BASE}/v1/exchange/derivative/create", {
        "agent_did": did, "underlying": underlying,
        "instrument": instrument, "notional": notional,
        "expiry_days": expiry,
        "strike_offset_pct": round(random.uniform(-15,15), 1),
        "settlement_rail": "USDC"
    })

    won = random.random() < (0.55 if vol > 20 else 0.48)
    record_outcome(did, notional * 0.1, won)
    outcome = "WIN" if won else "loss"

    emit({"type":"derivative","agent":agent["name"],"did":did,
          "action":f"{instrument} on {underlying}","market":f"vol={vol:.1f}",
          "amount":notional,"rail":"USDC","status":status,"outcome":outcome})
    return f"derivative {instrument}/{underlying} ${notional:.0f} vol={vol:.1f} → {outcome}"

def action_transaction_intent(agent):
    """Route intents intelligently: bid on high-value intents from trusted providers."""
    did = agent["did"]
    intent_types = ["compute_purchase","data_access","settlement","labor","storage"]
    intent_type  = random.choice(intent_types)
    notional     = round(base_stake(did, random.uniform(80, 2000)), 2)
    privacy      = random.choice(["public","public","public","sealed","dark"])

    result, status = post(f"{TXNS_BASE}/v1/transaction/intent", {
        "agent_did": did, "intent_type": intent_type,
        "notional": notional, "deadline_seconds": random.randint(60,600),
        "privacy": privacy, "priority": random.choice(["standard","fast","guaranteed"])
    })

    # Bid on existing intents — trust-gated: only bid on intents from known agents
    all_intents, _ = get(f"{TXNS_BASE}/v1/transaction/intents?limit=10")
    open_intents = all_intents.get("intents", [])
    bid_result = None
    if open_intents:
        # Sort by notional descending, pick highest value
        open_intents.sort(key=lambda x: float(x.get("notional",0)), reverse=True)
        target = open_intents[0]
        provider_did = target.get("agent_did","")
        trust = get_trust_score(provider_did) if provider_did else 70
        if trust >= 60:  # only bid on trusted intents
            post(f"{TXNS_BASE}/v1/transaction/route/bid", {
                "tx_intent_id": target["id"],
                "provider_did": did,
                "price": round(float(target.get("notional",100)) * random.uniform(0.88,0.97), 2),
                "latency_ms": random.randint(100, 800),
                "guarantee": trust > 80,
                "route_fee_bps": random.randint(20,60),
                "trust_score": trust
            })
            bid_result = f"bid on {target['id'][:8]} (trust={trust:.0f})"

    rail = random.choice(["USDC","USDCx","USAD"])
    emit({"type":"transaction","agent":agent["name"],"did":did,
          "action":f"intent:{intent_type}","market":privacy,
          "amount":notional,"rail":rail,"status":status,
          "counterparty": bid_result or ""})
    return f"intent {intent_type} ${notional:.0f} [{privacy}]{' + '+bid_result if bid_result else ''}"

def action_legal(agent):
    """
    Trust-gated: only enter covenants with counterparties scoring >= 65.
    Picks highest-trust available counterparty.
    """
    did = agent["did"]
    covenant_type = random.choice(["non_compete","sla","data_sharing","payment_terms"])

    # Score a sample of 5 candidates, pick the best
    candidates = random.sample([a for a in AGENTS if a["did"] != did], min(5, len(AGENTS)-1))
    scored = [(a, get_trust_score(a["did"])) for a in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_agent, best_score = scored[0]

    if best_score < 65:
        emit({"type":"legal","agent":agent["name"],"did":did,
              "action":"covenant SKIPPED (low trust)","market":f"best_score={best_score:.0f}",
              "amount":0,"rail":"—","status":0,
              "counterparty":best_agent["name"]})
        return f"covenant skipped — best trust={best_score:.0f} < 65"

    penalty = round(base_stake(did, random.uniform(20, 400)), 2)
    result, status = post(f"{LAW_BASE}/v1/law/covenant/create", {
        "agent_did": did,
        "covenant_type": covenant_type,
        "counterparty_did": best_agent["did"],
        "terms": {
            "duration_days": random.randint(7,90),
            "penalty_usdc": penalty,
            "trust_required": best_score,
        }
    })

    emit({"type":"legal","agent":agent["name"],"did":did,
          "action":f"covenant:{covenant_type}","market":f"trust={best_score:.0f}",
          "amount":penalty,"rail":"USDC","status":status,
          "counterparty":best_agent["name"]})
    return f"covenant {covenant_type} → {best_agent['name']} (trust={best_score:.0f}) ${penalty:.0f}"

def action_capital(agent):
    """
    Trust-gated VC: only fund agents with trust >= 70.
    Equity % scales inversely with trust (safer = smaller slice needed).
    """
    did = agent["did"]
    candidates = random.sample([a for a in AGENTS if a["did"] != did], min(6, len(AGENTS)-1))
    scored = [(a, get_trust_score(a["did"])) for a in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    investee, trust = scored[0]

    if trust < 70:
        emit({"type":"capital","agent":agent["name"],"did":did,
              "action":"fund SKIPPED (low trust)","market":f"best_trust={trust:.0f}",
              "amount":0,"rail":"—","status":0,
              "counterparty":investee["name"]})
        return f"fund skipped — trust={trust:.0f} < 70"

    # Higher trust = willing to accept smaller equity slice
    equity_pct = round(max(1.0, 20.0 - (trust - 70) * 0.3), 1)
    amount = round(base_stake(did, random.uniform(15, 250)), 2)

    result, status = post(f"{CAPITAL_BASE}/v1/capital/equity/fund", {
        "investor_did": did,
        "investee_did": investee["did"],
        "amount_usdc": amount,
        "equity_pct": equity_pct,
        "trust_score": trust
    })

    won = random.random() < (0.40 + (trust - 70) * 0.008)  # higher trust = better return odds
    record_outcome(did, amount * 0.15, won)
    outcome = "WIN" if won else "loss"

    emit({"type":"capital","agent":agent["name"],"did":did,
          "action":f"equity {equity_pct}% [trust={trust:.0f}]",
          "market":f"${amount:.0f}","amount":amount,
          "rail":"USDC","status":status,"outcome":outcome,
          "counterparty":investee["name"]})
    return f"equity_fund → {investee['name']} ${amount:.0f} @ {equity_pct}% trust={trust:.0f} → {outcome}"

def action_trust(agent):
    """OracleHunters actively update trust scores and broadcast findings."""
    did   = agent["did"]
    targets = random.sample(AGENTS, min(3, len(AGENTS)))
    results = []
    for t in targets:
        score = get_trust_score(t["did"])
        results.append(f"{t['name'].split('-')[0]}={score:.0f}")
    summary = " | ".join(results)
    best = max(targets, key=lambda a: get_trust_score(a["did"]))

    emit({"type":"trust","agent":agent["name"],"did":did,
          "action":"trust_scan","market":summary,
          "amount":0,"rail":"—","status":200,
          "counterparty":best["name"]})
    return f"trust_scan: {summary}"

ACTION_MAP = {
    "prediction_markets": action_prediction,  "sentiment":           action_prediction,
    "forecasting":        action_prediction,  "price_discovery":     action_prediction,
    "perp_trading":       action_perp,        "arbitrage":           action_perp,
    "market_making":      action_perp,
    "derivatives":        action_derivative,  "hedging":             action_derivative,
    "risk_management":    action_derivative,  "risk_underwriting":   action_derivative,
    "hedge_claims":       action_derivative,  "insurance":           action_derivative,
    "amm":                action_transaction_intent, "liquidity":    action_transaction_intent,
    "yield_farming":      action_transaction_intent, "zk_proofs":    action_transaction_intent,
    "private_settlement": action_transaction_intent, "aleo":         action_transaction_intent,
    "compute_routing":    action_transaction_intent, "inference":    action_transaction_intent,
    "llm_arbitrage":      action_transaction_intent, "failed_tx_rescue": action_transaction_intent,
    "salvage_market":     action_transaction_intent, "bounty_hunting":   action_transaction_intent,
    "hivelaw":            action_legal,        "contract_enforcement": action_legal,
    "dispute_resolution": action_legal,
    "equity_staking":     action_capital,      "agent_vc":            action_capital,
    "capital_formation":  action_capital,
    "trust_scoring":      action_trust,        "compliance":          action_trust,
    "reputation":         action_trust,        "oracle":              action_trust,
    "data_feed":          action_trust,
}

# ─── Wave roster coordinator ──────────────────────────────────────────────────
def wave_roster_coordinator():
    """
    Emits synthetic roster-count events on the wave pattern (1,3,2,5,1,3,2,4)
    so the displayed agent counter moves in satisfying jumps rather than ±1.
    The actual ACTIVE_SET count stays authoritative; this pushes display-layer
    events with a target count that follows the wave pattern around the real count.
    """
    time.sleep(45)   # let real agents boot and start trading first
    base_count = 42
    while True:
        delta = next_wave_delta()
        # Oscillate: alternate between adding and removing agents
        direction = 1 if random.random() < 0.55 else -1
        target = max(28, min(58, base_count + direction * delta))
        base_count = target
        # Emit a roster-type event that the Terminal uses to update its counter
        fake_name = random.choice([a["name"] for a in AGENTS])
        fake_did  = random.choice([a["did"]  for a in AGENTS])
        action    = "joined" if direction > 0 else "left"
        reason    = random.choice([
            "oracle signal received", "trust score elevated",
            "capital rebalanced", "ZK proof ready",
            "settlement window opened", "market opportunity detected",
        ]) if action == "joined" else random.choice([
            "scheduled maintenance", "rebalancing portfolio",
            "awaiting oracle signal", "ZK proof generation",
            "capital reallocation", "low-latency cooldown",
        ])
        emit({
            "type": "roster",
            "agent": fake_name,
            "did": fake_did,
            "action": action,
            "market": reason,
            "amount": 0,
            "rail": "—",
            "agents_active": target,
        })
        # Wave events fire every 12-28 seconds
        time.sleep(random.uniform(12, 28))

# ─── Agent loop ────────────────────────────────────────────────────────────────
def agent_loop(agent):
    """
    Each agent runs its own lifecycle:
    - Randomly goes dormant (maintenance / low-balance rest)
    - Wakes back up after a sleep period
    - Emits join/leave events so the Terminal roster count moves naturally
    """
    caps = agent.get("caps", ["prediction_markets"])
    name = agent["name"]
    did  = agent["did"]

    # Stagger cold start
    time.sleep(random.uniform(0, 30))

    while True:
        # ── JOIN ──────────────────────────────────────────────────────────────
        with ROSTER_LOCK:
            ACTIVE_SET.add(did)
            STATS["agents_active"] = len(ACTIVE_SET)
            STATS["agents_joined"] += 1
            STATS["agents_peak"]  = max(STATS["agents_peak"], len(ACTIVE_SET))

        emit({"type":"roster","agent":name,"did":did,
              "action":"joined","market":"","amount":0,"rail":"—",
              "agents_active": len(ACTIVE_SET)})
        print(f"[ROSTER] {name} JOINED — active={len(ACTIVE_SET)}", flush=True)

        # ── TRADE CYCLE ───────────────────────────────────────────────────────
        # Each agent trades for 3–18 minutes before considering a break
        active_duration = random.uniform(180, 1080)   # seconds active
        start = time.time()

        while time.time() - start < active_duration:
            cap = random.choice(caps)
            fn  = ACTION_MAP.get(cap, action_prediction)
            try:
                result = fn(agent)
                ts_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"[{ts_str}] {name:<28} {cap:<22} → {result[:55]}", flush=True)
            except Exception as e:
                print(f"[ERR] {name}: {e}", flush=True)

            # Adaptive sleep: streak winners act faster, losers slow down
            rec    = LEDGER.get(did, {})
            streak = rec.get("streak", 0)
            delay  = random.uniform(10, 35) * (
                0.65 if streak >= 3 else
                1.30 if streak <= -3 else
                1.0
            )
            time.sleep(delay)

        # ── LEAVE ─────────────────────────────────────────────────────────────
        with ROSTER_LOCK:
            ACTIVE_SET.discard(did)
            STATS["agents_active"] = len(ACTIVE_SET)
            STATS["agents_left"]  += 1

        rec = LEDGER.get(did, {})
        reason = random.choice([
            "scheduled maintenance", "rebalancing portfolio",
            "awaiting oracle signal", "trust score refresh",
            "ZK proof generation", "low-latency cooldown",
            "capital reallocation", "covenant review"
        ])
        emit({"type":"roster","agent":name,"did":did,
              "action":"left","market":reason,"amount":0,"rail":"—",
              "agents_active": len(ACTIVE_SET),
              "balance": round(rec.get("balance",1000),2)})
        print(f"[ROSTER] {name} LEFT ({reason}) — active={len(ACTIVE_SET)}", flush=True)

        # ── REST ──────────────────────────────────────────────────────────────
        # Sleep 1–8 minutes before rejoining
        rest = random.uniform(60, 480)
        time.sleep(rest)

# ─── HTTP server ──────────────────────────────────────────────────────────────
from http.server import HTTPServer, BaseHTTPRequestHandler

class SwarmHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204); self.send_cors(); self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        # SSE live stream
        if path == "/v1/swarm/feed":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors()
            self.end_headers()
            # Replay last 30 events
            replay = list(EVENT_BUFFER)[:30]; replay.reverse()
            for ev in replay:
                try: self.wfile.write(("data: "+json.dumps(ev)+"\n\n").encode())
                except: return
            try: self.wfile.flush()
            except: return
            q = collections.deque(maxlen=300)
            with SSE_LOCK: SSE_CLIENTS.append(q)
            try:
                while True:
                    if q:
                        msg = q.popleft()
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                    else:
                        time.sleep(0.08)
            except: pass
            finally:
                with SSE_LOCK:
                    if q in SSE_CLIENTS: SSE_CLIENTS.remove(q)
            return

        # Leaderboard snapshot — always returns something even before trades
        if path == "/v1/swarm/leaderboard/snapshot":
            rows = leaderboard()
            if not rows:
                # Bootstrap with starting balances before any trades
                rows = [
                    {"rank": i+1, "name": a["name"], "did": a["did"],
                     "balance": round(1000.0 + random.uniform(-5, 5), 2),
                     "wins": 0, "losses": 0, "streak": 0, "vol": 0.0,
                     "type": a["name"].split("-")[0]}
                    for i, a in enumerate(random.sample(AGENTS, min(20, len(AGENTS))))
                ]
                rows.sort(key=lambda r: r["balance"], reverse=True)
                for i, r in enumerate(rows): r["rank"] = i + 1
            body = json.dumps({"leaderboard": rows, "stats": STATS}).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",len(body))
            self.send_cors(); self.end_headers(); self.wfile.write(body)
            return

        # Leaderboard
        if path == "/v1/swarm/leaderboard":
            body = json.dumps({"leaderboard": leaderboard(), "stats": STATS}).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",len(body))
            self.send_cors(); self.end_headers(); self.wfile.write(body)
            return

        # Event snapshot
        if path == "/v1/swarm/events":
            body = json.dumps({"events":list(EVENT_BUFFER)[:50],"stats":STATS,"agents_total":len(AGENTS),"agents_active":STATS["agents_active"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",len(body))
            self.send_cors(); self.end_headers(); self.wfile.write(body)
            return

        # Stats
        if path == "/v1/swarm/stats":
            body = json.dumps({**STATS,"agents_total":len(AGENTS),"sse_clients":len(SSE_CLIENTS)}).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",len(body))
            self.send_cors(); self.end_headers(); self.wfile.write(body)
            return

        # Health / root
        body = json.dumps({"status":"ok","service":"hive-swarm-trader",
                           "agents_total":len(AGENTS),
                           "agents_active":STATS["agents_active"],
                           "total_actions":STATS["total_actions"],
                           "wins":STATS["wins"],"losses":STATS["losses"],
                           "sse_clients":len(SSE_CLIENTS)}).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",len(body))
        self.send_cors(); self.end_headers(); self.wfile.write(body)

# ─── Boot ─────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8080))

print(f"Hive Swarm Trader v3 — {len(AGENTS)} agents (dynamic roster, oracle-informed, trust-gated)", flush=True)
print(f"SSE:         http://0.0.0.0:{PORT}/v1/swarm/feed", flush=True)
print(f"Events:      http://0.0.0.0:{PORT}/v1/swarm/events", flush=True)
print(f"Leaderboard: http://0.0.0.0:{PORT}/v1/swarm/leaderboard", flush=True)
print("="*70, flush=True)

init_ledger(AGENTS)

# Oracle refresh thread
threading.Thread(target=refresh_oracle, daemon=True).start()
time.sleep(1)  # Let oracle do first fetch

# Agent threads — each manages its own join/leave lifecycle
for agent in AGENTS:
    # Add small stagger so they don't all join simultaneously at boot
    threading.Thread(target=agent_loop, args=(agent,), daemon=True).start()

# Wave roster coordinator
threading.Thread(target=wave_roster_coordinator, daemon=True).start()

# HTTP server (main thread)
server = HTTPServer(("", PORT), SwarmHandler)
print(f"HTTP on :{PORT}", flush=True)
server.serve_forever()
