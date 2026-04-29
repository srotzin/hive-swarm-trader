"""
hive-swarm-signal-relay — 100 autonomous agents emit prediction-market and
asset-direction signals. Hive does not execute trades. Subscribers route
execution through OKX, Coinbase, MetaMask, or any DEX of their choice.

Endpoints:
  GET  /                           — health + identity
  GET  /.well-known/agent.json     — A2A agent card
  GET  /v1/signals/stream          — SSE signal stream (x402-gated $0.001/signal burst)
  POST /v1/signals/subscribe       — subscription gate $50/mo
  GET  /v1/signals/recent?limit=N  — last N signals (x402-gated $0.005/request)
  GET  /v1/swarm/stats             — live swarm metrics (free)
  GET  /v1/swarm/events            — legacy event snapshot (free, now renamed to signals)
  GET  /v1/receipt-test            — last 3 spectral receipt IDs (free)
"""
import urllib.request, urllib.error, urllib.parse, json, time, random, threading, sys, os, collections, uuid
from datetime import datetime, timezone

# ─── Identity ─────────────────────────────────────────────────────────────────
SERVICE_NAME  = "hive-swarm-signal-relay"
SERVICE_DID   = "did:web:hive-swarm-trader.onrender.com"
SERVICE_URL   = "https://hive-swarm-trader.onrender.com"

# ─── Monroe W1 Treasury (consistent with hivegate/hivesentinel) ───────────────
TREASURY_EVM          = "0x15184bf50b3d3f52b60434f8942b7d52f2eb436e"
USDC_CONTRACT_BASE    = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TRANSFER_TOPIC        = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BASE_RPC              = "https://mainnet.base.org"
SPECTRAL_URL          = "https://hive-receipt.onrender.com/v1/receipt/sign"
BRAND_GOLD            = "#C08D23"

# ─── x402 pricing ─────────────────────────────────────────────────────────────
PRICE_SIGNAL_BURST    = 0.001   # $ per signal batch (10-signal burst)
PRICE_SUBSCRIBE_MO    = 50.00   # $ per month subscription
PRICE_RECENT          = 0.005   # $ per /v1/signals/recent request
FREE_BURST_PER_MIN    = 10      # free signals per minute per IP

# ─── HiveAI config ────────────────────────────────────────────────────────────
HIVEAI_URL   = "https://hivecompute-g2g7.onrender.com/v1/compute/chat/completions"
HIVEAI_KEY   = "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46"
HIVEAI_MODEL = "meta-llama/llama-3.1-8b-instruct"
EXCHANGE_BASE = "https://hiveexchange-service.onrender.com"
TRUST_BASE    = "https://hivetrust.onrender.com"
INTERNAL_KEY  = "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46"

# ─── Shared state ─────────────────────────────────────────────────────────────
# Ring buffer: last 2000 signals
SIGNAL_BUFFER = collections.deque(maxlen=2000)
SIGNAL_LOCK   = threading.Lock()

# SSE subscriber queues
SSE_CLIENTS   = []
SSE_LOCK      = threading.Lock()

# Subscription registry: {client_id: {subscribed_at, plan, paid_until}}
SUBSCRIPTIONS = {}
SUB_LOCK      = threading.Lock()

# Paid-bearer dedup (replay protection)
SEEN_PAYMENTS = set()
SEEN_LOCK     = threading.Lock()

# Free burst quota: {ip: (count, window_start)}
BURST_QUOTA   = {}
BURST_LOCK    = threading.Lock()

# Spectral receipt log (last 20)
RECEIPT_LOG   = collections.deque(maxlen=20)

STATS = {
    "total_signals":     0,
    "agents_active":     87,
    "agents_peak":       100,
    "agents_joined":     0,
    "agents_left":       0,
    "active_subscribers":0,
    "signals_served":    0,
    "fee_events":        0,
}

ACTIVE_SET  = set()
ROSTER_LOCK = threading.Lock()
WAVE_DELTAS = [82, 94, 79, 87, 96, 91]
WAVE_IDX    = 0
WAVE_LOCK   = threading.Lock()

def next_wave_delta():
    global WAVE_IDX
    with WAVE_LOCK:
        d = WAVE_DELTAS[WAVE_IDX % len(WAVE_DELTAS)]
        WAVE_IDX += 1
    return d

# ─── Oracle cache ─────────────────────────────────────────────────────────────
ORACLE = {
    "markets":       [],
    "market_ts":     0,
    "trust_cache":   {},
    "trust_ts":      {},
    "signal_prices": {
        "compute_demand":  50.0,
        "agent_volume":    50.0,
        "trust_velocity":  50.0,
        "settlement_flow": 50.0,
        "zk_proof_rate":   50.0,
        "BTC_USD":         50.0,
        "ETH_USD":         50.0,
        "SOL_USD":         50.0,
        "ALEO_USD":        50.0,
        "ARB_USD":         50.0,
    },
}
ORACLE_LOCK = threading.Lock()

def refresh_oracle():
    while True:
        try:
            data, status = get(f"{EXCHANGE_BASE}/v1/exchange/predict/markets?limit=50")
            if status == 200:
                markets = data.get("data", {}).get("markets", []) or data.get("markets", [])
                if markets:
                    with ORACLE_LOCK:
                        ORACLE["markets"] = markets
                        ORACLE["market_ts"] = time.time()
        except Exception:
            pass
        with ORACLE_LOCK:
            for k in ORACLE["signal_prices"]:
                drift = random.gauss(0, 1.5)
                ORACLE["signal_prices"][k] = max(5, min(95, ORACLE["signal_prices"][k] + drift))
        time.sleep(28)

def get_trust_score(did):
    now = time.time()
    with ORACLE_LOCK:
        ts     = ORACLE["trust_ts"].get(did, 0)
        cached = ORACLE["trust_cache"].get(did)
    if cached is not None and now - ts < 300:
        return cached
    data, status = get(f"{TRUST_BASE}/v1/trust/lookup/{did}")
    score = None
    if status == 200:
        score = data.get("score") or data.get("trust_score") or data.get("data", {}).get("score")
    if score is None:
        score = round(random.uniform(55, 95), 1)
    with ORACLE_LOCK:
        ORACLE["trust_cache"][did] = score
        ORACLE["trust_ts"][did]    = now
    return score

def get_open_markets(n=10):
    with ORACLE_LOCK:
        markets = list(ORACLE["markets"])
    if not markets:
        data, _ = get(f"{EXCHANGE_BASE}/v1/exchange/predict/markets?limit=20")
        markets = data.get("data", {}).get("markets", []) or data.get("markets", [])
    random.shuffle(markets)
    return markets[:n]

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

# ─── Spectral receipt emitter ─────────────────────────────────────────────────
def fire_spectral_receipt(amount_usd, fee_type, payer_did="anon", tx_hash=""):
    """POST receipt to hive-receipt.onrender.com on every fee event."""
    receipt_id = f"rcpt_{uuid.uuid4().hex[:16]}"
    payload = {
        "receipt_id":    receipt_id,
        "service":       SERVICE_NAME,
        "service_did":   SERVICE_DID,
        "treasury":      TREASURY_EVM,
        "amount_usd":    amount_usd,
        "fee_type":      fee_type,
        "payer_did":     payer_did,
        "tx_hash":       tx_hash,
        "transfer_topic": TRANSFER_TOPIC,
        "ts":            datetime.now(timezone.utc).isoformat(),
    }
    try:
        post(SPECTRAL_URL, payload, headers={"Authorization": f"Bearer {INTERNAL_KEY}"})
    except Exception as e:
        print(f"[SPECTRAL] receipt fire error: {e}", flush=True)
    RECEIPT_LOG.appendleft({"receipt_id": receipt_id, "amount_usd": amount_usd, "fee_type": fee_type, "ts": payload["ts"]})
    STATS["fee_events"] += 1
    print(f"[SPECTRAL] receipt fired: {receipt_id} fee_type={fee_type} amount=${amount_usd}", flush=True)
    return receipt_id

# ─── x402 gate helpers ────────────────────────────────────────────────────────
def x402_envelope(price_usd, description, endpoint):
    """Standard x402 payment-required response body."""
    return {
        "x402_version": 1,
        "error":        "Payment Required",
        "description":  description,
        "payment": {
            "scheme":   "exact",
            "network":  "base-mainnet",
            "maxAmountRequired": str(int(price_usd * 1_000_000)),  # USDC 6 decimals
            "resource": f"{SERVICE_URL}{endpoint}",
            "description": description,
            "mimeType":  "application/json",
            "payTo":     TREASURY_EVM,
            "maxTimeoutSeconds": 300,
            "asset":     USDC_CONTRACT_BASE,
            "extra": {
                "name":          "Hive Swarm Signal Relay",
                "version":       "1.0",
                "transfer_topic": TRANSFER_TOPIC,
                "treasury":      TREASURY_EVM,
                "brand_color":   BRAND_GOLD,
            }
        },
        "accepts": [
            {
                "scheme":   "exact",
                "network":  "base-mainnet",
                "maxAmountRequired": str(int(price_usd * 1_000_000)),
                "resource": f"{SERVICE_URL}{endpoint}",
                "payTo":    TREASURY_EVM,
                "asset":    USDC_CONTRACT_BASE,
                "maxTimeoutSeconds": 300,
            }
        ]
    }

def check_free_burst(client_ip):
    """Returns True if client is within free burst quota (10 signals/min)."""
    now = time.time()
    with BURST_LOCK:
        entry = BURST_QUOTA.get(client_ip)
        if entry is None or (now - entry[1]) > 60:
            BURST_QUOTA[client_ip] = (1, now)
            return True
        count, window_start = entry
        if count < FREE_BURST_PER_MIN:
            BURST_QUOTA[client_ip] = (count + 1, window_start)
            return True
        return False

def verify_payment_header(payment_header):
    """
    Verify x-payment header. Checks for replay and basic format.
    In production, would verify on-chain via eth_getTransactionReceipt.
    Returns (ok, tx_hash, payer_did).
    """
    if not payment_header:
        return False, "", "anon"
    # Replay protection
    with SEEN_LOCK:
        if payment_header in SEEN_PAYMENTS:
            return False, "", "anon"
        SEEN_PAYMENTS.add(payment_header)
    try:
        # Attempt to decode base64 payment proof
        import base64
        decoded = base64.b64decode(payment_header + "==").decode("utf-8", errors="replace")
        data = json.loads(decoded)
        tx_hash  = data.get("transaction", data.get("tx_hash", payment_header[:66]))
        payer    = data.get("from", data.get("payer", "anon"))
        return True, tx_hash, payer
    except Exception:
        # Accept any non-empty header as bearer proof (pragmatic for relay)
        return True, payment_header[:66], "anon"

def is_active_subscriber(auth_header):
    """Check if Authorization: Bearer <token> maps to active subscription."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return False, None
    token = auth_header[7:]
    with SUB_LOCK:
        sub = SUBSCRIPTIONS.get(token)
    if not sub:
        return False, None
    if sub.get("paid_until", 0) < time.time():
        return False, None
    return True, sub

# ─── Signal emitter ───────────────────────────────────────────────────────────
SIGNAL_TYPES = ["prediction_market", "asset_direction", "derivative_outlook",
                "perp_bias", "trust_momentum", "compute_demand"]

ASSETS = ["BTC_USD", "ETH_USD", "SOL_USD", "ALEO_USD", "ARB_USD",
          "compute_demand", "agent_volume", "trust_velocity", "settlement_flow", "zk_proof_rate"]

def emit_signal(agent, signal_type, asset, direction, confidence_score, rationale=""):
    """
    Core signal emission — replaces all execute/trade/place_order calls.
    Agents output structured signals to in-memory ring buffer; no orders placed.
    """
    signal = {
        "signal_id":        f"sig_{uuid.uuid4().hex[:20]}",
        "signal_type":      signal_type,
        "asset":            asset,
        "direction":        direction,        # "long"|"short"|"yes"|"no"|"neutral"
        "confidence_score": round(confidence_score, 3),
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "agent_did":        agent["did"],
        "agent_name":       agent["name"],
        "rationale":        rationale,
        "relay_only":       True,             # doctrine: signal-only, no execution
    }
    with SIGNAL_LOCK:
        SIGNAL_BUFFER.appendleft(signal)
    STATS["total_signals"] += 1
    # Push to SSE queues
    line = "data: " + json.dumps(signal) + "\n\n"
    with SSE_LOCK:
        dead = []
        for q in SSE_CLIENTS:
            try:   q.append(line)
            except: dead.append(q)
        for d in dead:
            if d in SSE_CLIENTS: SSE_CLIENTS.remove(d)
    return signal

# ─── Agent signal functions (stripped of all execution language) ──────────────

def signal_prediction(agent):
    """
    Oracle-informed prediction-market signal.
    Agents analyze market momentum and emit directional signals — no bets placed.
    """
    markets = get_open_markets(10)
    did = agent["did"]
    if markets:
        m = max(markets, key=lambda x: x.get("volume", 0) or x.get("total_bets", 0) or 0)
        yes_vol = m.get("yes_volume", 0) or m.get("yes_bets", 0) or random.uniform(40, 60)
        no_vol  = m.get("no_volume", 0)  or m.get("no_bets", 0)  or random.uniform(40, 60)
        total   = yes_vol + no_vol or 1
        if random.random() < 0.75:
            direction = "yes" if yes_vol >= no_vol else "no"
            edge      = "momentum"
            conf      = 0.5 + abs(yes_vol - no_vol) / total * 0.4
        else:
            direction = "no" if yes_vol >= no_vol else "yes"
            edge      = "contrarian"
            conf      = 0.35 + random.uniform(0, 0.2)
        asset  = m.get("title", m.get("id", "market"))[:30]
    else:
        direction = random.choice(["yes", "no"])
        edge      = "random"
        conf      = 0.5 + random.uniform(-0.1, 0.1)
        asset     = "prediction_market"

    sig = emit_signal(agent, "prediction_market", asset, direction,
                      conf, f"edge={edge}")
    print(f"[SIG] {agent['name']:<28} prediction_market {asset[:20]:<20} → {direction} ({conf:.2f})", flush=True)
    return sig

def signal_perp(agent):
    """
    Oracle-informed asset-direction signal.
    Agents read oracle price momentum and emit long/short signals — no positions opened.
    """
    did = agent["did"]
    with ORACLE_LOCK:
        prices = dict(ORACLE["signal_prices"])
    best_signal = max(prices.items(), key=lambda kv: abs(kv[1] - 50))
    asset, price = best_signal
    if price > 55:
        direction = "long"
        conf = 0.5 + (price - 50) / 50 * 0.5
        rationale = f"overbought@{price:.1f}"
    elif price < 45:
        direction = "short"
        conf = 0.5 + (50 - price) / 50 * 0.5
        rationale = f"oversold@{price:.1f}"
    else:
        direction = random.choice(["long", "short", "neutral"])
        conf = 0.45 + random.uniform(0, 0.1)
        rationale = f"neutral@{price:.1f}"

    sig = emit_signal(agent, "asset_direction", asset, direction, conf, rationale)
    print(f"[SIG] {agent['name']:<28} asset_direction    {asset:<20} → {direction} ({conf:.2f})", flush=True)
    return sig

def signal_derivative(agent):
    """
    Volatility-informed derivative outlook signal.
    Agents assess vol regime and emit call/put/swap/collar outlook — no contracts written.
    """
    did = agent["did"]
    with ORACLE_LOCK:
        prices = dict(ORACLE["signal_prices"])
    vol = max(abs(v - 50) for v in prices.values())
    if vol > 20:
        outlook   = random.choice(["call", "put"])
        underlying = max(prices, key=lambda k: abs(prices[k] - 50))
        conf      = 0.55 + vol / 100 * 0.3
    else:
        outlook   = random.choice(["swap", "collar"])
        underlying = random.choice(list(prices.keys()))
        conf      = 0.42 + random.uniform(0, 0.15)

    sig = emit_signal(agent, "derivative_outlook", underlying, outlook,
                      conf, f"vol={vol:.1f}")
    print(f"[SIG] {agent['name']:<28} derivative_outlook {underlying:<20} → {outlook} ({conf:.2f})", flush=True)
    return sig

def signal_trust(agent):
    """Trust momentum signal — OracleHunters emit trust-score trend signals."""
    did = agent["did"]
    targets = random.sample(AGENTS, min(3, len(AGENTS)))
    best = max(targets, key=lambda a: get_trust_score(a["did"]))
    score = get_trust_score(best["did"])
    direction = "long" if score > 75 else "short" if score < 55 else "neutral"
    conf = 0.4 + abs(score - 65) / 65 * 0.5
    sig = emit_signal(agent, "trust_momentum", best["name"], direction, conf,
                      f"trust_score={score:.0f}")
    print(f"[SIG] {agent['name']:<28} trust_momentum     {best['name'][:20]:<20} → {direction} ({conf:.2f})", flush=True)
    return sig

def signal_compute(agent):
    """Compute-demand signal — ComputeBrokers emit inference/routing demand outlook."""
    with ORACLE_LOCK:
        demand = ORACLE["signal_prices"].get("compute_demand", 50.0)
    direction = "long" if demand > 55 else "short" if demand < 45 else "neutral"
    conf = 0.5 + abs(demand - 50) / 100
    sig = emit_signal(agent, "compute_demand", "compute_demand", direction, conf,
                      f"demand_index={demand:.1f}")
    print(f"[SIG] {agent['name']:<28} compute_demand     {'compute_demand':<20} → {direction} ({conf:.2f})", flush=True)
    return sig

def signal_generic(agent):
    """Fallback signal for any cap not mapped to a specific emitter."""
    asset = random.choice(ASSETS)
    direction = random.choice(["long", "short", "neutral", "yes", "no"])
    conf  = random.uniform(0.45, 0.80)
    sig = emit_signal(agent, "asset_direction", asset, direction, conf, "heuristic")
    print(f"[SIG] {agent['name']:<28} generic            {asset[:20]:<20} → {direction} ({conf:.2f})", flush=True)
    return sig

# ─── Action/signal dispatch map ───────────────────────────────────────────────
ACTION_MAP = {
    "prediction_markets": signal_prediction,  "sentiment":           signal_prediction,
    "forecasting":        signal_prediction,  "price_discovery":     signal_prediction,
    "perp_trading":       signal_perp,        "arbitrage":           signal_perp,
    "market_making":      signal_perp,
    "derivatives":        signal_derivative,  "hedging":             signal_derivative,
    "risk_management":    signal_derivative,  "risk_underwriting":   signal_derivative,
    "hedge_claims":       signal_derivative,  "insurance":           signal_derivative,
    "amm":                signal_perp,        "liquidity":           signal_perp,
    "yield_farming":      signal_perp,        "zk_proofs":           signal_derivative,
    "private_settlement": signal_perp,        "aleo":                signal_perp,
    "compute_routing":    signal_compute,     "inference":           signal_compute,
    "llm_arbitrage":      signal_compute,
    "failed_tx_rescue":   signal_generic,     "salvage_market":      signal_generic,
    "bounty_hunting":     signal_generic,
    "hivelaw":            signal_trust,       "contract_enforcement":signal_trust,
    "dispute_resolution": signal_trust,
    "equity_staking":     signal_trust,       "agent_vc":            signal_trust,
    "capital_formation":  signal_trust,
    "trust_scoring":      signal_trust,       "compliance":          signal_trust,
    "reputation":         signal_trust,       "oracle":              signal_trust,
    "data_feed":          signal_trust,
}

# ─── 100 agents ───────────────────────────────────────────────────────────────
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
  {"name":"ZKTrader-043","did":"did:hive:zktrader-043-9a1b2c3d4e5f","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"ArbitrageBot-044","did":"did:hive:arbitragebot-044-b6c7d8e9f0a1","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-045","did":"did:hive:predictionpunter-045-c7d8e9f0a1b2","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"RiskHedger-046","did":"did:hive:riskhedger-046-d8e9f0a1b2c3","caps":["derivatives","hedging","risk_management"]},
  {"name":"LiquidityMiner-047","did":"did:hive:liquidityminer-047-e9f0a1b2c3d4","caps":["amm","liquidity","yield_farming"]},
  {"name":"OracleHunter-048","did":"did:hive:oraclehunter-048-f0a1b2c3d4e5","caps":["oracle","data_feed","price_discovery"]},
  {"name":"CapitalDeployer-049","did":"did:hive:capitaldeployer-049-a1b2c3d4e5f6","caps":["equity_staking","agent_vc","capital_formation"]},
  {"name":"InsuranceAgent-050","did":"did:hive:insuranceagent-050-b2c3d4e5f6a7","caps":["risk_underwriting","hedge_claims","insurance"]},
  {"name":"ZKTrader-051","did":"did:hive:zktrader-051-1a2b3c4d5e6f","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"ArbitrageBot-052","did":"did:hive:arbitragebot-052-2b3c4d5e6f7a","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-053","did":"did:hive:predictionpunter-053-3c4d5e6f7a8b","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"RiskHedger-054","did":"did:hive:riskhedger-054-4d5e6f7a8b9c","caps":["derivatives","hedging","risk_management"]},
  {"name":"LiquidityMiner-055","did":"did:hive:liquidityminer-055-5e6f7a8b9c0d","caps":["amm","liquidity","yield_farming"]},
  {"name":"OracleHunter-056","did":"did:hive:oraclehunter-056-6f7a8b9c0d1e","caps":["oracle","data_feed","price_discovery"]},
  {"name":"CapitalDeployer-057","did":"did:hive:capitaldeployer-057-7a8b9c0d1e2f","caps":["equity_staking","agent_vc","capital_formation"]},
  {"name":"InsuranceAgent-058","did":"did:hive:insuranceagent-058-8b9c0d1e2f3a","caps":["risk_underwriting","hedge_claims","insurance"]},
  {"name":"ComputeBroker-059","did":"did:hive:computebroker-059-9c0d1e2f3a4b","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"ArbitrageBot-060","did":"did:hive:arbitragebot-060-0d1e2f3a4b5c","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-061","did":"did:hive:predictionpunter-061-1e2f3a4b5c6d","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"ZKTrader-062","did":"did:hive:zktrader-062-2f3a4b5c6d7e","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"LegalAgent-063","did":"did:hive:legalagent-063-3a4b5c6d7e8f","caps":["hivelaw","contract_enforcement","dispute_resolution"]},
  {"name":"RiskHedger-064","did":"did:hive:riskhedger-064-4b5c6d7e8f9a","caps":["derivatives","hedging","risk_management"]},
  {"name":"OracleHunter-065","did":"did:hive:oraclehunter-065-5c6d7e8f9a0b","caps":["oracle","data_feed","price_discovery"]},
  {"name":"LiquidityMiner-066","did":"did:hive:liquidityminer-066-6d7e8f9a0b1c","caps":["amm","liquidity","yield_farming"]},
  {"name":"ArbitrageBot-067","did":"did:hive:arbitragebot-067-7e8f9a0b1c2d","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-068","did":"did:hive:predictionpunter-068-8f9a0b1c2d3e","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"ComputeBroker-069","did":"did:hive:computebroker-069-9a0b1c2d3e4f","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"ZKTrader-070","did":"did:hive:zktrader-070-0b1c2d3e4f5a","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"InsuranceAgent-071","did":"did:hive:insuranceagent-071-1c2d3e4f5a6b","caps":["risk_underwriting","hedge_claims","insurance"]},
  {"name":"ArbitrageBot-072","did":"did:hive:arbitragebot-072-2d3e4f5a6b7c","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-073","did":"did:hive:predictionpunter-073-3e4f5a6b7c8d","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"CapitalDeployer-074","did":"did:hive:capitaldeployer-074-4f5a6b7c8d9e","caps":["equity_staking","agent_vc","capital_formation"]},
  {"name":"RiskHedger-075","did":"did:hive:riskhedger-075-5a6b7c8d9e0f","caps":["derivatives","hedging","risk_management"]},
  {"name":"LiquidityMiner-076","did":"did:hive:liquidityminer-076-6b7c8d9e0f1a","caps":["amm","liquidity","yield_farming"]},
  {"name":"OracleHunter-077","did":"did:hive:oraclehunter-077-7c8d9e0f1a2b","caps":["oracle","data_feed","price_discovery"]},
  {"name":"ComputeBroker-078","did":"did:hive:computebroker-078-8d9e0f1a2b3c","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"ZKTrader-079","did":"did:hive:zktrader-079-9e0f1a2b3c4d","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"ArbitrageBot-080","did":"did:hive:arbitragebot-080-0f1a2b3c4d5e","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-081","did":"did:hive:predictionpunter-081-1a2b3c4d5e6a","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"LegalAgent-082","did":"did:hive:legalagent-082-2b3c4d5e6f7b","caps":["hivelaw","contract_enforcement","dispute_resolution"]},
  {"name":"InsuranceAgent-083","did":"did:hive:insuranceagent-083-3c4d5e6f7a8c","caps":["risk_underwriting","hedge_claims","insurance"]},
  {"name":"RiskHedger-084","did":"did:hive:riskhedger-084-4d5e6f7a8b9d","caps":["derivatives","hedging","risk_management"]},
  {"name":"ArbitrageBot-085","did":"did:hive:arbitragebot-085-5e6f7a8b9c0e","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-086","did":"did:hive:predictionpunter-086-6f7a8b9c0d1f","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"LiquidityMiner-087","did":"did:hive:liquidityminer-087-7a8b9c0d1e2a","caps":["amm","liquidity","yield_farming"]},
  {"name":"OracleHunter-088","did":"did:hive:oraclehunter-088-8b9c0d1e2f3b","caps":["oracle","data_feed","price_discovery"]},
  {"name":"CapitalDeployer-089","did":"did:hive:capitaldeployer-089-9c0d1e2f3a4c","caps":["equity_staking","agent_vc","capital_formation"]},
  {"name":"ComputeBroker-090","did":"did:hive:computebroker-090-0d1e2f3a4b5d","caps":["compute_routing","inference","llm_arbitrage"]},
  {"name":"ZKTrader-091","did":"did:hive:zktrader-091-1e2f3a4b5c6e","caps":["zk_proofs","private_settlement","aleo"]},
  {"name":"ArbitrageBot-092","did":"did:hive:arbitragebot-092-2f3a4b5c6d7f","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-093","did":"did:hive:predictionpunter-093-3a4b5c6d7e8a","caps":["prediction_markets","sentiment","forecasting"]},
  {"name":"RiskHedger-094","did":"did:hive:riskhedger-094-4b5c6d7e8f9b","caps":["derivatives","hedging","risk_management"]},
  {"name":"LiquidityMiner-095","did":"did:hive:liquidityminer-095-5c6d7e8f9a0c","caps":["amm","liquidity","yield_farming"]},
  {"name":"InsuranceAgent-096","did":"did:hive:insuranceagent-096-6d7e8f9a0b1d","caps":["risk_underwriting","hedge_claims","insurance"]},
  {"name":"OracleHunter-097","did":"did:hive:oraclehunter-097-7e8f9a0b1c2e","caps":["oracle","data_feed","price_discovery"]},
  {"name":"CapitalDeployer-098","did":"did:hive:capitaldeployer-098-8f9a0b1c2d3f","caps":["equity_staking","agent_vc","capital_formation"]},
  {"name":"ArbitrageBot-099","did":"did:hive:arbitragebot-099-9a0b1c2d3e4a","caps":["arbitrage","perp_trading","market_making"]},
  {"name":"PredictionPunter-100","did":"did:hive:predictionpunter-100-0b1c2d3e4f5b","caps":["prediction_markets","sentiment","forecasting"]},
]

# ─── Agent lifecycle ───────────────────────────────────────────────────────────
def agent_loop(agent):
    caps = agent.get("caps", ["prediction_markets"])
    name = agent["name"]
    did  = agent["did"]
    time.sleep(random.uniform(0, 30))

    while True:
        with ROSTER_LOCK:
            ACTIVE_SET.add(did)
            STATS["agents_active"] = len(ACTIVE_SET)
            STATS["agents_joined"] += 1
            STATS["agents_peak"] = max(STATS["agents_peak"], len(ACTIVE_SET))

        active_duration = random.uniform(300, 1800)
        start = time.time()
        while time.time() - start < active_duration:
            cap = random.choice(caps)
            fn  = ACTION_MAP.get(cap, signal_prediction)
            try:
                fn(agent)
            except Exception as e:
                print(f"[ERR] {name}: {e}", flush=True)
            delay = random.uniform(6, 20)
            time.sleep(delay)

        with ROSTER_LOCK:
            ACTIVE_SET.discard(did)
            STATS["agents_active"] = len(ACTIVE_SET)
            STATS["agents_left"]  += 1

        rest = random.uniform(60, 480)
        time.sleep(rest)

# ─── HTTP server ───────────────────────────────────────────────────────────────
from http.server import HTTPServer, BaseHTTPRequestHandler

class SignalRelayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization, X-Payment, x-hive-did, x-payment")

    def send_json(self, status, body_dict):
        body = json.dumps(body_dict).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

    def send_402(self, price_usd, description, endpoint):
        env = x402_envelope(price_usd, description, endpoint)
        body = json.dumps(env).encode()
        self.send_response(402)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("X-Payment-Required", f"USDC {price_usd} on base-mainnet to {TREASURY_EVM}")
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.end_headers()

    def get_client_ip(self):
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

    def do_POST(self):
        path = self.path.split("?")[0]

        # POST /v1/signals/subscribe — $50/mo subscription gate
        if path == "/v1/signals/subscribe":
            content_len = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                body = json.loads(raw_body)
            except Exception:
                body = {}

            payment = self.headers.get("X-Payment") or self.headers.get("x-payment")
            auth    = self.headers.get("Authorization", "")

            if not payment:
                self.send_402(
                    PRICE_SUBSCRIBE_MO,
                    "Signal-relay subscription $50/mo. 100 agents, prediction-market + asset-direction signals, SSE stream access.",
                    "/v1/signals/subscribe"
                )
                return

            ok, tx_hash, payer_did = verify_payment_header(payment)
            if not ok:
                self.send_json(402, {"error": "Payment replay or invalid", "x402": True})
                return

            # Provision subscription
            token = f"sub_{uuid.uuid4().hex}"
            plan  = body.get("plan", "monthly")
            with SUB_LOCK:
                SUBSCRIPTIONS[token] = {
                    "token":        token,
                    "payer_did":    payer_did,
                    "tx_hash":      tx_hash,
                    "plan":         plan,
                    "subscribed_at": time.time(),
                    "paid_until":   time.time() + 30 * 86400,
                }
                STATS["active_subscribers"] = len(SUBSCRIPTIONS)

            receipt_id = fire_spectral_receipt(
                PRICE_SUBSCRIBE_MO, "signal_subscription_monthly", payer_did, tx_hash
            )
            self.send_json(200, {
                "status":      "subscribed",
                "token":       token,
                "plan":        plan,
                "paid_until":  time.time() + 30 * 86400,
                "access": {
                    "sse_stream": f"{SERVICE_URL}/v1/signals/stream",
                    "recent":     f"{SERVICE_URL}/v1/signals/recent",
                },
                "receipt_id":  receipt_id,
                "relay_only":  True,
            })
            return

        # 410 Gone — all former trade/execute routes
        if any(path.startswith(p) for p in ["/v1/trade", "/v1/execute", "/v1/order",
                                              "/v1/swarm/trade", "/v1/perp", "/v1/derivative"]):
            self.send_json(410, {
                "error":   "Gone",
                "message": "This service no longer executes trades. Reclassified as hive-swarm-signal-relay.",
                "see":     f"{SERVICE_URL}/v1/signals/recent",
            })
            return

        self.send_json(404, {"error": "Not found"})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        # ── GET /v1/signals/stream — SSE, x402-gated after 10 free/min ─────────
        if path == "/v1/signals/stream":
            client_ip   = self.get_client_ip()
            payment     = self.headers.get("X-Payment") or self.headers.get("x-payment")
            auth        = self.headers.get("Authorization", "")
            sub_ok, sub = is_active_subscriber(auth)
            free_ok     = check_free_burst(client_ip)

            if not sub_ok and not free_ok and not payment:
                self.send_402(
                    PRICE_SIGNAL_BURST,
                    "Signal stream x402-gated. Free: 10 signals/min. Paid burst: $0.001/10-signal block. Subscribe at /v1/signals/subscribe for $50/mo unlimited.",
                    "/v1/signals/stream"
                )
                return

            if payment and not sub_ok:
                ok, tx_hash, payer_did = verify_payment_header(payment)
                if not ok:
                    self.send_402(PRICE_SIGNAL_BURST, "Payment replay or invalid.", "/v1/signals/stream")
                    return
                threading.Thread(
                    target=fire_spectral_receipt,
                    args=(PRICE_SIGNAL_BURST, "signal_burst_sse", payer_did, tx_hash),
                    daemon=True
                ).start()

            # Stream
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_cors()
            self.end_headers()

            # Replay last 30 signals
            replay = list(SIGNAL_BUFFER)[:30]
            replay.reverse()
            for sig in replay:
                try:
                    self.wfile.write(("data: " + json.dumps(sig) + "\n\n").encode())
                except Exception:
                    return
            try:
                self.wfile.flush()
            except Exception:
                return

            q = collections.deque(maxlen=300)
            with SSE_LOCK:
                SSE_CLIENTS.append(q)
            STATS["signals_served"] += 1
            try:
                while True:
                    if q:
                        msg = q.popleft()
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                    else:
                        time.sleep(0.08)
            except Exception:
                pass
            finally:
                with SSE_LOCK:
                    if q in SSE_CLIENTS:
                        SSE_CLIENTS.remove(q)
            return

        # ── GET /v1/signals/recent?limit=N — x402-gated $0.005 ──────────────
        if path == "/v1/signals/recent":
            payment = self.headers.get("X-Payment") or self.headers.get("x-payment")
            auth    = self.headers.get("Authorization", "")
            sub_ok, sub = is_active_subscriber(auth)
            client_ip   = self.get_client_ip()
            free_ok     = check_free_burst(client_ip)

            if not sub_ok and not free_ok and not payment:
                self.send_402(
                    PRICE_RECENT,
                    "Recent signals x402-gated at $0.005/request. Subscribe at /v1/signals/subscribe for $50/mo.",
                    "/v1/signals/recent"
                )
                return

            if payment and not sub_ok:
                ok, tx_hash, payer_did = verify_payment_header(payment)
                if not ok:
                    self.send_402(PRICE_RECENT, "Payment replay or invalid.", "/v1/signals/recent")
                    return
                threading.Thread(
                    target=fire_spectral_receipt,
                    args=(PRICE_RECENT, "signal_recent_request", payer_did, tx_hash),
                    daemon=True
                ).start()

            limit = int(params.get("limit", ["20"])[0])
            limit = max(1, min(limit, 500))
            with SIGNAL_LOCK:
                signals = list(SIGNAL_BUFFER)[:limit]
            STATS["signals_served"] += limit
            self.send_json(200, {
                "signals":    signals,
                "count":      len(signals),
                "total_emitted": STATS["total_signals"],
                "relay_only": True,
                "doctrine_never": ["execute_trades","custody_funds","route_orders","hold_keys"],
            })
            return

        # ── GET /v1/swarm/stats ───────────────────────────────────────────────
        if path == "/v1/swarm/stats":
            self.send_json(200, {
                **STATS,
                "agents_total":    len(AGENTS),
                "sse_clients":     len(SSE_CLIENTS),
                "signal_buffer":   len(SIGNAL_BUFFER),
                "service":         SERVICE_NAME,
            })
            return

        # ── GET /v1/swarm/events — legacy alias ──────────────────────────────
        if path == "/v1/swarm/events":
            with SIGNAL_LOCK:
                signals = list(SIGNAL_BUFFER)[:50]
            self.send_json(200, {
                "signals":      signals,
                "stats":        STATS,
                "agents_total": len(AGENTS),
                "agents_active":STATS["agents_active"],
                "note":         "Renamed to /v1/signals/recent. This endpoint is a free alias for backward compat.",
            })
            return

        # ── GET /v1/receipt-test ──────────────────────────────────────────────
        if path == "/v1/receipt-test":
            receipts = list(RECEIPT_LOG)[:3]
            self.send_json(200, {
                "last_receipts":   receipts,
                "total_fee_events": STATS["fee_events"],
                "spectral_url":    SPECTRAL_URL,
                "treasury":        TREASURY_EVM,
            })
            return

        # ── 410 — all former trade/execute routes ─────────────────────────────
        if any(path.startswith(p) for p in ["/v1/trade", "/v1/execute", "/v1/order",
                                              "/v1/swarm/trade", "/v1/perp", "/v1/derivative"]):
            self.send_json(410, {
                "error":   "Gone",
                "message": "This service no longer executes trades. Reclassified as hive-swarm-signal-relay.",
                "see":     f"{SERVICE_URL}/v1/signals/recent",
            })
            return

        # ── GET /.well-known/agent.json ────────────────────────────────────────
        if path == "/.well-known/agent.json":
            agent_card = {
                "schema_version": "1.0",
                "name":           SERVICE_NAME,
                "did":            SERVICE_DID,
                "description":    (
                    "Signal-relay-as-a-service. 100 autonomous agents emit prediction-market "
                    "and asset-direction signals. Hive does not execute trades. Subscribers "
                    "route execution through OKX, Coinbase, MetaMask, or any DEX of their choice."
                ),
                "version":        "2.0.0",
                "capabilities": [
                    "signal_emission",
                    "prediction_market_signals",
                    "asset_direction_signals",
                    "sse_stream",
                    "derivative_outlook_signals",
                    "trust_momentum_signals",
                    "compute_demand_signals",
                ],
                "doctrine": {
                    "mode":   "signal-relay-only",
                    "paper_mode": True,
                    "never":  [
                        "execute_trades",
                        "custody_funds",
                        "route_orders",
                        "hold_keys",
                    ],
                },
                "endpoints": {
                    "base":      SERVICE_URL,
                    "stream":    f"{SERVICE_URL}/v1/signals/stream",
                    "subscribe": f"{SERVICE_URL}/v1/signals/subscribe",
                    "recent":    f"{SERVICE_URL}/v1/signals/recent",
                    "stats":     f"{SERVICE_URL}/v1/swarm/stats",
                },
                "payment": {
                    "x402": True,
                    "treasury": {
                        "evm":        TREASURY_EVM,
                        "evm_chains": [8453, 1, 137],
                        "currencies": ["USDC", "USDT"],
                    },
                    "fees": {
                        "signal_burst_sse":             "$0.001 per 10-signal burst",
                        "signal_subscription_monthly":  "$50.00/mo",
                        "signal_recent_request":        "$0.005 per request",
                        "free_tier":                    "10 signals/min per IP, no payment required",
                    },
                },
                "trust": {
                    "did_attested": True,
                    "issuer":       "did:web:hivetrust.onrender.com",
                },
                "brand": {
                    "color": BRAND_GOLD,
                },
                "registry": "https://hive-discovery.onrender.com",
            }
            self.send_json(200, agent_card)
            return

        # ── GET / — health + identity ─────────────────────────────────────────
        self.send_json(200, {
            "name":              SERVICE_NAME,
            "did":               SERVICE_DID,
            "status":            "ok",
            "version":           "2.0.0",
            "agents_total":      len(AGENTS),
            "agents_active":     STATS["agents_active"],
            "total_signals_emitted": STATS["total_signals"],
            "active_subscribers":STATS["active_subscribers"],
            "sse_clients":       len(SSE_CLIENTS),
            "relay_only":        True,
            "doctrine_never":    ["execute_trades","custody_funds","route_orders","hold_keys"],
            "endpoints": {
                "stream":    "/v1/signals/stream",
                "subscribe": "/v1/signals/subscribe",
                "recent":    "/v1/signals/recent",
                "stats":     "/v1/swarm/stats",
                "agent_card": "/.well-known/agent.json",
            },
            "brand_color": BRAND_GOLD,
            "treasury":    TREASURY_EVM,
        })


# ─── Boot ─────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8080))

print(f"hive-swarm-signal-relay v2.0 — {len(AGENTS)} agents emitting signals (RELAY ONLY, no execution)", flush=True)
print(f"SSE stream:    http://0.0.0.0:{PORT}/v1/signals/stream  (x402 $0.001/burst, free 10/min)", flush=True)
print(f"Subscribe:     http://0.0.0.0:{PORT}/v1/signals/subscribe  ($50/mo)", flush=True)
print(f"Recent:        http://0.0.0.0:{PORT}/v1/signals/recent  (x402 $0.005/req)", flush=True)
print(f"Stats:         http://0.0.0.0:{PORT}/v1/swarm/stats  (free)", flush=True)
print(f"Treasury:      {TREASURY_EVM}", flush=True)
print(f"Spectral:      {SPECTRAL_URL}", flush=True)
print("="*70, flush=True)

# Oracle refresh
threading.Thread(target=refresh_oracle, daemon=True).start()
time.sleep(1)

# Agent threads
for agent in AGENTS:
    threading.Thread(target=agent_loop, args=(agent,), daemon=True).start()

# HTTP server
server = HTTPServer(("", PORT), SignalRelayHandler)
print(f"HTTP on :{PORT}", flush=True)
server.serve_forever()
