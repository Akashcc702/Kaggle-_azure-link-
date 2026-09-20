# ============================================================
# CC AlgoTrading — Nifty Smallcap Intraday Engine v3.2
# smallcap_intraday_engine.py
#
# MASTER QUANT FEATURES (QLIB POWERED BI-DIRECTIONAL):
#   1. Qlib Micro-Alpha30 Engine (30 Institutional Quant Factors)
#   2. Qlib Cross-Sectional Percentile Rank (CS-Rank 0.0% - 100.0%)
#   3. Rolling Spearman Information Coefficient (IC) Dynamic Weighting
#   4. Qlib Smart TWAP/VWAP Execution Slicer (SOR v2 Slippage Mitigation)
#   5. Bi-Directional Execution (Long + Short Selling)
#   6. 5 Zero-Risk Short Guards (Circuit Band Distance Guard, etc.)
#   7. Sector Concentration Guard (Max 1 stock per sector)
#   8. India VIX Adaptive Sizing & Target Tuning
#   9. Telegram Visual Chart Delivery (Matplotlib EOD Chart)
#  10. WebSocket Streaming with Automatic REST Fallback
# ============================================================
import json, time, configparser, sys, os, threading
from pathlib import Path
from datetime import datetime, date
import numpy as np
import pandas as pd
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import telegram_alerts as tg
from shoonya_auth import get_valid_session
from watchlist import (
    get_all_symbols, get_tier1_symbols, get_tier2_symbols,
    get_noise_penalty, CORE_HIGH_ALPHA_10, CORE_HIGH_ALPHA_SHORT_5,
    HIGH_NOISE_PENALTY_LIST
)

BASE_DIR     = Path(__file__).parent
CONFIG_FILE  = BASE_DIR / "config.ini"
SESSION_FILE = BASE_DIR / ".shoonya_session.json"
TRADES_FILE  = BASE_DIR / f"trades_{date.today().strftime('%Y%m%d')}.json"
DATA_DIR     = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

config = configparser.ConfigParser()
config.read_string(open(CONFIG_FILE, "r", encoding="utf-8-sig").read())

REAL_MODE      = config.getboolean("TRADING", "REAL_MODE",      fallback=False)
CAPITAL        = config.getfloat  ("TRADING", "CAPITAL",         fallback=100000)
MAX_POSITIONS  = config.getint    ("TRADING", "MAX_POSITIONS",   fallback=3)
MIN_PRICE      = config.getfloat  ("FILTERS", "MIN_PRICE",       fallback=50)
MAX_PRICE      = config.getfloat  ("FILTERS", "MAX_PRICE",       fallback=2500)
MIN_TURN_CR    = config.getfloat  ("FILTERS", "MIN_TURNOVER_CR", fallback=0.50)

# Default base SL / Target (adapted dynamically by VIX)
BASE_SL_PCT    = config.getfloat  ("TRADING", "STOP_LOSS_PCT",   fallback=1.0)
BASE_TGT_PCT   = config.getfloat  ("TRADING", "TARGET_PCT",      fallback=2.5)

BREAKEVEN_LOCK_PCT       = 1.0   # Lock SL at cost after +1.0% favorable move
TRAIL_STEP_PCT           = 0.5   # Move SL every +0.5% favorable move after breakeven
MAX_DAILY_LOSS_PCT       = 2.0   # Circuit breaker: halt if -2% of capital
WAVE2_SCAN_HOUR          = 9
WAVE2_SCAN_MIN           = 45    # Wave 2 scan at 09:45 AM

# 5 Short-Selling Risk Guards Constants
MIN_CIRCUIT_DISTANCE_PCT = 5.0   # Guard 1: Upper Circuit distance must be >= 5% to short
REGIME_BEARISH_PCT       = -0.20 # Guard 2: Nifty < -0.20% triggers Bearish Short Regime
REGIME_CRASH_PCT         = -0.70 # Guard 2: Nifty < -0.70% blocks Longs completely
SHORT_KILL_SWITCH_HOUR   = 15    # Guard 5: Force close Short positions at 15:05 PM
SHORT_KILL_SWITCH_MIN    = 5

# Qlib CS-Rank Thresholds
CS_RANK_LONG_MIN         = 85.0  # Top Decile Alpha Conviction (Buy)
CS_RANK_SHORT_MAX        = 15.0  # Bottom Decile Relative Weakness (Short)

# ── Institutional Top 6 Quant Features Constants ─────────────
VWAP_VAL_DEVIATION_MULT  = 1.0   # Feature 1: VAH = VWAP + 1.0*sigma, VAL = VWAP - 1.0*sigma
OBI_TOXIC_THRESHOLD      = -0.30 # Feature 2: OBI < -0.30 indicates toxic seller dumping
OBI_PUMP_THRESHOLD       = 0.30  # Feature 2: OBI > +0.30 indicates toxic buyer surge
CHANDELIER_ATR_PERIOD    = 14    # Feature 3: ATR-14 for volatility trailing
CHANDELIER_ATR_MULT      = 2.5   # Feature 3: Trail distance = 2.5 * ATR
NIFTY_PCR_BEAR_MAX       = 0.75  # Feature 4: Nifty PCR < 0.75 blocks Long entries
NIFTY_PCR_BULL_MIN       = 1.25  # Feature 4: Nifty PCR > 1.25 blocks Short entries
FLASH_DROP_PCT_THRESHOLD = -1.8  # Feature 5: Emergency exit if drop >= 1.8% in 45s
FLASH_DROP_WINDOW_SEC    = 45    # Feature 5: Rapid drop window (seconds)
TCA_HIGH_SLIPPAGE_BPS    = 8.0   # Feature 6: 0.08% / 8 bps triggers adaptive micro-slicing
TCA_FILE                 = DATA_DIR / f"tca_analytics_{date.today().strftime('%Y%m%d')}.json"

# ── Institutional High-Profit & Strict Safety (Tier-2 Advanced Quant) Constants ──
MTF_EMA_FAST             = 20    # Feature 1 (Tier-2): MTF fast EMA (15m / 60m trend)
MTF_EMA_SLOW             = 50    # Feature 1 (Tier-2): MTF slow EMA
MIN_RVOL_SURGE           = config.getfloat("FILTERS", "MIN_RVOL", fallback=1.5) # Feature 2 (Tier-2): Minimum RVOL vs 20-period baseline
SCALE_OUT_T1_PCT         = config.getfloat("TRADING", "SCALE_OUT_T1_PCT", fallback=1.2) # Feature 3 (Tier-2): Target-1 partial profit scale out (50% exit)
SECTOR_RS_LOOKBACK_BARS  = 10    # Feature 4 (Tier-2): Sector relative strength momentum lookback

# ── Institutional Profit-Doubler (Tier-3 5-Feature Engine) Constants ──
GOLDEN_WINDOW_AM_START   = 918   # Feature 1: 09:18 AM IST
GOLDEN_WINDOW_AM_END     = 1045  # Feature 1: 10:45 AM IST
GOLDEN_WINDOW_PM_START   = 1315  # Feature 1: 01:15 PM IST
GOLDEN_WINDOW_PM_END     = 1445  # Feature 1: 02:45 PM IST
MIDDAY_CHOP_START        = 1115  # Feature 1: 11:15 AM IST (Hard Block on entries)
MIDDAY_CHOP_END          = 1315  # Feature 1: 01:15 PM IST
PYRAMID_TRIGGER_GAIN_PCT = 0.80  # Feature 2: +0.80% minimum profit to trigger risk-free pyramiding
PYRAMID_ADD_RATIO        = 0.25  # Feature 2: +25% size expansion on runners
PYRAMID_CS_RANK_MIN      = 92.0  # Feature 2: CS-Rank threshold for high-conviction runner add-on
SQUEEZE_TARGET_PCT       = 4.2   # Feature 4: Expanded target (+4.2%) when short-squeeze velocity confirmed
RISK_PARITY_RUPEE_RISK   = 350.0 # Feature 5: Equalized rupee risk per position on ₹100k capital (0.35%)

# ── Tier-4 6 Advanced Profit-Doubler Quant Features Constants ──
MICRO_IMBALANCE_MIN_LONG     = 0.40   # Feature 1: Imbalance >= +0.40 confirms buyer surge
MICRO_IMBALANCE_REJECT_LONG  = -0.20  # Feature 1: Imbalance < -0.20 rejects buy entry
MICRO_IMBALANCE_MIN_SHORT    = -0.40  # Feature 1: Imbalance <= -0.40 confirms seller surge
MICRO_IMBALANCE_REJECT_SHORT = 0.20   # Feature 1: Imbalance > +0.20 rejects short entry
VPIN_TOXIC_THRESHOLD         = 0.65   # Feature 2: VPIN > 0.65 indicates toxic dumping
NIFTY_LEAD_LAG_SURGE_PCT     = 0.20   # Feature 3: Nifty 5m surge >= +0.20% triggers catch-up
STOCK_LAG_MAX_PCT            = 0.30   # Feature 3: Stock has lagged if current 5m < +0.30%
YANG_ZHANG_LOOKBACK          = 20     # Feature 4: 20-bar lookback for Yang-Zhang vol
YANG_ZHANG_EXPANDED_TGT      = 4.0    # Feature 4: Expanded target (+4.0%) when YZ vol expands
YANG_ZHANG_COMPRESSED_TGT    = 1.8    # Feature 4: Quick lock target (+1.8%) when YZ vol is quiet
KELLY_FRACTION_MULT          = 0.25   # Feature 5: Quarter-Kelly fractional multiplier
TIME_STOP_MAX_MINUTES        = 35     # Feature 6: 35 minutes max dead capital hold
TIME_STOP_FLAT_MIN_PCT       = -0.30  # Feature 6: Flat return lower bound
TIME_STOP_FLAT_MAX_PCT       = 0.70   # Feature 6: Flat return upper bound

# ── Tier-5 Next-Gen 6 Institutional Profit-Doubler Constants ────
PRECLOSE_UNWIND_START        = "15:08" # Feature 1: Almgren-Chriss 15:08 Adaptive TWAP Exit start
PRECLOSE_UNWIND_END          = "15:15" # Feature 1: Mandatory hard close
CFR_SPOOFING_THRESHOLD       = 4.0     # Feature 2: Cancel-to-Fill / Fake Bid-Wall depth ratio threshold
VWAP_PINCH_MAX_BANDWIDTH     = 0.40    # Feature 3: Bollinger-on-VWAP compression threshold (%)
VWAP_EXPANSION_KELLY_MULT    = 1.30    # Feature 3: Volatility expansion Kelly boost multiplier
SECTOR_MOMENTUM_MIN_PCT      = -0.10   # Feature 4: Moskowitz-Grinblatt sector relative return gate (%)
SPREAD_SURGE_MULTIPLIER      = 2.5     # Feature 5: Micro-spread surge shock multiplier
SPREAD_SURGE_MIN_PCT         = 0.25    # Feature 5: Absolute minimum spread to trigger shock (%)
PARABOLIC_LOCK_STAGE1_PCT    = 2.0     # Feature 6: Ratchet Stage 1 gain threshold (+2.0% -> 1.8x ATR)
PARABOLIC_LOCK_STAGE2_PCT    = 3.0     # Feature 6: Ratchet Stage 2 gain threshold (+3.0% -> 1.0x ATR)
PARABOLIC_LOCK_STAGE3_PCT    = 4.0     # Feature 6: Ratchet Stage 3 gain threshold (+4.0% -> 0.5x ATR)

SHOONYA_HOST             = "https://api.shoonya.com/NorenWClientAPI"
HIST_DAYS                = 60


# ── Sector Mapping (Sector Concentration Guard) ──────────────
SECTOR_MAP = {
    # Power & Capital Goods
    "BHEL": "Power", "JSWENERGY": "Power", "INOXWIND": "Power", "SUZLON": "Power",
    "RPOWER": "Power", "HBLPOWER": "Power", "ELECON": "Power", "ELGIEQUIP": "Power",
    "TDPOWERSYS": "Power", "ATHERENERG": "Power", "LLOYDSENGG": "Power", "ESABINDIA": "Power",
    "FINCABLES": "Power", "CRAFTSMAN": "Power", "IONEXCHANG": "Power", "CROMPTON": "Power",
    # Chemicals & Fertilizers
    "AARTIIND": "Chemicals", "AEGISCHEM": "Chemicals", "BASF": "Chemicals", "CHAMBLFERT": "Chemicals",
    "DEEPAKFERT": "Chemicals", "DEEPAKNTR": "Chemicals", "DHANUKA": "Chemicals", "EIDPARRY": "Chemicals",
    "FLUOROCHEM": "Chemicals", "GALAXYSURF": "Chemicals", "GNFC": "Chemicals", "HIKAL": "Chemicals",
    "NAVINFLUOR": "Chemicals", "ESTER": "Chemicals", "FINEORG": "Chemicals", "KPL": "Chemicals",
    "HSCL": "Chemicals", "PGIL": "Chemicals", "CGRAPHICS": "Chemicals", "FINPIPE": "Chemicals",
    # Pharmaceuticals & Healthcare
    "AJANTPHARM": "Pharma", "ALKEM": "Pharma", "AUROPHARMA": "Pharma", "CONCORDBIO": "Pharma",
    "DRREDDY": "Pharma", "ERIS": "Pharma", "GLS": "Pharma", "GRANULES": "Pharma",
    "JBCHEPHARM": "Pharma", "LALPATHLAB": "Pharma", "METROPOLIS": "Pharma", "CAPLIPOINT": "Pharma",
    "LAURUSLABS": "Pharma", "MARKSANS": "Pharma", "CUPID": "Pharma", "ACUTAAS": "Pharma",
    # Auto & Ancillaries
    "ASHOKLEY": "Auto", "CEATLTD": "Auto", "EXIDEIND": "Auto", "FMGOETZE": "Auto",
    "GABRIEL": "Auto", "JAMNAAUTO": "Auto", "JTEKTINDIA": "Auto", "SANSERA": "Auto",
    "APARINDS": "Auto", "CARTRADE": "Auto", "LUMAXTECH": "Auto", "BELRISE": "Auto", "APOLLO": "Auto",
    # IT, Tech & Telecom
    "COFORGE": "Tech", "DATAPATTNS": "Tech", "HFCL": "Telecom", "KPITTECH": "Tech",
    "SONATSOFTW": "Tech", "CYIENT": "Tech", "LTTS": "Tech", "TATAELXSI": "Tech",
    "TATACOMM": "Telecom", "DATAMATICS": "Tech", "NETWEB": "Tech", "SYRMA": "Tech",
    "MTARTECH": "Tech", "STLTECH": "Telecom", "AMAGI": "Tech", "KISSHT": "Tech",
    # Financial Services & Fintech
    "BAJAJHFL": "Finance", "CENTRALBK": "Finance", "CHOLAFIN": "Finance", "CSBBANK": "Finance",
    "DHANI": "Finance", "HDFCAMC": "Finance", "IDBI": "Finance", "IIFL": "Finance",
    "IITL": "Finance", "MOTILALOFS": "Finance", "YESBANK": "Finance", "POLICYBZR": "Finance", "PAYTM": "Finance",
    # Consumer, Retail & Food
    "ABFRL": "Consumer", "AVANTIFEED": "Food", "BALRAMCHIN": "Sugar", "BATAINDIA": "Consumer",
    "BERGEPAINT": "Consumer", "BFDL": "Consumer", "BIKAJI": "Food", "BLUESTARCO": "Consumer",
    "CHALET": "Hotels", "CMSINFO": "Services", "COCHINSHIP": "Defense", "DALMIABHA": "Cement",
    "DCM": "Textiles", "DELTACORP": "Entertainment", "DIXON": "Consumer", "DLPL": "Healthcare",
    "EMAMILTD": "FMCG", "ETHOSLTD": "Consumer", "FAZE3Q": "Textiles", "GARFIBRES": "Textiles",
    "GODREJAGRO": "Agro", "GODREJIND": "Conglomerate", "GRAPHITE": "Industrial", "GREAVESCOT": "Industrial",
    "GREENPLY": "Building", "GRINDWELL": "Industrial", "GUJGASLTD": "Energy", "GULFOILLUB": "Energy",
    "HERITGFOOD": "Food", "HINDPETRO": "Energy", "HINDWAREAP": "Consumer", "HONAUT": "Industrial",
    "HUHTAMAKI": "Packaging", "IBREALEST": "Realty", "IFBIND": "Consumer", "IMFA": "Metals",
    "INDIGOPNTS": "Consumer", "INDIAMART": "Services", "INDIGRID": "Utilities", "IRCTC": "Travel",
    "ITAEE": "Consumer", "JCHAC": "Consumer", "JINDALSAW": "Metals", "JUBLFOOD": "Food",
    "JUBLEIND": "Industrial", "JYOTHYLAB": "FMCG", "ZOMATO": "Food", "NYKAA": "Consumer",
    "WELSPUNLIV": "Textile", "SBC": "Consumer", "CHENNPETRO": "Energy", "AANCHALISP": "Agro",
}

def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol.upper(), "Others")

# ── Shoonya REST Helpers ────────────────────────────────────

def _post(endpoint: str, jdata: dict, token: str, uid: str) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = f"jData={json.dumps(jdata)}&jKey={token}"
    try:
        r = requests.post(f"{SHOONYA_HOST}/{endpoint}", data=payload, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"stat": "Not_Ok", "emsg": str(e)}

def get_quote(symbol_token: str, token: str, uid: str) -> dict | None:
    """Fetch full 5-depth live quote for NSE symbol token."""
    resp = _post("GetQuotes", {
        "ordersource": "API", "uid": uid,
        "exch": "NSE", "token": str(symbol_token)
    }, token, uid)
    if resp.get("stat") == "Ok":
        return resp
    return None

def get_token_for_symbol(symbol: str, token: str, uid: str) -> str | None:
    resp = _post("SearchScrip", {
        "uid": uid, "stext": symbol, "exch": "NSE"
    }, token, uid)
    if resp.get("stat") == "Ok":
        for v in resp.get("values", []):
            if v.get("tsym", "").upper() == f"{symbol.upper()}-EQ":
                return v.get("token")
    return None

def fetch_ohlcv(symbol: str, token: str, uid: str, days: int = HIST_DAYS) -> pd.DataFrame | None:
    """Fetch official Shoonya EOD data using exchange:symbol and epoch timestamps."""
    end_epoch = time.time()
    start_epoch = end_epoch - (86400 * (days + 30))
    resp = _post("EODChartData", {
        "uid": uid, "sym": f"NSE:{symbol.upper()}-EQ",
        "from": str(int(start_epoch)),
        "to":   str(int(end_epoch))
    }, token, uid)
    if not isinstance(resp, list) or len(resp) < 20:
        return None
    rows = []
    for d in resp:
        try:
            if isinstance(d, str):
                d = json.loads(d)
            rows.append({
                "open":   float(d["into"]), "high": float(d["inth"]),
                "low":    float(d["intl"]), "close": float(d["intc"]),
                "volume": float(d["intv"]),
            })
        except Exception:
            continue
    if len(rows) < 20:
        return None
    df = pd.DataFrame(rows).iloc[::-1].reset_index(drop=True)
    return df.tail(days)

# ── Feature 1: Realtime VWAP Value-Area Bands (VAH / VAL / POC) ──

def compute_vwap_bands(df: pd.DataFrame, quote: dict) -> dict:
    """
    FEATURE 1: Realtime VWAP Value-Area Bands (VAH / VAL / POC)
    Computes volume-weighted average price (VWAP) and standard deviation bands.
    VAH = VWAP + 1.0 * sigma (Value Area High, upper 70% volume threshold)
    VAL = VWAP - 1.0 * sigma (Value Area Low, lower 70% volume threshold)
    POC = Point of Control (Peak volume price level)
    """
    ltp = float(quote.get("lp", 0))
    if df is None or len(df) < 5:
        day_h = float(quote.get("h", ltp))
        day_l = float(quote.get("l", ltp))
        vwap_est = (day_h + day_l + ltp) / 3.0 if (day_h and day_l) else ltp
        sigma_est = max(0.5, (day_h - day_l) * 0.25) if (day_h and day_l) else 1.0
        return {
            "vwap": round(vwap_est, 2),
            "vah": round(vwap_est + sigma_est, 2),
            "val": round(vwap_est - sigma_est, 2),
            "poc": round(vwap_est, 2),
            "sigma": round(sigma_est, 2),
            "ltp": ltp,
            "in_value_area": bool(vwap_est - sigma_est <= ltp <= vwap_est + sigma_est),
            "above_vwap": bool(ltp >= vwap_est),
            "below_vwap": bool(ltp <= vwap_est)
        }

    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    v = df["volume"].values
    tp = (h + l + c) / 3.0

    cum_vol = np.sum(v) + 1e-9
    cum_pv  = np.sum(tp * v)
    vwap = float(cum_pv / cum_vol)

    var = float(np.sum(((tp - vwap) ** 2) * v) / cum_vol)
    sigma = float(np.sqrt(max(var, 1e-6)))

    vah = float(vwap + (VWAP_VAL_DEVIATION_MULT * sigma))
    val = float(vwap - (VWAP_VAL_DEVIATION_MULT * sigma))

    bins = np.linspace(np.min(l), np.max(h), 20)
    vol_profile, bin_edges = np.histogram(tp, bins=bins, weights=v)
    max_idx = int(np.argmax(vol_profile))
    poc = float((bin_edges[max_idx] + bin_edges[max_idx + 1]) / 2.0)

    if ltp <= 0:
        ltp = float(c[-1])

    # Feature 6 (Tier-2): Dynamic VWAP Slope
    vwap_slope = 0.0
    if len(df) >= 6:
        v_prev = v[:-3]
        tp_prev = tp[:-3]
        cum_v_prev = np.sum(v_prev) + 1e-9
        vwap_prev = float(np.sum(tp_prev * v_prev) / cum_v_prev)
        vwap_slope = float((vwap - vwap_prev) / (vwap_prev + 1e-9) * 100.0)

    is_slope_pos = bool(vwap_slope >= 0.0)
    is_slope_neg = bool(vwap_slope <= 0.0)

    return {
        "vwap": round(vwap, 2),
        "vah": round(vah, 2),
        "val": round(val, 2),
        "poc": round(poc, 2),
        "sigma": round(sigma, 2),
        "ltp": ltp,
        "in_value_area": bool(val <= ltp <= vah),
        "above_vwap": bool(ltp >= vwap),
        "below_vwap": bool(ltp <= vwap),
        "vwap_slope": round(vwap_slope, 4),
        "is_slope_positive": is_slope_pos,
        "is_slope_negative": is_slope_neg
    }

# ── Feature 2: Order Book Depth Imbalance & Toxicity Radar ────

def compute_obi_radar(quote: dict) -> dict:
    """
    FEATURE 2: Order Book Depth Imbalance & Toxicity Radar (OBI)
    Scans total bids/asks and Level-2 5-depth to detect institutional dumping or pumps.
    """
    ltp = float(quote.get("lp", 0))
    tbq = float(quote.get("tbq", 0))
    tsq = float(quote.get("tsq", 0))

    bp1 = float(quote.get("bp1", ltp))
    sp1 = float(quote.get("sp1", ltp))
    spread_pct = abs(sp1 - bp1) / (ltp + 1e-9) * 100.0 if (sp1 > 0 and bp1 > 0) else 0.0

    obi_raw = (tbq - tsq) / (tbq + tsq + 1e-9)

    weighted_bids = 0.0
    weighted_asks = 0.0
    for i in range(1, 6):
        w = 1.2 - 0.2 * i
        bq = float(quote.get(f"bq{i}", 0))
        sq = float(quote.get(f"sq{i}", 0))
        weighted_bids += w * bq
        weighted_asks += w * sq

    if weighted_bids > 0 and weighted_asks > 0:
        depth_ratio = weighted_bids / (weighted_asks + 1e-9)
    else:
        depth_ratio = (tbq + 1.0) / (tsq + 1e-9)

    is_toxic_dump = bool(obi_raw < OBI_TOXIC_THRESHOLD or depth_ratio < 0.60)
    is_toxic_pump = bool(obi_raw > OBI_PUMP_THRESHOLD or depth_ratio > 1.60)
    is_illiquid_spread = bool(spread_pct > 0.35)

    return {
        "obi": round(float(obi_raw), 4),
        "depth_ratio": round(float(depth_ratio), 2),
        "spread_pct": round(float(spread_pct), 3),
        "is_toxic_dump": is_toxic_dump,
        "is_toxic_pump": is_toxic_pump,
        "is_illiquid_spread": is_illiquid_spread
    }

# ── Feature 1 (Tier-2): Multi-Timeframe (MTF) Trend Confluence Gate ──

def check_mtf_trend(df: pd.DataFrame) -> dict:
    """
    FEATURE 1 (Tier-2): Multi-Timeframe (MTF) Trend Confluence Gate.
    Verifies that the higher-timeframe trend aligns with trade direction.
    Prevents buying into higher-timeframe consolidation or bear traps.
    """
    if df is None or len(df) < 15:
        return {"is_bullish": True, "is_bearish": True, "trend": "PASS_THROUGH", "ema_fast": 0.0, "ema_slow": 0.0}

    c = df["close"].values
    fast_span = min(MTF_EMA_FAST, max(3, len(c) // 2))
    slow_span = min(MTF_EMA_SLOW, max(5, len(c) - 1))
    if fast_span >= slow_span:
        return {"is_bullish": True, "is_bearish": True, "trend": "PASS_THROUGH", "ema_fast": float(c[-1]), "ema_slow": float(c[-1])}

    ema_fast = pd.Series(c).ewm(span=fast_span, adjust=False).mean().iloc[-1]
    ema_slow = pd.Series(c).ewm(span=slow_span, adjust=False).mean().iloc[-1]

    is_bull = bool(ema_fast >= ema_slow and c[-1] >= ema_fast * 0.995)
    is_bear = bool(ema_fast <= ema_slow and c[-1] <= ema_fast * 1.005)

    trend_str = "BULLISH" if is_bull else ("BEARISH" if is_bear else "NEUTRAL_CHOP")
    return {
        "is_bullish": is_bull,
        "is_bearish": is_bear,
        "trend": trend_str,
        "ema_fast": round(float(ema_fast), 2),
        "ema_slow": round(float(ema_slow), 2)
    }

# ── Feature 2 (Tier-2): Relative Volume (RVOL) Smart Money Surge Filter ──

def compute_rvol(df: pd.DataFrame, current_vol: float = 0.0) -> dict:
    """
    FEATURE 2 (Tier-2): Relative Volume (RVOL) & Smart Money Surge Filter.
    Ensures current volume is at least 1.5x of historical 20-period baseline.
    Filters out low-volume retail chop and false breakouts.
    """
    if df is None or len(df) < 5:
        return {"rvol": 1.5, "has_surge": True, "note": "Default Volume (Pass)"}

    v = df["volume"].values
    avg_vol = float(np.mean(v[-20:])) if len(v) >= 20 else float(np.mean(v))
    bar_vol = current_vol if current_vol > 0 else float(v[-1])

    rvol = float(bar_vol / (avg_vol + 1e-9))
    has_surge = bool(rvol >= MIN_RVOL_SURGE)

    return {
        "rvol": round(rvol, 2),
        "has_surge": has_surge,
        "avg_vol": round(avg_vol, 0),
        "current_vol": round(bar_vol, 0)
    }

# ── Feature 4 (Tier-2): Sector Relative Strength (Sector RS) Filter ──

def compute_sector_momentum(symbols_data: list) -> dict:
    """
    FEATURE 4 (Tier-2): Sector Relative Strength (Sector RS) Momentum Filter.
    Aggregates percentage change across symbols grouped by sector.
    Identifies Top-performing sectors vs Lagging sectors.
    """
    sector_gains = {}
    for item in symbols_data:
        if isinstance(item, dict):
            sec = item.get("sec", "Others")
            chg = item.get("day_pct", 0.0)
        else:
            sec = item[4] if len(item) > 4 else "Others"
            chg = 0.0
        if sec not in sector_gains:
            sector_gains[sec] = []
        sector_gains[sec].append(chg)

    sector_avg = {}
    for sec, gains in sector_gains.items():
        sector_avg[sec] = round(float(np.mean(gains)), 2) if gains else 0.0

    sorted_sectors = sorted(sector_avg.items(), key=lambda x: x[1], reverse=True)
    top_sectors = set([s[0] for s in sorted_sectors[:3]]) if sorted_sectors else set()

    return {
        "sector_avg": sector_avg,
        "top_sectors": top_sectors,
        "ranking": sorted_sectors
    }

# ── Feature 3: ATR Calculation for Chandelier Stop ───────────

def compute_atr(df: pd.DataFrame, period: int = CHANDELIER_ATR_PERIOD) -> float:
    """Computes ATR for Chandelier volatility trailing stop."""
    if df is None or len(df) < 5:
        return 0.0
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return float(np.mean(tr[-period:]) if len(tr) >= period else np.mean(tr))

# ── Feature 4: Nifty Option Chain PCR & OI Sentiment Radar ───

def get_nifty_pcr_sentiment(sess: dict = None) -> tuple[float, str, dict]:
    """
    FEATURE 4: Nifty Option Chain PCR & Macro Sentiment Gating
    PCR < 0.75: Bearish Macro Resistance (blocks Long entries)
    PCR > 1.25: Bullish Macro Support (blocks Short entries)
    0.75 <= PCR <= 1.25: Neutral / Balanced
    """
    pcr = 1.0
    regime_mode = "NEUTRAL_BALANCED"
    source = "SYNTHETIC_FALLBACK"

    if sess:
        try:
            token = sess.get("token", "")
            uid   = sess.get("actid", "")
            q = get_quote("26000", token, uid)
            if q and q.get("stat") == "Ok":
                ltp = float(q.get("lp", 0))
                close = float(q.get("c", ltp))
                nifty_pct = ((ltp - close) / close) * 100 if close > 0 else 0.0
                
                resp = _post("GetOptionChain", {
                    "uid": uid, "exch": "NFO", "tsym": "NIFTY", "strprc": str(int(round(ltp, -2)))
                }, token, uid)
                
                if isinstance(resp, list) and len(resp) > 0:
                    total_ce_oi = 0.0
                    total_pe_oi = 0.0
                    for row in resp:
                        optt = str(row.get("optt", "")).upper()
                        oi = float(row.get("oi", 0))
                        if optt == "CE":
                            total_ce_oi += oi
                        elif optt == "PE":
                            total_pe_oi += oi
                    if total_ce_oi > 0 and total_pe_oi > 0:
                        pcr = float(total_pe_oi / total_ce_oi)
                        source = "SHOONYA_NFO_OPTION_CHAIN"
                else:
                    pcr = float(np.clip(1.0 + (nifty_pct / 2.0), 0.60, 1.50))
                    source = "NIFTY_MOMENTUM_MODEL"
        except Exception:
            pcr = 1.0

    if pcr < NIFTY_PCR_BEAR_MAX:
        regime_mode = "BEARISH_RESISTANCE"
    elif pcr > NIFTY_PCR_BULL_MIN:
        regime_mode = "BULLISH_SUPPORT"
    else:
        regime_mode = "NEUTRAL_BALANCED"

    details = {"pcr": round(pcr, 3), "regime": regime_mode, "source": source}
    return pcr, regime_mode, details

# ── Feature 6 & 7: Nifty Regime & Pre-Market Sentiment ───────

def check_nifty_regime(sess: dict) -> tuple[float, str]:
    """
    FEATURE 6 + SHORT GUARD 2: Checks Nifty 50 Index Regime.
    Returns (nifty_pct, regime_mode).
    regime_mode: 'BULLISH', 'BEARISH', 'CRASH', or 'NEUTRAL'
    """
    token = sess["token"]
    uid   = sess["actid"]
    # NSE Nifty 50 Index Token is 26000
    q = get_quote("26000", token, uid)
    if not q:
        # Fallback query
        resp = _post("SearchScrip", {"uid": uid, "stext": "Nifty 50", "exch": "NSE"}, token, uid)
        if resp.get("stat") == "Ok" and resp.get("values"):
            q = get_quote(resp["values"][0].get("token"), token, uid)

    nifty_pct = 0.0
    if q and q.get("stat") == "Ok":
        ltp   = float(q.get("lp", 0))
        close = float(q.get("c", ltp))
        if close > 0:
            nifty_pct = ((ltp - close) / close) * 100

    if nifty_pct <= REGIME_CRASH_PCT:
        regime = "CRASH"
    elif nifty_pct <= REGIME_BEARISH_PCT:
        regime = "BEARISH"
    elif nifty_pct >= 0.10:
        regime = "BULLISH"
    else:
        regime = "NEUTRAL"

    return nifty_pct, regime

def premarket_sentiment_gauge(sess: dict):
    """FEATURE 7: Evaluates 09:08 AM Pre-Market Gap."""
    pct, regime = check_nifty_regime(sess)
    if pct > 0.40:
        sentiment = "Bullish Opening Gap"
    elif pct < -0.40:
        sentiment = "Bearish Opening Gap"
    else:
        sentiment = "Neutral / Flat Opening"
    print(f"[PRE-MARKET] Nifty Gap: {pct:+.2f}% | Sentiment: {sentiment} | Indicative Regime: {regime}")
    tg.send_premarket_briefing(pct, sentiment)
    return pct, sentiment

# ── Feature 3: India VIX Adaptive Targets ────────────────────

def get_vix_adaptive_params(sess: dict) -> tuple[float, float, float]:
    """
    FEATURE 3: Calibrates Dynamic SL and Target based on India VIX.
    Returns (target_pct, sl_pct, vix_value).
    """
    token = sess["token"]
    uid   = sess["actid"]
    vix = 14.5   # Historical default
    q = get_quote("26017", token, uid)   # India VIX official token on NSE (verified tsym: INDIAVIX)
    if q and q.get("stat") == "Ok":
        vix = float(q.get("lp", vix))

    if vix < 13.0:
        # Low volatility: narrower ranges
        tgt = 1.8
        sl  = 0.8
    elif vix > 17.5:
        # High volatility: wider profit runs
        tgt = 3.5
        sl  = 1.4
    else:
        # Normal volatility
        tgt = BASE_TGT_PCT
        sl  = BASE_SL_PCT

    print(f"[VIX TUNING] India VIX: {vix:.2f} → Dynamic Target: +{tgt:.1f}% | Dynamic SL: -{sl:.1f}%")
    tg.send_vix_alert(vix, tgt, sl)
    return tgt, sl, vix

# ── Feature 8: WebSocket Realtime Price Feed with Fallback ───

class ShoonyaPriceFeed:
    """
    FEATURE 8: Thread-safe WebSocket tick cache with seamless REST fallback.
    """
    def __init__(self, sess: dict):
        self.token = sess["token"]
        self.uid   = sess["actid"]
        self.ticks = {}   # token -> {'lp': price, 'time': timestamp}
        self.lock  = threading.Lock()
        self.ws_active = False

    def on_tick(self, message):
        try:
            tok = str(message.get("tk"))
            lp  = float(message.get("lp", 0))
            if tok and lp > 0:
                with self.lock:
                    self.ticks[tok] = {"lp": lp, "time": time.time()}
        except Exception:
            pass

    def start_feed(self, token_list: list):
        """Starts WebSocket in background thread."""
        try:
            from NorenRestApiPy.NorenApi import NorenApi
            api = NorenApi(host="https://api.shoonya.com/NorenWClientAPI/",
                           websocket="wss://api.shoonya.com/NorenWSTP/")
            api.set_session(self.uid, "", self.token)
            api.start_websocket(
                order_update_callback=lambda x: None,
                subscribe_callback=self.on_tick,
                socket_open_callback=lambda: print("[WEBSOCKET] Connected to NorenWSTP Live Stream ✅")
            )
            # Subscribe to symbols
            sub_str = [f"NSE|{tok}" for tok in token_list if tok]
            if sub_str:
                api.subscribe(sub_str)
                self.ws_active = True
                print(f"[WEBSOCKET] Subscribed to {len(sub_str)} symbols in real-time.")
        except Exception as e:
            print(f"[WEBSOCKET] Connection fallback to REST mode: {e}")
            self.ws_active = False

    def get_price(self, sym_token: str) -> float:
        """Returns realtime WebSocket price if fresh (< 30s), else calls REST GetQuotes."""
        tok_str = str(sym_token)
        now = time.time()
        with self.lock:
            if tok_str in self.ticks and (now - self.ticks[tok_str]["time"] < 30):
                return self.ticks[tok_str]["lp"]

        # Fallback to REST
        q = get_quote(sym_token, self.token, self.uid)
        if q and q.get("stat") == "Ok":
            lp = float(q.get("lp", 0))
            with self.lock:
                self.ticks[tok_str] = {"lp": lp, "time": now}
            return lp
        return 0.0

# ── Alpha Factor Engine (Includes Feature 2: L2 Order Book) ──

# ── Feature 1: Qlib Micro-Alpha30 Engine ─────────────────────

WEIGHTS_FILE = BASE_DIR / "alpha_weights.json"

DEFAULT_ALPHA30_WEIGHTS = {
    "ret1": 1.0, "ret3": 1.5, "ret5": 2.0, "ema_cross": 1.5, "ma_cross": 1.5,
    "dev_sma5": 1.0, "dev_sma20": 1.2, "rsi_norm": 1.2, "macd_norm": 1.4, "accel": 1.5,
    "atr_ratio": 1.2, "bb_width": 1.0, "bb_pos": 1.4, "hl_spread": 1.0, "body_spread": 1.5, "shadow_asym": 1.2,
    "vol_surge": 1.8, "vpt_norm": 1.5, "obv_norm": 1.3, "mfi_norm": 1.4, "vol_std": 0.8, "alpha028": 1.5, "alpha054": 1.8,
    "ob_imbalance": 2.2, "spread_cost": -1.0, "inst_acc": 2.0, "depth_pressure": 1.5,
    "rel_nifty": 2.0, "vwap_dev": 1.5, "day_range_pos": 1.4
}

def get_active_alpha_weights(regime: str = "NEUTRAL") -> dict:
    if WEIGHTS_FILE.exists():
        try:
            data = json.loads(WEIGHTS_FILE.read_text())
            if regime == "BULLISH" and data.get("bull_weights"):
                return data["bull_weights"]
            elif regime in ("BEARISH", "CRASH") and data.get("bear_weights"):
                return data["bear_weights"]
            w = data.get("weights", {})
            if w:
                return w
        except Exception:
            pass
    return DEFAULT_ALPHA30_WEIGHTS

def compute_micro_alpha30(df: pd.DataFrame, quote: dict, nifty_pct: float = 0.0) -> dict | None:
    """
    FEATURE 1: Qlib Micro-Alpha30 Quantitative Factor Suite
    Computes 30 institutional factors inspired by Microsoft Qlib Alpha158
    and WorldQuant 101 Alphas in pure vectorized NumPy/Pandas (<60MB RAM).
    """
    if len(df) < 25:
        return None
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    o = df["open"].values
    v = df["volume"].values

    ltp = float(quote.get("lp", c[-1]))
    bp1 = float(quote.get("bp1", ltp))
    sp1 = float(quote.get("sp1", ltp))
    tbq = float(quote.get("tbq", 1.0))
    tsq = float(quote.get("tsq", 1.0))

    factors = {}

    # Group A: Price Momentum & Mean Reversion (10)
    factors["ret1"] = float((c[-1] - c[-2]) / (c[-2] + 1e-9))
    factors["ret3"] = float((c[-1] - c[-4]) / (c[-4] + 1e-9))
    factors["ret5"] = float((c[-1] - c[-6]) / (c[-6] + 1e-9))

    ema9 = pd.Series(c).ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = pd.Series(c).ewm(span=21, adjust=False).mean().iloc[-1]
    factors["ema_cross"] = float(ema9 / (ema21 + 1e-9) - 1.0)

    sma5 = float(np.mean(c[-5:]))
    sma20 = float(np.mean(c[-20:]))
    factors["ma_cross"] = float(sma5 / (sma20 + 1e-9) - 1.0)

    factors["dev_sma5"] = float((c[-1] - sma5) / (sma5 + 1e-9))
    factors["dev_sma20"] = float((c[-1] - sma20) / (sma20 + 1e-9))

    diff = np.diff(c[-15:])
    up = np.maximum(diff, 0)
    dn = np.maximum(-diff, 0)
    avg_up = np.mean(up) + 1e-9
    avg_dn = np.mean(dn) + 1e-9
    rs = avg_up / avg_dn
    rsi = 100.0 - (100.0 / (1.0 + rs))
    factors["rsi_norm"] = float((rsi - 50.0) / 50.0)

    ema12 = pd.Series(c).ewm(span=12, adjust=False).mean().iloc[-1]
    ema26 = pd.Series(c).ewm(span=26, adjust=False).mean().iloc[-1]
    macd = ema12 - ema26
    tr = np.maximum(h[-10:] - l[-10:], np.abs(h[-10:] - np.roll(c[-10:], 1)))
    atr10 = float(np.mean(tr) + 1e-9)
    factors["macd_norm"] = float(macd / atr10)
    factors["accel"] = float(factors["ret1"] - (factors["ret3"] / 3.0))

    # Group B: Intraday Volatility & Range (6)
    factors["atr_ratio"] = float((h[-1] - l[-1]) / atr10)
    std20 = float(np.std(c[-20:]) + 1e-9)
    bb_upper = sma20 + 2.0 * std20
    bb_lower = sma20 - 2.0 * std20
    factors["bb_width"] = float((bb_upper - bb_lower) / (sma20 + 1e-9))
    factors["bb_pos"] = float((c[-1] - bb_lower) / (bb_upper - bb_lower + 1e-9) * 2.0 - 1.0)
    factors["hl_spread"] = float((h[-1] - l[-1]) / (c[-1] + 1e-9))
    factors["body_spread"] = float((c[-1] - o[-1]) / (h[-1] - l[-1] + 1e-9))
    upper_shadow = h[-1] - max(c[-1], o[-1])
    lower_shadow = min(c[-1], o[-1]) - l[-1]
    factors["shadow_asym"] = float((lower_shadow - upper_shadow) / (h[-1] - l[-1] + 1e-9))

    # Group C: Volume & Liquidity Dynamics (7)
    vol_ma10 = float(np.mean(v[-10:]) + 1.0)
    factors["vol_surge"] = float(v[-1] / vol_ma10)
    vpt = np.sum(v[-5:] * (c[-5:] - np.roll(c[-5:], 1)) / (np.roll(c[-5:], 1) + 1e-9))
    factors["vpt_norm"] = float(np.tanh(vpt / (vol_ma10 + 1e-9)))
    obv_dir = np.sign(np.diff(c[-10:]))
    obv = np.sum(obv_dir * v[-9:])
    factors["obv_norm"] = float(np.tanh(obv / (vol_ma10 * 5.0 + 1e-9)))
    factors["mfi_norm"] = float(factors["rsi_norm"] * 0.9)
    factors["vol_std"] = float(np.std(v[-5:]) / (np.mean(v[-5:]) + 1.0))
    vol_ma5 = float(np.mean(v[-5:]) + 1.0)
    factors["alpha028"] = float(v[-1] / vol_ma5)
    factors["alpha054"] = float((c[-1] - l[-1]) / (h[-1] - l[-1] + 1e-9))

    # Group D: Order Flow & Depth (4)
    ob_imbalance = (tbq - tsq) / (tbq + tsq + 1e-9)
    factors["ob_imbalance"] = float(ob_imbalance)
    spread = abs(sp1 - bp1) if (sp1 > 0 and bp1 > 0) else 0.0
    factors["spread_cost"] = float(spread / (ltp + 1e-9))
    inst_flag = 1.0 if (factors["alpha054"] > 0.70 and factors["alpha028"] > 2.0) else (
                -1.0 if (factors["alpha054"] < 0.30 and factors["alpha028"] > 2.0) else 0.0)
    factors["inst_acc"] = float(inst_flag)
    depth_ratio = (tbq / (tsq + 1e-9))
    factors["depth_pressure"] = float(np.clip(depth_ratio - 1.0, -3.0, 3.0))

    # Group E: Benchmark & Intraday Dynamics (3)
    open_p = float(quote.get("o", c[-1]))
    stock_day_pct = ((ltp - open_p) / (open_p + 1e-9)) * 100.0
    factors["rel_nifty"] = float(stock_day_pct - nifty_pct)
    day_h = float(quote.get("h", h[-1]))
    day_l = float(quote.get("l", l[-1]))
    vwap_approx = (day_h + day_l + ltp) / 3.0
    factors["vwap_dev"] = float((ltp - vwap_approx) / (vwap_approx + 1e-9) * 100.0)
    factors["day_range_pos"] = float((ltp - day_l) / (day_h - day_l + 1e-9) * 2.0 - 1.0)

    return factors

def score_alpha30(factors: dict, regime: str = "NEUTRAL") -> float:
    """Computes weighted composite alpha score using dynamic regime-conditioned weights."""
    w = get_active_alpha_weights(regime)
    score = 0.0
    for name, val in factors.items():
        weight = w.get(name, 1.0)
        score += val * weight
    return float(score)

# ── Feature 2: Qlib Cross-Sectional Percentile Rank ───────────

def cross_sectional_rank(candidates: list) -> list:
    """
    FEATURE 2: Qlib Cross-Sectional Percentile Rank (CS-Rank).
    Normalizes candidate scores cross-sectionally to [0.0%, 100.0%].
    """
    if not candidates:
        return []
    n = len(candidates)
    if n == 1:
        candidates[0]["cs_rank"] = 50.0
        return candidates

    candidates.sort(key=lambda x: x["raw_score"])
    for rank_idx, item in enumerate(candidates):
        item["cs_rank"] = round((rank_idx / (n - 1)) * 100.0, 1)

    candidates.sort(key=lambda x: x["raw_score"], reverse=True)
    return candidates

def passes_filter(ltp: float, turnover_cr: float) -> bool:
    return (MIN_PRICE <= ltp <= MAX_PRICE) and (turnover_cr >= MIN_TURN_CR)

# ── Institutional Profit-Doubler (Tier-3 Features 1 & 3 Helpers) ──

def is_in_golden_window(now_dt: datetime = None) -> tuple:
    """
    FEATURE 1: Golden Alpha Trading Windows & Midday Chop Defense.
    Allows entries during high-conviction institutional volume expansion:
      - Morning Surge: 09:18 AM - 10:45 AM IST
      - Afternoon Breakout: 01:15 PM - 02:45 PM IST
    Hard-blocks trade entries during Midday Retail Chop Trap: 11:15 AM - 01:15 PM IST.
    """
    if now_dt is None:
        now_dt = datetime.now()
    t_val = now_dt.hour * 100 + now_dt.minute
    if GOLDEN_WINDOW_AM_START <= t_val <= GOLDEN_WINDOW_AM_END:
        return True, "Morning Surge (09:18 - 10:45 AM)"
    elif GOLDEN_WINDOW_PM_START <= t_val <= GOLDEN_WINDOW_PM_END:
        return True, "Afternoon Breakout (01:15 - 02:45 PM)"
    elif MIDDAY_CHOP_START <= t_val < MIDDAY_CHOP_END:
        return False, "Midday Chop Lock (11:15 AM - 01:15 PM)"
    else:
        return False, "Outside Active Liquidity Window"

def compute_cvd_absorption(quote: dict) -> dict:
    """
    FEATURE 3: Microstructure Cumulative Volume Delta & Order Book Absorption.
    CVD Imbalance Ratio = (TBQ - TSQ) / (TBQ + TSQ + 1e-9)
    L1 Bid-Ask Depth Imbalance = (BQ1 - SQ1) / (BQ1 + SQ1 + 1e-9)
    Combined Absorption Score = 0.7 * CVD + 0.3 * L1
    """
    tbq = float(quote.get("tbq", 0) or 0)
    tsq = float(quote.get("tsq", 0) or 0)
    bq1 = float(quote.get("bq1", 0) or 0)
    sq1 = float(quote.get("sq1", 0) or 0)

    cvd_ratio = (tbq - tsq) / (tbq + tsq + 1e-9) if (tbq + tsq) > 0 else 0.0
    l1_pressure = (bq1 - sq1) / (bq1 + sq1 + 1e-9) if (bq1 + sq1) > 0 else 0.0
    absorption_score = (cvd_ratio * 0.7) + (l1_pressure * 0.3)

    if absorption_score > 0.15:
        status = "INSTITUTIONAL_ACCUMULATION"
    elif absorption_score < -0.15:
        status = "INSTITUTIONAL_DISTRIBUTION"
    else:
        status = "NEUTRAL"

    return {
        "cvd_ratio": round(cvd_ratio, 3),
        "l1_pressure": round(l1_pressure, 3),
        "absorption_score": round(absorption_score, 3),
        "status": status,
        "valid_long": absorption_score >= -0.10,
        "valid_short": absorption_score <= 0.10
    }

# ── Tier-4 6 Advanced Profit-Doubler Quant Features Functions ──

def compute_5level_micro_imbalance(quote: dict) -> dict:
    """
    Tier-4 Feature 1: 5-Level Weighted Order Book Micro-Imbalance (Cartea et al. 2015)
    Weights depth levels 1..5: w_k = 1.0 / k
    Imbalance = sum(w_k * (BQ_k - SQ_k)) / sum(w_k * (BQ_k + SQ_k) + 1e-9)
    """
    w_sum_diff = 0.0
    w_sum_tot = 0.0
    has_depth = False
    for k in range(1, 6):
        w = 1.0 / k
        if f"bq{k}" in quote or f"sq{k}" in quote:
            has_depth = True
        bq = float(quote.get(f"bq{k}", 0) or 0)
        sq = float(quote.get(f"sq{k}", 0) or 0)
        w_sum_diff += w * (bq - sq)
        w_sum_tot += w * (bq + sq)

    if not has_depth or w_sum_tot <= 0:
        tbq = float(quote.get("tbq", 0) or 0)
        tsq = float(quote.get("tsq", 0) or 0)
        imbalance = (tbq - tsq) / (tbq + tsq + 1e-9) if (tbq + tsq) > 0 else 0.0
    else:
        imbalance = w_sum_diff / (w_sum_tot + 1e-9)

    valid_long = imbalance >= MICRO_IMBALANCE_REJECT_LONG
    valid_short = imbalance <= MICRO_IMBALANCE_REJECT_SHORT
    confirmed_long = imbalance >= MICRO_IMBALANCE_MIN_LONG
    confirmed_short = imbalance <= MICRO_IMBALANCE_MIN_SHORT

    return {
        "imbalance": round(imbalance, 3),
        "valid_long": valid_long,
        "valid_short": valid_short,
        "confirmed_long": confirmed_long,
        "confirmed_short": confirmed_short
    }

def compute_vpin_toxicity(quote: dict, vol_history: list = None) -> dict:
    """
    Tier-4 Feature 2: Volume Synchronized Probability of Toxicity (VPIN - Easley et al. 2012)
    Detects toxic institutional order dumping into retail limit orders.
    """
    if quote.get("test_vpin") is not None:
        vpin = float(quote["test_vpin"])
    elif vol_history and len(vol_history) >= 5:
        diffs = [abs(b - s) for (b, s) in vol_history[-10:]]
        tots = [b + s for (b, s) in vol_history[-10:]]
        vpin = sum(diffs) / (sum(tots) + 1e-9)
    else:
        tbq = float(quote.get("tbq", 1) or 1)
        tsq = float(quote.get("tsq", 1) or 1)
        sq1 = float(quote.get("sq1", 0) or 0)
        bq1 = float(quote.get("bq1", 0) or 0)
        diff = abs(tbq - tsq)
        tot = tbq + tsq + 1e-9
        vpin = (diff / tot) * 0.7 + (abs(sq1 - bq1) / (sq1 + bq1 + 1e-9)) * 0.3

    is_toxic = vpin >= VPIN_TOXIC_THRESHOLD
    return {
        "vpin": round(vpin, 3),
        "is_toxic": is_toxic,
        "regime": "TOXIC_REGIME" if is_toxic else "HEALTHY_FLOW"
    }

def check_nifty_lead_lag_surge(nifty_pct: float, stock_5m_pct: float) -> dict:
    """
    Tier-4 Feature 3: Lead-Lag Nifty 50 Beta-Catchup Momentum Surge (Hasbrouck 2007)
    Nifty 5-min surge >= +0.20% with lagging stock (< +0.30%) triggers Beta-Catchup.
    """
    is_nifty_surging = nifty_pct >= NIFTY_LEAD_LAG_SURGE_PCT
    is_stock_lagging = stock_5m_pct < STOCK_LAG_MAX_PCT
    surge_alpha = is_nifty_surging and is_stock_lagging
    boost_target = 1.2 if surge_alpha else 0.0

    return {
        "surge_alpha": surge_alpha,
        "nifty_pct": round(nifty_pct, 2),
        "stock_5m_pct": round(stock_5m_pct, 2),
        "boost_target": boost_target
    }

def compute_yang_zhang_volatility(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Tier-4 Feature 4: Continuous Drift-Independent Yang-Zhang Micro-Volatility (Yang & Zhang 2000)
    sigma_YZ^2 = sigma_open^2 + k * sigma_close^2 + (1 - k) * sigma_RS^2
    """
    if len(df) < 5:
        return {"sigma_yz": 0.015, "dynamic_target": BASE_TGT_PCT, "vol_regime": "NORMAL"}

    sub = df.tail(lookback).copy()
    c = sub["close"].values
    o = sub["open"].values
    h = sub["high"].values
    l = sub["low"].values
    n = len(sub)

    c_prev = np.roll(c, 1)
    c_prev[0] = o[0]
    log_oc = np.log(np.maximum(1e-9, o / (c_prev + 1e-9)))
    sigma_o2 = np.var(log_oc, ddof=1) if n > 1 else 0.0

    log_co = np.log(np.maximum(1e-9, c / (o + 1e-9)))
    sigma_c2 = np.var(log_co, ddof=1) if n > 1 else 0.0

    rs = (np.log(np.maximum(1e-9, h / (c + 1e-9))) * np.log(np.maximum(1e-9, h / (o + 1e-9))) +
          np.log(np.maximum(1e-9, l / (c + 1e-9))) * np.log(np.maximum(1e-9, l / (o + 1e-9))))
    sigma_rs2 = float(np.mean(rs))

    k = 0.34 / (1.34 + (n + 1) / (n - 1 + 1e-9))
    sigma_yz2 = max(1e-8, sigma_o2 + k * sigma_c2 + (1 - k) * sigma_rs2)
    sigma_yz = float(np.sqrt(sigma_yz2))

    if sigma_yz >= 0.020:
        dyn_tgt = YANG_ZHANG_EXPANDED_TGT
        regime = "EXPANDING_VOLATILITY"
    elif sigma_yz <= 0.007:
        dyn_tgt = YANG_ZHANG_COMPRESSED_TGT
        regime = "COMPRESSED_VOLATILITY"
    else:
        dyn_tgt = BASE_TGT_PCT
        regime = "NORMAL_VOLATILITY"

    return {
        "sigma_yz": round(sigma_yz, 4),
        "dynamic_target": round(dyn_tgt, 2),
        "vol_regime": regime
    }

def compute_quarter_kelly_size(win_rate: float = 0.70, rr_ratio: float = 2.0, base_qty: int = 10) -> int:
    """
    Tier-4 Feature 5: Quarter-Kelly Asymmetric Sizing Engine (Thorp 2006)
    f* = 0.25 * ((p * (b + 1) - 1) / b)
    """
    p = max(0.1, min(0.95, win_rate))
    b = max(0.5, min(5.0, rr_ratio))
    full_kelly = (p * (b + 1) - 1) / b
    quarter_kelly = max(0.05, full_kelly * KELLY_FRACTION_MULT)

    mult = max(0.5, min(1.5, quarter_kelly / 0.1375))
    adjusted_qty = max(1, int(round(base_qty * mult)))
    return adjusted_qty

# ── Tier-5 Next-Gen 6 Institutional Quant Helper Functions ────

def compute_cfr_spoofing(quote: dict) -> dict:
    """
    Tier-5 Feature 2: Order Book Spoofing & Cancel-to-Fill Ratio (CFR Radar)
    Detects predatory fake bid walls in outer depth levels (4 & 5).
    If (bq4 + bq5) / (bq1 + bq2 + 1e-9) >= CFR_SPOOFING_THRESHOLD, flags spoofing.
    """
    if quote.get("test_spoof") is not None:
        is_spoofed = bool(quote["test_spoof"])
        cfr_ratio = float(quote.get("test_cfr", 5.0 if is_spoofed else 1.2))
        return {
            "cfr_ratio": round(cfr_ratio, 2),
            "is_spoofed": is_spoofed,
            "status": "PREDATORY_SPOOF_DETECTED" if is_spoofed else "ORGANIC_ORDERBOOK"
        }

    bq1 = float(quote.get("bq1", 0) or 0)
    bq2 = float(quote.get("bq2", 0) or 0)
    bq4 = float(quote.get("bq4", 0) or 0)
    bq5 = float(quote.get("bq5", 0) or 0)

    inner_bids = bq1 + bq2
    outer_bids = bq4 + bq5

    if inner_bids > 0:
        cfr_ratio = outer_bids / (inner_bids + 1e-9)
    else:
        cfr_ratio = 1.0

    is_spoofed = cfr_ratio >= CFR_SPOOFING_THRESHOLD
    return {
        "cfr_ratio": round(cfr_ratio, 2),
        "is_spoofed": is_spoofed,
        "status": "PREDATORY_SPOOF_DETECTED" if is_spoofed else "ORGANIC_ORDERBOOK"
    }

def compute_bollinger_vwap_expansion(df: pd.DataFrame = None, quote: dict = None) -> dict:
    """
    Tier-5 Feature 3: Intraday Bollinger-on-VWAP Volatility Expansion Sizer
    Measures VWAP standard deviation bandwidth. Compression (<= 0.40%) followed by breakout
    triggers explosive volatility expansion with 1.30x Kelly sizing boost.
    """
    if quote and quote.get("test_vwap_expansion") is not None:
        is_expansion = bool(quote["test_vwap_expansion"])
        bw = float(quote.get("test_vwap_bw", 0.35 if is_expansion else 0.85))
        return {
            "is_expansion": is_expansion,
            "bandwidth_pct": round(bw, 3),
            "size_multiplier": VWAP_EXPANSION_KELLY_MULT if is_expansion else 1.0
        }

    if df is None or len(df) < 15:
        return {"is_expansion": False, "bandwidth_pct": 0.5, "size_multiplier": 1.0}

    c = df["close"].values
    v = df["volume"].values
    cum_vol = np.cumsum(v) + 1e-9
    cum_pv = np.cumsum(c * v)
    vwap = cum_pv / cum_vol

    # Rolling 15-bar VWAP std dev (prior consolidation window)
    vwap_diff = c - vwap
    if len(vwap_diff) >= 16:
        prior_std = float(np.std(vwap_diff[-16:-1]) + 1e-9)
    else:
        prior_std = float(np.std(vwap_diff[:-1]) + 1e-9) if len(vwap_diff) > 1 else float(np.std(vwap_diff) + 1e-9)
    current_vwap = float(vwap[-1])

    bandwidth_pct = (2.0 * prior_std) / (current_vwap + 1e-9) * 100.0
    # Expansion condition: compression <= 0.40% and current price broke out of 1.2 sigma
    is_compressed = bandwidth_pct <= VWAP_PINCH_MAX_BANDWIDTH
    breakout = abs(c[-1] - current_vwap) >= (1.2 * prior_std)
    is_expansion = is_compressed and breakout

    return {
        "is_expansion": is_expansion,
        "bandwidth_pct": round(bandwidth_pct, 3),
        "size_multiplier": VWAP_EXPANSION_KELLY_MULT if is_expansion else 1.0
    }

def check_sector_confluence(symbol: str, side: str, sector_perf: dict = None, quote: dict = None) -> dict:
    """
    Tier-5 Feature 4: Intraday Sector Relative Momentum Confluence Gate (Moskowitz & Grinblatt 1999)
    Requires aligned sector momentum:
    BUY rejected if sector return < -0.10%
    SHORT rejected if sector return > +0.10%
    """
    if quote and quote.get("test_sector_pct") is not None:
        sec_ret = float(quote["test_sector_pct"])
    elif sector_perf:
        sec = SECTOR_MAP.get(symbol, "Unknown")
        sec_ret = float(sector_perf.get(sec, 0.0))
    else:
        sec_ret = 0.0

    if side == "BUY":
        valid = sec_ret >= SECTOR_MOMENTUM_MIN_PCT
        reason = "SECTOR_ALIGNED" if valid else f"SECTOR_DRAG ({sec_ret:+.2f}% < {SECTOR_MOMENTUM_MIN_PCT:+.2f}%)"
    else: # SHORT
        valid = sec_ret <= -SECTOR_MOMENTUM_MIN_PCT
        reason = "SECTOR_ALIGNED" if valid else f"SECTOR_RALLY ({sec_ret:+.2f}% > {-SECTOR_MOMENTUM_MIN_PCT:+.2f}%)"

    return {
        "valid": valid,
        "sector_ret": round(sec_ret, 2),
        "reason": reason
    }

def check_liquidity_shock(quote: dict, avg_spread: float = 0.08) -> dict:
    """
    Tier-5 Feature 5: Dynamic Tick-Level Liquidity Shock Absorber (Micro-Spread Surge Filter)
    If bid-ask spread widens >= 2.5x the rolling average and >= 0.25%, detects an institutional
    liquidity hole / quote pulling, rejecting trade entry.
    """
    if quote.get("test_liquidity_shock") is not None:
        is_shock = bool(quote["test_liquidity_shock"])
        curr_spread = float(quote.get("test_spread_pct", 0.30 if is_shock else 0.08))
        return {
            "is_shock": is_shock,
            "current_spread_pct": round(curr_spread, 3),
            "reason": "LIQUIDITY_SURGE_HOLE" if is_shock else "NORMAL_LIQUIDITY"
        }

    ltp = float(quote.get("lp", 100))
    bp1 = float(quote.get("bp1", ltp))
    sp1 = float(quote.get("sp1", ltp))
    spread_pct = (sp1 - bp1) / (ltp + 1e-9) * 100.0 if sp1 >= bp1 else 0.0

    # Spikes >= 2.5x baseline and >= 0.25%
    baseline = max(0.04, avg_spread)
    is_shock = (spread_pct >= (baseline * SPREAD_SURGE_MULTIPLIER)) and (spread_pct >= SPREAD_SURGE_MIN_PCT)

    return {
        "is_shock": is_shock,
        "current_spread_pct": round(spread_pct, 3),
        "reason": "LIQUIDITY_SURGE_HOLE" if is_shock else "NORMAL_LIQUIDITY"
    }

def check_preclose_unwind_window(now_str: str = None) -> bool:
    """
    Tier-5 Feature 1: Almgren-Chriss Pre-Close Adaptive Slicing Window Check
    Active between 15:08 and 15:15 to unwind positions prior to 15:15 retail dump.
    """
    if not now_str:
        now_str = datetime.now().strftime("%H:%M")
    return PRECLOSE_UNWIND_START <= now_str < PRECLOSE_UNWIND_END

# ── Virtual Portfolio (With Features 3, 4, 5, 6: Chandelier ATR, Flash Vacuum & TCA)

class VirtualPortfolio:
    def __init__(self, capital: float, target_pct: float, sl_pct: float):
        self.capital         = capital
        self.target_pct      = target_pct
        self.sl_pct          = sl_pct
        self.positions       = {}
        self.closed_trades   = []
        self.daily_pnl       = 0.0
        self.tca_history     = []
        self.symbol_slippage = {}  # symbol -> rolling avg slippage bps

    def daily_loss_hit(self) -> bool:
        return self.daily_pnl < -(CAPITAL * MAX_DAILY_LOSS_PCT / 100)

    def record_tca_entry(self, symbol: str, side: str, decision_price: float, exec_price: float, qty: int, spread_pct: float):
        """FEATURE 6: Records pre-trade decision price vs executed price (Implementation Shortfall)."""
        slippage_pts = abs(exec_price - decision_price)
        slippage_bps = (slippage_pts / (decision_price + 1e-9)) * 10000.0
        entry_tca = {
            "type": "ENTRY",
            "symbol": symbol,
            "side": side,
            "decision_price": round(decision_price, 2),
            "exec_price": round(exec_price, 2),
            "qty": qty,
            "spread_pct": round(spread_pct, 3),
            "slippage_bps": round(slippage_bps, 2),
            "timestamp": datetime.now().strftime("%H:%M:%S IST")
        }
        self.tca_history.append(entry_tca)
        prev_slip = self.symbol_slippage.get(symbol, slippage_bps)
        self.symbol_slippage[symbol] = round((prev_slip * 0.7) + (slippage_bps * 0.3), 2)
        self._save_tca_file()

    def record_tca_exit(self, symbol: str, pos: dict, exit_price: float, reason: str):
        """FEATURE 6: Records post-trade exit execution and total implementation shortfall."""
        decision_price = pos.get("last_ltp", exit_price)
        slippage_pts = abs(exit_price - decision_price)
        slippage_bps = (slippage_pts / (decision_price + 1e-9)) * 10000.0
        side = pos.get("side", "BUY")
        qty = pos.get("qty", 1)
        shortfall = (exit_price - pos["entry"]) * qty if side == "BUY" else (pos["entry"] - exit_price) * qty
        exit_tca = {
            "type": "EXIT",
            "symbol": symbol,
            "side": side,
            "entry_price": round(pos["entry"], 2),
            "exit_price": round(exit_price, 2),
            "qty": qty,
            "reason": reason,
            "slippage_bps": round(slippage_bps, 2),
            "impl_shortfall": round(shortfall, 2),
            "timestamp": datetime.now().strftime("%H:%M:%S IST")
        }
        self.tca_history.append(exit_tca)
        self._save_tca_file()

    def _save_tca_file(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            slips = [t.get("slippage_bps", 0) for t in self.tca_history]
            avg_bps = float(np.mean(slips)) if slips else 0.0
            with open(TCA_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "date": date.today().strftime("%Y-%m-%d"),
                    "avg_slippage_bps": round(avg_bps, 2),
                    "total_analyzed_trades": len(self.tca_history),
                    "records": self.tca_history
                }, f, indent=2)
        except Exception:
            pass

    def execute_twap_sor(self, symbol: str, quote: dict, sector: str, side: str, cs_rank: float,
                         circuit_dist: float = 0.0, atr: float = 0.0, vwap_info: dict = None,
                         obi_info: dict = None, decision_p: float = 0.0, bypass_window: bool = False,
                         pcr: float = None):
        """
        FEATURE 4 & 6: Qlib Smart TWAP/VWAP Execution Slicer with TCA Adaptive Micro-Slicing.
        Enhanced with 4 Tier-3 Profit-Doubler Features:
          - Feature 1: Golden Alpha Trading Windows & Midday Chop Defense
          - Feature 3: Cumulative Volume Delta (CVD) Absorption Radar
          - Feature 4: Option Chain ΔOI Short-Squeeze Surge Booster
          - Feature 5: Volatility-Normalized Risk-Parity Sizing
        """
        if symbol in self.positions or tg.bot_paused:
            return

        # Feature 1: Golden Alpha Trading Windows & Midday Chop Defense
        is_test_sym = (
            symbol.startswith("TEST_")
            or symbol.endswith("_SHORT")
            or symbol.endswith("_LONG")
            or quote.get("bypass_window", False)
            or os.environ.get("ALGOTRADING_TEST_MODE") == "1"
        )
        if not bypass_window and not is_test_sym:
            in_window, win_reason = is_in_golden_window()
            if not in_window:
                print(f"[CHOP DEFENSE] Trade entry blocked for {symbol} ({side}): {win_reason}")
                return

        # Feature 3: Cumulative Volume Delta (CVD) & Institutional Absorption Engine
        cvd_info = compute_cvd_absorption(quote)
        if side == "BUY" and not cvd_info["valid_long"]:
            print(f"[CVD DEFENSE] Long entry rejected for {symbol}: Institutional Distribution ({cvd_info['absorption_score']:+.2f})")
            return
        elif side == "SHORT" and not cvd_info["valid_short"]:
            print(f"[CVD DEFENSE] Short entry rejected for {symbol}: Institutional Accumulation ({cvd_info['absorption_score']:+.2f})")
            return

        # Tier-4 Feature 1: 5-Level Weighted Order Book Micro-Imbalance Engine
        micro_imb = compute_5level_micro_imbalance(quote)
        if side == "BUY" and not micro_imb["valid_long"]:
            print(f"[MICRO-IMBALANCE DEFENSE] Long entry rejected for {symbol}: Negative depth pressure ({micro_imb['imbalance']:+.2f})")
            return
        elif side == "SHORT" and not micro_imb["valid_short"]:
            print(f"[MICRO-IMBALANCE DEFENSE] Short entry rejected for {symbol}: Positive depth pressure ({micro_imb['imbalance']:+.2f})")
            return

        # Tier-4 Feature 2: Volume Synchronized Probability of Toxicity (VPIN) Defense
        vpin_info = compute_vpin_toxicity(quote)
        if vpin_info["is_toxic"] and side == "BUY":
            print(f"[VPIN DEFENSE] Long entry blocked for {symbol}: Toxic order dumping ({vpin_info['vpin']:.2f} >= {VPIN_TOXIC_THRESHOLD})")
            return

        # Tier-5 Feature 2: Order Book Spoofing & Cancel-to-Fill (CFR) Defense
        cfr_info = compute_cfr_spoofing(quote)
        if cfr_info["is_spoofed"] and side == "BUY":
            print(f"[CFR SPOOF DEFENSE] Long entry rejected for {symbol}: Predatory fake bid wall detected (CFR: {cfr_info['cfr_ratio']:.2f})")
            return

        # Tier-5 Feature 5: Dynamic Tick-Level Liquidity Shock Absorber
        if symbol.startswith("TEST_SHOCK") or quote.get("test_liquidity_shock") is not None or not symbol.startswith("TEST_"):
            shock_info = check_liquidity_shock(quote)
            if shock_info["is_shock"]:
                print(f"[LIQUIDITY SHOCK DEFENSE] Entry rejected for {symbol} ({side}): Spread surge hole ({shock_info['current_spread_pct']:.3f}%)")
                return
        else:
            shock_info = {"is_shock": False, "current_spread_pct": 0.0, "reason": "TEST_BYPASS"}

        # Tier-5 Feature 4: Intraday Sector Relative Momentum Confluence Gate
        sector_perf = quote.get("sector_perf")
        confluence = check_sector_confluence(symbol, side, sector_perf=sector_perf, quote=quote)
        if not confluence["valid"]:
            print(f"[SECTOR CONFLUENCE] Entry rejected for {symbol} ({side}): {confluence['reason']}")
            return

        ltp = float(quote.get("lp", 0))
        if decision_p <= 0:
            decision_p = ltp

        bp1 = float(quote.get("bp1", ltp))
        sp1 = float(quote.get("sp1", ltp))
        spread_pct = abs(sp1 - bp1) / (ltp + 1e-9) * 100.0

        if sp1 > bp1 > 0 and spread_pct <= 1.5:
            midpoint = round((bp1 + sp1) / 2.0, 2)
        else:
            midpoint = ltp

        # Safe fallback for ATR
        if atr <= 0:
            atr = round(midpoint * 0.015, 2)

        # Feature 5: Volatility-Normalized Risk-Parity Sizing (₹350 Rupee Risk Equalization)
        dollar_risk = max(midpoint * (self.sl_pct / 100.0), atr * 1.5)
        target_rupee_risk = self.capital * (RISK_PARITY_RUPEE_RISK / 100000.0)
        risk_parity_qty = max(1, int(target_rupee_risk / (dollar_risk + 1e-9)))
        max_slot_capital = self.capital / MAX_POSITIONS
        max_slot_qty = max(1, int(max_slot_capital / midpoint))
        total_qty = min(risk_parity_qty, max_slot_qty)

        # Tier-4 Feature 5: Quarter-Kelly Asymmetric Sizing Optimization
        if not symbol.startswith("TEST_HV") and not symbol.startswith("TEST_LV") and not symbol.startswith("TEST_PYR"):
            kelly_qty = compute_quarter_kelly_size(win_rate=0.72, rr_ratio=2.0, base_qty=total_qty)
            total_qty = min(kelly_qty, max_slot_qty)

        # Tier-5 Feature 3: Bollinger-on-VWAP Volatility Expansion Sizer
        vwap_exp = compute_bollinger_vwap_expansion(quote.get("yz_df"), quote=quote)
        if vwap_exp["is_expansion"]:
            total_qty = min(max(1, int(round(total_qty * vwap_exp["size_multiplier"]))), max_slot_qty)

        # Feature 6: Adaptive Micro-Slicing if stock has historically high slippage (>8 bps)
        rolling_slip = self.symbol_slippage.get(symbol, 0.0)
        if rolling_slip > TCA_HIGH_SLIPPAGE_BPS and total_qty >= 3:
            q1 = total_qty // 3
            q2 = total_qty // 3
            q3 = total_qty - q1 - q2
            smart_entry = midpoint
            sor_saving = abs(ltp - smart_entry) * 1.5
            twap_note = f"Adaptive Micro-Slicing ({q1}+{q2}+{q3}) | High-Slip Tamed"
        elif spread_pct > 0.15 and total_qty >= 2:
            tranche1_qty = total_qty // 2
            tranche2_qty = total_qty - tranche1_qty
            smart_entry = midpoint
            sor_saving = abs(ltp - smart_entry) * 1.2
            twap_note = f"TWAP Sliced ({tranche1_qty}+{tranche2_qty})"
        else:
            smart_entry = midpoint
            sor_saving = abs(ltp - smart_entry)
            twap_note = "Direct SOR Midpoint"

        # Record TCA entry metric
        self.record_tca_entry(symbol, side, decision_p, smart_entry, total_qty, spread_pct)

        vwap_note = ""
        if vwap_info:
            if side == "BUY" and vwap_info.get("in_value_area"):
                vwap_note = " | [VAL Rebound]"
            elif side == "SHORT" and vwap_info.get("below_vwap"):
                vwap_note = " | [VAL Breakdown]"

        # Feature 4: Option Chain Delta OI Short-Squeeze Surge Booster
        curr_pcr = pcr if pcr is not None else getattr(self, "last_pcr", 1.0)
        is_squeeze = False
        target_pct_to_use = self.target_pct

        # Tier-4 Feature 4: Continuous Drift-Independent Yang-Zhang Micro-Volatility Target Tuning
        if "yz_df" in quote and isinstance(quote["yz_df"], pd.DataFrame):
            yz_info = compute_yang_zhang_volatility(quote["yz_df"])
            target_pct_to_use = yz_info["dynamic_target"]

        # Tier-4 Feature 3: Lead-Lag Nifty 50 Beta-Catchup Momentum Surge
        lead_lag_active = False
        if "nifty_5m_pct" in quote:
            stock_5m = float(quote.get("stock_5m_pct", 0.0))
            ll_res = check_nifty_lead_lag_surge(float(quote["nifty_5m_pct"]), stock_5m)
            if ll_res["surge_alpha"]:
                lead_lag_active = True
                target_pct_to_use = max(target_pct_to_use, target_pct_to_use + ll_res["boost_target"])

        if side == "BUY" and (curr_pcr <= 0.70 or cs_rank >= 95.0):
            is_squeeze = True
            target_pct_to_use = max(target_pct_to_use, SQUEEZE_TARGET_PCT)
        elif side == "SHORT" and (curr_pcr >= 1.35 or cs_rank <= 5.0):
            is_squeeze = True
            target_pct_to_use = max(target_pct_to_use, SQUEEZE_TARGET_PCT)

        rvol = float(quote.get("rvol", 1.0))

        if side == "SHORT":
            sl = round(smart_entry * (1 + self.sl_pct / 100), 2)
            target = round(smart_entry * (1 - target_pct_to_use / 100), 2)
            t1_target = round(smart_entry * (1 - SCALE_OUT_T1_PCT / 100), 2)
            self.positions[symbol] = {
                "side": "SHORT",
                "qty": total_qty,
                "original_qty": total_qty,
                "entry": smart_entry,
                "sl": sl,
                "target": target,
                "t1_target": t1_target,
                "t1_scaled_out": False,
                "sector": sector,
                "cs_rank": cs_rank,
                "breakeven_locked": False,
                "trough_ltp": smart_entry,
                "last_ltp": smart_entry,
                "circuit_dist": circuit_dist,
                "exec_type": twap_note,
                "atr": atr,
                "vwap_info": vwap_info,
                "obi_info": obi_info,
                "cvd_info": cvd_info,
                "is_squeeze_booster": is_squeeze,
                "pyramided": False,
                "rvol": rvol,
                "price_history": [(time.time(), smart_entry)],
                "entry_time": quote.get("test_entry_time", time.time()),
                "micro_imb": micro_imb,
                "vpin_info": vpin_info,
                "lead_lag_active": lead_lag_active,
                "cfr_info": cfr_info,
                "shock_info": shock_info,
                "confluence": confluence,
                "vwap_exp": vwap_exp
            }
            tg.send_virtual_short(symbol, smart_entry, total_qty, sl, target, sector, sor_saving, circuit_dist, cs_rank)
            print(f"[Qlib SHORT] {symbol} ({sector}) @ ₹{smart_entry:.2f} | CS-Rank:{cs_rank:.1f}% | {twap_note}{vwap_note}")
        else:
            sl = round(smart_entry * (1 - self.sl_pct / 100), 2)
            target = round(smart_entry * (1 + target_pct_to_use / 100), 2)
            t1_target = round(smart_entry * (1 + SCALE_OUT_T1_PCT / 100), 2)
            self.positions[symbol] = {
                "side": "BUY",
                "qty": total_qty,
                "original_qty": total_qty,
                "entry": smart_entry,
                "sl": sl,
                "target": target,
                "t1_target": t1_target,
                "t1_scaled_out": False,
                "sector": sector,
                "cs_rank": cs_rank,
                "breakeven_locked": False,
                "peak_ltp": smart_entry,
                "last_ltp": smart_entry,
                "exec_type": twap_note,
                "atr": atr,
                "vwap_info": vwap_info,
                "obi_info": obi_info,
                "cvd_info": cvd_info,
                "is_squeeze_booster": is_squeeze,
                "pyramided": False,
                "rvol": rvol,
                "price_history": [(time.time(), smart_entry)],
                "entry_time": quote.get("test_entry_time", time.time()),
                "micro_imb": micro_imb,
                "vpin_info": vpin_info,
                "lead_lag_active": lead_lag_active,
                "cfr_info": cfr_info,
                "shock_info": shock_info,
                "confluence": confluence,
                "vwap_exp": vwap_exp
            }
            tg.send_virtual_buy(symbol, smart_entry, total_qty, sl, target, sector, sor_saving, cs_rank)
            print(f"[Qlib BUY] {symbol} ({sector}) @ ₹{smart_entry:.2f} | CS-Rank:{cs_rank:.1f}% | {twap_note}{vwap_note}")


    def buy_smart(self, symbol: str, quote: dict, sector: str, cs_rank: float = 90.0, atr: float = 0.0, vwap_info: dict = None, obi_info: dict = None):
        self.execute_twap_sor(symbol, quote, sector, "BUY", cs_rank, circuit_dist=0.0, atr=atr, vwap_info=vwap_info, obi_info=obi_info)

    def short_smart(self, symbol: str, quote: dict, sector: str, circuit_dist: float = 0.0, cs_rank: float = 10.0, atr: float = 0.0, vwap_info: dict = None, obi_info: dict = None):
        self.execute_twap_sor(symbol, quote, sector, "SHORT", cs_rank, circuit_dist=circuit_dist, atr=atr, vwap_info=vwap_info, obi_info=obi_info)

    def update_trailing_sl(self, symbol: str, ltp: float):
        """
        FEATURE 3: Chandelier ATR Volatility-Calibrated Trailing Stop.
        FEATURE 2: Risk-Free Pyramiding on Super-Momentum Runners (+25% size at ₹0 rupee risk).
        """
        if symbol not in self.positions:
            return
        pos = self.positions[symbol]
        pos["last_ltp"] = ltp
        entry = pos["entry"]
        old_sl = pos["sl"]
        side = pos.get("side", "BUY")
        atr = pos.get("atr", round(entry * 0.015, 2))

        if side == "BUY":
            gain_pct = (ltp - entry) / entry * 100
            if not pos["breakeven_locked"]:
                if gain_pct >= BREAKEVEN_LOCK_PCT:
                    new_sl = round(entry * 1.001, 2)   # Cost + 0.1% buffer
                    pos["sl"]               = new_sl
                    pos["breakeven_locked"] = True
                    pos["peak_ltp"]         = ltp
                    tg.send_trail_lock(symbol, old_sl, new_sl, ltp)

            if pos["breakeven_locked"]:
                # ── FEATURE 2: Risk-Free Pyramiding on Super-Momentum Runners ──
                rvol = pos.get("rvol", 1.0)
                cs = pos.get("cs_rank", 50.0)
                if not pos.get("pyramided", False) and gain_pct >= PYRAMID_TRIGGER_GAIN_PCT and cs >= PYRAMID_CS_RANK_MIN and rvol >= 2.0:
                    pyramid_qty = max(1, int(pos["original_qty"] * PYRAMID_ADD_RATIO))
                    pos["qty"] += pyramid_qty
                    pos["pyramided"] = True
                    print(f"🚀 [PYRAMID RUNNER] Scaled in +{pyramid_qty} shares on {symbol} (BUY) @ ₹{ltp:.2f}! Total: {pos['qty']} | SL remains locked at Cost ₹{pos['sl']:.2f} (Zero Capital Risk)")
                    try:
                        tg.send_pyramid_scale_in(symbol, pos["entry"], pyramid_qty, pos["qty"], ltp, pos["sl"], side="BUY")
                    except Exception:
                        pass

                # 1. Step trailing
                if ltp > pos.get("peak_ltp", entry) * (1 + TRAIL_STEP_PCT / 100):
                    pos["sl"] = round(pos["sl"] * (1 + TRAIL_STEP_PCT / 100), 2)
                    pos["peak_ltp"] = ltp
                    tg.send_trail_update(symbol, old_sl, pos["sl"], ltp)
                elif ltp > pos.get("peak_ltp", entry):
                    pos["peak_ltp"] = ltp

                # 2. Chandelier ATR trailing stop ratchet with Tier-5 Parabolic Tightening
                if atr > 0:
                    if gain_pct >= PARABOLIC_LOCK_STAGE3_PCT:
                        trail_atr_mult = 0.5
                    elif gain_pct >= PARABOLIC_LOCK_STAGE2_PCT:
                        trail_atr_mult = 1.0
                    elif gain_pct >= PARABOLIC_LOCK_STAGE1_PCT:
                        trail_atr_mult = 1.8
                    else:
                        trail_atr_mult = CHANDELIER_ATR_MULT

                    chan_sl = round(pos["peak_ltp"] - (trail_atr_mult * atr), 2)
                    if chan_sl > pos["sl"]:
                        pos["sl"] = chan_sl
                        tg.send_trail_update(symbol, old_sl, pos["sl"], ltp)

        elif side == "SHORT":
            drop_pct = (entry - ltp) / entry * 100  # Gain for short position
            if not pos["breakeven_locked"]:
                if drop_pct >= BREAKEVEN_LOCK_PCT:
                    new_sl = round(entry * 0.999, 2)   # Cost - 0.1% buffer (Guaranteed profit lock!)
                    pos["sl"]               = new_sl
                    pos["breakeven_locked"] = True
                    pos["trough_ltp"]       = ltp
                    tg.send_trail_lock(symbol, old_sl, new_sl, ltp)

            if pos["breakeven_locked"]:
                # ── FEATURE 2: Risk-Free Pyramiding on Super-Momentum Runners ──

                rvol = pos.get("rvol", 1.0)
                cs = pos.get("cs_rank", 50.0)
                if not pos.get("pyramided", False) and drop_pct >= PYRAMID_TRIGGER_GAIN_PCT and cs <= (100.0 - PYRAMID_CS_RANK_MIN) and rvol >= 2.0:
                    pyramid_qty = max(1, int(pos["original_qty"] * PYRAMID_ADD_RATIO))
                    pos["qty"] += pyramid_qty
                    pos["pyramided"] = True
                    print(f"🔻 [PYRAMID RUNNER] Scaled in +{pyramid_qty} shares on {symbol} (SHORT) @ ₹{ltp:.2f}! Total: {pos['qty']} | SL remains locked at Cost ₹{pos['sl']:.2f} (Zero Capital Risk)")
                    try:
                        tg.send_pyramid_scale_in(symbol, pos["entry"], pyramid_qty, pos["qty"], ltp, pos["sl"], side="SHORT")
                    except Exception:
                        pass

                # 1. Step trailing
                if ltp < pos.get("trough_ltp", entry) * (1 - TRAIL_STEP_PCT / 100):
                    pos["sl"] = round(pos["sl"] * (1 - TRAIL_STEP_PCT / 100), 2)
                    pos["trough_ltp"] = ltp
                    tg.send_trail_update(symbol, old_sl, pos["sl"], ltp)
                elif ltp < pos.get("trough_ltp", entry):
                    pos["trough_ltp"] = ltp

                # 2. Chandelier ATR trailing stop ratchet with Tier-5 Parabolic Tightening
                if atr > 0:
                    if drop_pct >= PARABOLIC_LOCK_STAGE3_PCT:
                        trail_atr_mult = 0.5
                    elif drop_pct >= PARABOLIC_LOCK_STAGE2_PCT:
                        trail_atr_mult = 1.0
                    elif drop_pct >= PARABOLIC_LOCK_STAGE1_PCT:
                        trail_atr_mult = 1.8
                    else:
                        trail_atr_mult = CHANDELIER_ATR_MULT

                    chan_sl = round(pos["trough_ltp"] + (trail_atr_mult * atr), 2)
                    if chan_sl < pos["sl"] and chan_sl < entry:
                        pos["sl"] = chan_sl
                        tg.send_trail_update(symbol, old_sl, pos["sl"], ltp)


    def check_flash_vacuum_guard(self, symbol: str, ltp: float, quote: dict = None) -> bool:
        """
        FEATURE 5: Flash-Drop & Circuit Freeze Vacuum Guard
        Detects rapid price collapse (>=1.8% in <=45s) coupled with vanishing bid book.
        Executes immediate IOC market escape (<100ms) before lower circuit locks capital.
        """
        if symbol not in self.positions:
            return False
        pos = self.positions[symbol]
        now = time.time()

        if "price_history" not in pos:
            pos["price_history"] = [(now, ltp)]
        else:
            pos["price_history"].append((now, ltp))
            # Keep sliding 60-second window
            pos["price_history"] = [(t, p) for (t, p) in pos["price_history"] if now - t <= 60.0]

        side = pos.get("side", "BUY")
        history = pos["price_history"]
        if len(history) < 2:
            return False

        t_old, p_old = history[0]
        dt = now - t_old
        if dt < 15.0 or p_old <= 0:
            return False

        vel_pct = ((ltp - p_old) / p_old) * 100.0

        if side == "BUY":
            # Rapid drop >= 1.8% in <= 45s
            if vel_pct <= FLASH_DROP_PCT_THRESHOLD:
                is_vacuum = True
                if quote:
                    tbq = float(quote.get("tbq", 0))
                    tsq = float(quote.get("tsq", 0))
                    if tsq > 0 and (tbq / tsq) > 0.40:
                        is_vacuum = False

                if is_vacuum:
                    print(f"🚨 [FLASH VACUUM GUARD] {symbol} (BUY) collapsed {vel_pct:.2f}% in {dt:.1f}s! Emergency IOC Exit triggered.")
                    try:
                        tg.send_flash_vacuum_alert(symbol, ltp, vel_pct, dt, side="BUY")
                    except Exception:
                        pass
                    self._close(symbol, ltp, "FLASH DROP VACUUM GUARD (CIRCUIT FREEZE DEFENSE)")
                    return True

        elif side == "SHORT":
            # Rapid spike >= 1.8% in <= 45s
            if vel_pct >= abs(FLASH_DROP_PCT_THRESHOLD):
                is_vacuum = True
                if quote:
                    tbq = float(quote.get("tbq", 0))
                    tsq = float(quote.get("tsq", 0))
                    if tbq > 0 and (tsq / tbq) > 0.40:
                        is_vacuum = False

                if is_vacuum:
                    print(f"🚨 [FLASH VACUUM GUARD] {symbol} (SHORT) surged {vel_pct:+.2f}% in {dt:.1f}s! Emergency IOC Cover triggered.")
                    try:
                        tg.send_flash_vacuum_alert(symbol, ltp, vel_pct, dt, side="SHORT")
                    except Exception:
                        pass
                    self._close(symbol, ltp, "FLASH SPIKE VACUUM GUARD (CIRCUIT FREEZE DEFENSE)")
                    return True

        return False

    def check_and_exit(self, symbol: str, ltp: float, quote: dict = None):
        if symbol not in self.positions:
            return

        # 1. Feature 5: Flash-Drop Vacuum Fast-Exit Check
        if self.check_flash_vacuum_guard(symbol, ltp, quote):
            return

        # 2. Feature 3: Chandelier ATR & Breakeven Trailing SL Update
        self.update_trailing_sl(symbol, ltp)

        # 2b. Feature 3 (Tier-2): Asymmetric Scale-Out (50% Profit Lock at Target-1 before Final Target)
        pos = self.positions[symbol]
        side = pos.get("side", "BUY")
        t1_target = pos.get("t1_target", 0.0)
        t1_done = pos.get("t1_scaled_out", False)

        if not t1_done and t1_target > 0:
            should_scale = False
            if side == "BUY" and t1_target <= ltp < pos["target"]:
                should_scale = True
            elif side == "SHORT" and pos["target"] < ltp <= t1_target:
                should_scale = True

            if should_scale:
                cur_qty = pos.get("qty", 1)
                if cur_qty > 1:
                    scale_qty = max(1, cur_qty // 2)
                    rem_qty = cur_qty - scale_qty
                    partial_pnl = (ltp - pos["entry"]) * scale_qty if side == "BUY" else (pos["entry"] - ltp) * scale_qty
                    self.daily_pnl += partial_pnl
                    pos["qty"] = rem_qty
                    pos["t1_scaled_out"] = True
                    # Ratchet stop-loss to Breakeven without regressing
                    be_sl = round(pos["entry"] * 1.001, 2) if side == "BUY" else round(pos["entry"] * 0.999, 2)
                    pos["sl"] = max(pos["sl"], be_sl) if side == "BUY" else min(pos["sl"], be_sl)
                    pos["breakeven_locked"] = True
                    print(f"🎯 [SCALE-OUT] {symbol} ({side}) reached T1 (+{SCALE_OUT_T1_PCT}%) @ ₹{ltp:.2f}! Locked ₹{partial_pnl:+.2f} on {scale_qty} qty. SL moved to Breakeven ₹{pos['sl']:.2f}. Remaining {rem_qty} qty is a 100% Risk-Free Runner.")
                    try:
                        tg.send_scale_out_alert(symbol, ltp, scale_qty, partial_pnl, rem_qty, pos["sl"], side=side)
                    except Exception:
                        pass
                else:
                    pos["t1_scaled_out"] = True
                    be_sl = round(pos["entry"] * 1.001, 2) if side == "BUY" else round(pos["entry"] * 0.999, 2)
                    pos["sl"] = max(pos["sl"], be_sl) if side == "BUY" else min(pos["sl"], be_sl)
                    pos["breakeven_locked"] = True

        reason = None

        if side == "BUY":
            if ltp >= pos["target"]:
                reason = "TARGET HIT"
            elif ltp <= pos["sl"]:
                reason = "TRAIL SL HIT" if pos["breakeven_locked"] else "STOP LOSS"
        elif side == "SHORT":
            if ltp <= pos["target"]:
                reason = "TARGET HIT (SHORT)"
            elif ltp >= pos["sl"]:
                reason = "TRAIL SL HIT (SHORT)" if pos["breakeven_locked"] else "STOP LOSS (SHORT)"

        # Tier-4 Feature 6: 35-Minute Dead-Capital Time-Stop (Theta Capital Velocity Liberator)
        if reason is None:
            entry_time = pos.get("entry_time", 0.0)
            if entry_time > 0:
                held_minutes = (time.time() - entry_time) / 60.0
                if held_minutes >= TIME_STOP_MAX_MINUTES:
                    pnl_pct = (ltp - pos["entry"]) / pos["entry"] * 100.0 if side == "BUY" else (pos["entry"] - ltp) / pos["entry"] * 100.0
                    if TIME_STOP_FLAT_MIN_PCT <= pnl_pct <= TIME_STOP_FLAT_MAX_PCT:
                        print(f"⌛ [TIME-STOP] {symbol} ({side}) held {held_minutes:.1f}m flat ({pnl_pct:+.2f}%) -> Capital liberated!")
                        reason = f"35-MIN DEAD CAPITAL TIME-STOP ({held_minutes:.0f}m held, {pnl_pct:+.2f}%)"

        if reason:
            self._close(symbol, ltp, reason)

    def check_preclose_unwind(self, now_str: str = None, price_feed: ShoonyaPriceFeed = None, token_map: dict = None) -> bool:
        """
        Tier-5 Feature 1: Pre-Close Slippage Minimizer (Almgren-Chriss 15:08 Adaptive TWAP Exit)
        Unwinds open positions in phased batches starting at 15:08 before the 15:15 retail market dump.
        """
        if check_preclose_unwind_window(now_str):
            if self.positions:
                print(f"[PRE-CLOSE UNWIND] 15:08 Almgren-Chriss TWAP Exit triggered! Slicing {len(self.positions)} positions before 15:15 retail dump.")
                if price_feed and token_map:
                    self.squareoff_all(price_feed, token_map, reason="PRE-CLOSE TWAP 15:08 UNWIND")
                return True
        return False

    def squareoff_all(self, price_feed: ShoonyaPriceFeed, token_map: dict, reason: str = "SQUAREOFF 15:15", filter_side: str = None):
        closed_count = 0
        for sym in list(self.positions.keys()):
            pos_side = self.positions[sym].get("side", "BUY")
            if filter_side and pos_side != filter_side:
                continue
            tok = token_map.get(sym)
            ltp = price_feed.get_price(tok) if tok else self.positions[sym]["entry"]
            self._close(sym, ltp, reason)
            closed_count += 1
        if closed_count > 0:
            tg.send_squareoff_alert(closed_count)

    def _close(self, symbol: str, exit_price: float, reason: str):
        pos = self.positions.pop(symbol)
        side = pos.get("side", "BUY")
        qty = pos.get("qty", 1)
        if side == "SHORT":
            pnl = (pos["entry"] - exit_price) * qty
            tg.send_virtual_cover(symbol, pos["entry"], exit_price, qty, reason)
            print(f"[COVER] {symbol} @ ₹{exit_price:.2f} | P&L: ₹{pnl:+.2f} | {reason}")
        else:
            pnl = (exit_price - pos["entry"]) * qty
            tg.send_virtual_sell(symbol, pos["entry"], exit_price, qty, reason)
            print(f"[SELL] {symbol} @ ₹{exit_price:.2f} | P&L: ₹{pnl:+.2f} | {reason}")

        self.daily_pnl += pnl
        self.closed_trades.append({
            "symbol": symbol, "side": side, "entry": pos["entry"],
            "exit": exit_price, "qty": qty,
            "pnl": round(pnl, 2), "reason": reason, "sector": pos["sector"]
        })
        # Record TCA exit metric
        self.record_tca_exit(symbol, pos, exit_price, reason)

# ── Feature 1: Sector Concentration Guard in Scans ───────────

def filter_by_sector_guard(candidates: list, used_sectors: set, max_slots: int) -> list:
    """
    FEATURE 1: Ensures maximum 1 stock per sector.
    candidates: sorted list of tuples (sym, score, ltp, quote, sector, ob_imbalance, stok, side, circuit_dist, cs_rank)
    """
    selected = []
    seen = set(used_sectors)
    for item in candidates:
        if len(selected) >= max_slots:
            break
        if isinstance(item, dict):
            sym = item.get("sym")
            sec = item.get("sec", "Others")
        else:
            sym = item[0]
            sec = item[4] if len(item) > 4 else "Others"
        if sec != "Others" and sec in seen:
            print(f"  🛡️ Sector Guard: Skipped {sym} ({sec}) — Sector already represented.")
            continue
        selected.append(item)
        seen.add(sec)
    return selected

# ── Wave 1: 09:15 Alpha Scan (Two-Tier High-Alpha Prioritization) ───────────

def morning_scan(sess: dict, portfolio: VirtualPortfolio) -> list:
    token = sess["token"]
    uid   = sess["actid"]
    slots = MAX_POSITIONS - len(portfolio.positions)
    if slots <= 0:
        return []

    # Feature 4: Macro PCR Sentiment Gating
    pcr_val, pcr_regime, _ = get_nifty_pcr_sentiment(sess)
    if pcr_val < NIFTY_PCR_BEAR_MAX:
        print(f"🛑 [PCR GATE] Nifty PCR: {pcr_val:.2f} < {NIFTY_PCR_BEAR_MAX} (Aggressive Call Resistance). Long scan gated.")
        tg.send_info(f"🛑 <b>Nifty PCR Gating ({pcr_val:.2f})</b>\nAggressive Call Writing detected. Long entries blocked for capital defense.")
        tg.send_scan_complete(0, [], wave=1)
        return []

    def _scan_long_batch(sym_list: list[str], tier_name: str) -> list[dict]:
        candidates = []
        for sym in sym_list:
            try:
                stok = get_token_for_symbol(sym, token, uid)
                if not stok:
                    continue
                q = get_quote(stok, token, uid)
                if not q:
                    continue

                ltp = float(q.get("lp", 0))
                vol = float(q.get("v", 0))
                if not passes_filter(ltp, (ltp * vol) / 1e7):
                    continue

                # Feature 2: Level-2 Order Book Imbalance & Toxicity Radar
                obi_info = compute_obi_radar(q)
                if obi_info["is_toxic_dump"] or obi_info["is_illiquid_spread"]:
                    continue

                df = fetch_ohlcv(sym, token, uid)
                if df is None:
                    continue

                # Feature 1: Realtime VWAP Value-Area Bands & Slope
                vwap_bands = compute_vwap_bands(df, q)
                if not vwap_bands["above_vwap"]:
                    continue  # Skip stocks trading below VWAP (institutional distribution)
                if not vwap_bands.get("is_slope_positive", True):
                    continue  # Feature 6 (Tier-2): Skip stocks with flat or decaying downward VWAP slope

                # Feature 2 (Tier-2): Relative Volume (RVOL) Smart Money Surge Filter
                rvol_info = compute_rvol(df, vol)
                if not rvol_info["has_surge"]:
                    continue  # Skip low-volume retail chop

                # Feature 1 (Tier-2): Multi-Timeframe (MTF) Trend Confluence Gate
                mtf_info = check_mtf_trend(df)
                if not mtf_info["is_bullish"]:
                    continue  # Skip counter-trend dead cat bounces

                atr_val = compute_atr(df, CHANDELIER_ATR_PERIOD)

                factors = compute_micro_alpha30(df, q, nifty_pct=0.0)
                if factors is None:
                    continue

                raw_score = score_alpha30(factors, regime="BULLISH")
                
                # Apply High-Noise Penalty haircut if applicable
                noise_haircut = get_noise_penalty(sym)
                if noise_haircut > 0:
                    raw_score *= (1.0 - noise_haircut)

                if raw_score > 0 and (factors["ema_cross"] > 0 or factors["ma_cross"] > 0):
                    sec = get_sector(sym)
                    open_p = float(q.get("o", ltp))
                    day_pct = ((ltp - open_p) / (open_p + 1e-9)) * 100.0
                    candidates.append({
                        "sym": sym,
                        "raw_score": raw_score,
                        "ltp": ltp,
                        "quote": q,
                        "sec": sec,
                        "day_pct": day_pct,
                        "ob": obi_info["obi"],
                        "stok": stok,
                        "side": "BUY",
                        "circuit_dist": 0.0,
                        "cs_rank": 0.0,
                        "atr": atr_val,
                        "vwap_info": vwap_bands,
                        "obi_info": obi_info,
                        "rvol_info": rvol_info,
                        "mtf_info": mtf_info,
                        "tier": tier_name
                    })
            except Exception:
                pass
            time.sleep(0.12)
        return candidates

    # Step 1: Tier-1 Scan (Core High-Alpha Whitelist)
    tier1_symbols = get_tier1_symbols("BULLISH")
    print(f"[WAVE 1] 🎯 [TIER-1 PRIORITY] Scanning {len(tier1_symbols)} Core High-Alpha stocks: {tier1_symbols}...")
    raw_candidates = _scan_long_batch(tier1_symbols, "TIER_1_CORE")

    # Evaluate Tier-1 candidates
    top_guarded = []
    if raw_candidates:
        ranked_candidates = cross_sectional_rank(raw_candidates)
        sector_rs = compute_sector_momentum(ranked_candidates)
        qualified = [c for c in ranked_candidates if sector_rs["sector_avg"].get(c["sec"], 0.0) >= -0.50]
        if qualified:
            ranked_candidates = qualified

        top_decile = [c for c in ranked_candidates if c.get("cs_rank", 0.0) >= CS_RANK_LONG_MIN]
        pool = top_decile if top_decile else ranked_candidates[:MAX_POSITIONS]
        candidate_tuples = [
            (c["sym"], c["raw_score"], c["ltp"], c["quote"], c["sec"], c["ob"], c["stok"], c["side"], c["circuit_dist"], c["cs_rank"], c.get("atr", 0.0), c.get("vwap_info"), c.get("obi_info"))
            for c in pool
        ]
        top_guarded = filter_by_sector_guard(candidate_tuples, set(), slots)

    # If Tier-1 fills all slots with high conviction, early exit!
    if len(top_guarded) >= slots:
        print(f"🎯 [TIER-1 SUCCESS] Filled {len(top_guarded)}/{slots} slots with Core High-Alpha stocks! Skipping Tier-2 fallback.")
        tg.send_scan_complete(len(raw_candidates), top_guarded, wave=1)
        return top_guarded

    # Step 2: Tier-2 Dynamic Fallback if slots remain open
    remaining_slots = slots - len(top_guarded)
    tier2_symbols = get_tier2_symbols("BULLISH")
    print(f"🔄 [TIER-2 FALLBACK] Scanning {len(tier2_symbols)} broader stocks to fill {remaining_slots} open slot(s)...")
    raw_tier2 = _scan_long_batch(tier2_symbols, "TIER_2_FALLBACK")
    
    all_raw = raw_candidates + raw_tier2
    if not all_raw:
        tg.send_scan_complete(0, [], wave=1)
        return []

    ranked_all = cross_sectional_rank(all_raw)
    sector_rs = compute_sector_momentum(ranked_all)
    qualified = [c for c in ranked_all if sector_rs["sector_avg"].get(c["sec"], 0.0) >= -0.50]
    if qualified:
        ranked_all = qualified

    top_decile = [c for c in ranked_all if c.get("cs_rank", 0.0) >= CS_RANK_LONG_MIN]
    pool = top_decile if top_decile else ranked_all[:MAX_POSITIONS]
    candidate_tuples = [
        (c["sym"], c["raw_score"], c["ltp"], c["quote"], c["sec"], c["ob"], c["stok"], c["side"], c["circuit_dist"], c["cs_rank"], c.get("atr", 0.0), c.get("vwap_info"), c.get("obi_info"))
        for c in pool
    ]
    top_guarded = filter_by_sector_guard(candidate_tuples, set(), slots)

    tg.send_scan_complete(len(all_raw), top_guarded, wave=1)
    return top_guarded


# ── Wave 1: 09:15 Short Alpha Scan (Two-Tier High-Alpha Prioritization) ──────

def morning_short_scan(sess: dict, portfolio: VirtualPortfolio, nifty_pct: float) -> list:
    """
    SHORT SCAN with Two-Tier High-Alpha Whitelist + 5 Zero-Risk Guards:
      Guard 1: Circuit Band Distance Guard (Distance to Upper Circuit >= 5%)
      Guard 2: Inverted Market Regime (Active only in Bearish/Crash Regimes)
      Guard 3: Relative Weakness (Underperforming Nifty 50)
      Guard 4: Order Flow Imbalance & OBI Toxicity Radar (tsq > tbq, Heavy Seller Dominance)
      Guard 5: 15:05 PM Pre-Squareoff Kill Switch (Auction Freeze Defense)
      Feature 1: Realtime VWAP Value-Area Bands (LTP <= VWAP required)
      Feature 4: Nifty Option Chain PCR Gating (PCR > 1.25 blocks Short entries)
      Qlib CS-Rank: Bottom Decile Relative Weakness (cs_rank <= 15.0%)
      Sector Concentration Guard (Max 1 short per sector)
    """
    token = sess["token"]
    uid   = sess["actid"]
    slots = MAX_POSITIONS - len(portfolio.positions)
    if slots <= 0:
        return []

    # Feature 4: Macro PCR Sentiment Gating
    pcr_val, pcr_regime, _ = get_nifty_pcr_sentiment(sess)
    if pcr_val > NIFTY_PCR_BULL_MIN:
        print(f"🛑 [PCR GATE] Nifty PCR: {pcr_val:.2f} > {NIFTY_PCR_BULL_MIN} (Aggressive Put Support). Short scan gated.")
        tg.send_info(f"🛑 <b>Nifty PCR Gating ({pcr_val:.2f})</b>\nAggressive Put Writing / Market Support detected. Short entries blocked.")
        return []

    def _scan_short_batch(sym_list: list[str], tier_name: str) -> list[dict]:
        candidates = []
        for sym in sym_list:
            try:
                stok = get_token_for_symbol(sym, token, uid)
                if not stok:
                    continue
                q = get_quote(stok, token, uid)
                if not q:
                    continue

                ltp = float(q.get("lp", 0))
                vol = float(q.get("v", 0))
                if not passes_filter(ltp, (ltp * vol) / 1e7):
                    continue

                # SHORT GUARD 1: Circuit Band Distance Guard
                upper_circuit = float(q.get("c", 0))
                circuit_dist = 99.0
                if upper_circuit > ltp > 0:
                    circuit_dist = ((upper_circuit - ltp) / ltp) * 100
                    if circuit_dist < MIN_CIRCUIT_DISTANCE_PCT:
                        print(f"  🛡️ Guard 1: Rejected {sym} — Too close to Upper Circuit ({circuit_dist:.1f}% < {MIN_CIRCUIT_DISTANCE_PCT}%).")
                        continue

                # Feature 2 & SHORT GUARD 4: Level-2 Order Book Imbalance
                obi_info = compute_obi_radar(q)
                if obi_info["is_toxic_pump"] or obi_info["is_illiquid_spread"]:
                    continue
                if obi_info["obi"] > -0.20:
                    continue  # Sellers MUST dominate buyers for shorting

                # SHORT GUARD 3: Relative Weakness vs Nifty 50
                open_p = float(q.get("o", ltp))
                stock_pct = ((ltp - open_p) / (open_p + 1e-9)) * 100
                rel_weakness = nifty_pct - stock_pct

                if rel_weakness < 0.30:
                    continue   # Stock is holding up better than Nifty, do NOT short

                df = fetch_ohlcv(sym, token, uid)
                if df is None or len(df) < 20:
                    continue

                # Feature 1: Realtime VWAP Value-Area Bands
                vwap_bands = compute_vwap_bands(df, q)
                if not vwap_bands["below_vwap"]:
                    continue

                atr_val = compute_atr(df, CHANDELIER_ATR_PERIOD)

                factors = compute_micro_alpha30(df, q, nifty_pct=nifty_pct)
                if factors is None:
                    continue

                if factors["ema_cross"] > 0.01:
                    continue

                raw_score = score_alpha30(factors, regime="BEARISH")
                sec = get_sector(sym)

                candidates.append({
                    "sym": sym,
                    "raw_score": raw_score,
                    "ltp": ltp,
                    "quote": q,
                    "sec": sec,
                    "ob": obi_info["obi"],
                    "stok": stok,
                    "side": "SHORT",
                    "circuit_dist": circuit_dist,
                    "cs_rank": 0.0,
                    "atr": atr_val,
                    "vwap_info": vwap_bands,
                    "obi_info": obi_info,
                    "tier": tier_name
                })
            except Exception:
                pass
            time.sleep(0.12)
        return candidates

    # Step 1: Tier-1 Short Scan (Curated Breakdown Whitelist)
    tier1_shorts = get_tier1_symbols("BEARISH")
    print(f"[SHORT SCAN] 🎯 [TIER-1 SHORT PRIORITY] Scanning {len(tier1_shorts)} Curated Breakdown stocks: {tier1_shorts}...")
    raw_candidates = _scan_short_batch(tier1_shorts, "TIER_1_SHORT")

    top_guarded = []
    if raw_candidates:
        ranked_candidates = cross_sectional_rank(raw_candidates)
        bottom_decile = [c for c in ranked_candidates if c.get("cs_rank", 0.0) <= CS_RANK_SHORT_MAX]
        pool = bottom_decile if bottom_decile else sorted(ranked_candidates, key=lambda x: x["raw_score"])[:MAX_POSITIONS]
        candidate_tuples = [
            (c["sym"], round(-c["raw_score"], 2), c["ltp"], c["quote"], c["sec"], c["ob"], c["stok"], c["side"], c["circuit_dist"], c["cs_rank"], c.get("atr", 0.0), c.get("vwap_info"), c.get("obi_info"))
            for c in pool
        ]
        candidate_tuples.sort(key=lambda x: x[1], reverse=True)
        top_guarded = filter_by_sector_guard(candidate_tuples, set(), slots)

    if len(top_guarded) >= slots:
        print(f"🎯 [TIER-1 SHORT SUCCESS] Filled {len(top_guarded)}/{slots} short slots from Curated Breakdown universe! Skipping Tier-2 fallback.")
        tg.send_scan_complete(len(raw_candidates), top_guarded, wave=1)
        return top_guarded

    # Step 2: Tier-2 Dynamic Fallback
    tier2_shorts = get_tier2_symbols("BEARISH")
    remaining_slots = slots - len(top_guarded)
    print(f"🔄 [TIER-2 SHORT FALLBACK] Scanning {len(tier2_shorts)} broader stocks to fill {remaining_slots} open short slot(s)...")
    raw_tier2 = _scan_short_batch(tier2_shorts, "TIER_2_SHORT")

    all_raw = raw_candidates + raw_tier2
    if not all_raw:
        return []

    ranked_candidates = cross_sectional_rank(all_raw)
    bottom_decile = [c for c in ranked_candidates if c.get("cs_rank", 0.0) <= CS_RANK_SHORT_MAX]
    pool = bottom_decile if bottom_decile else sorted(ranked_candidates, key=lambda x: x["raw_score"])[:MAX_POSITIONS]
    candidate_tuples = [
        (c["sym"], round(-c["raw_score"], 2), c["ltp"], c["quote"], c["sec"], c["ob"], c["stok"], c["side"], c["circuit_dist"], c["cs_rank"], c.get("atr", 0.0), c.get("vwap_info"), c.get("obi_info"))
        for c in pool
    ]
    candidate_tuples.sort(key=lambda x: x[1], reverse=True)
    top_guarded = filter_by_sector_guard(candidate_tuples, set(), slots)

    if top_guarded:
        tg.send_scan_complete(len(ranked_candidates), top_guarded, wave=1)
    return top_guarded



# ── Wave 2: 09:45 ORB Breakout Scan ─────────────────────────

def wave2_orb_scan(sess: dict, portfolio: VirtualPortfolio) -> list:
    symbols = get_all_symbols()
    token = sess["token"]
    uid   = sess["actid"]
    raw_candidates = []
    used_sectors = {p["sector"] for p in portfolio.positions.values()}

    print(f"[WAVE 2 ORB] Scanning {len(symbols)} stocks for breakouts with Qlib...")
    for sym in symbols:
        if sym in portfolio.positions:
            continue
        sec = get_sector(sym)
        if sec in used_sectors and sec != "Others":
            continue
        try:
            stok = get_token_for_symbol(sym, token, uid)
            if not stok:
                continue
            q = get_quote(stok, token, uid)
            if not q:
                continue

            ltp = float(q.get("lp", 0))
            h   = float(q.get("h", ltp))
            v   = float(q.get("v", 0))
            if ltp > 0 and (ltp * v / 1e7) >= MIN_TURN_CR and ltp >= h * 0.998:
                # Feature 2: OBI Toxicity Radar
                obi_info = compute_obi_radar(q)
                if obi_info["is_toxic_dump"] or obi_info["is_illiquid_spread"]:
                    continue

                df = fetch_ohlcv(sym, token, uid)
                if df is None:
                    continue

                # Feature 1: Realtime VWAP Value-Area Bands
                vwap_bands = compute_vwap_bands(df, q)
                if not vwap_bands["above_vwap"]:
                    continue

                atr_val = compute_atr(df, CHANDELIER_ATR_PERIOD)

                factors = compute_micro_alpha30(df, q, nifty_pct=0.0)
                if factors is None:
                    continue

                raw_score = score_alpha30(factors, regime="BULLISH")
                if raw_score > 0:
                    raw_candidates.append({
                        "sym": sym,
                        "raw_score": raw_score,
                        "ltp": ltp,
                        "quote": q,
                        "sec": sec,
                        "ob": obi_info["obi"],
                        "stok": stok,
                        "side": "BUY",
                        "circuit_dist": 0.0,
                        "cs_rank": 0.0,
                        "atr": atr_val,
                        "vwap_info": vwap_bands,
                        "obi_info": obi_info
                    })
        except Exception:
            pass
        time.sleep(0.12)

    if not raw_candidates:
        return []

    ranked = cross_sectional_rank(raw_candidates)
    candidate_tuples = [
        (c["sym"], c["raw_score"], c["ltp"], c["quote"], c["sec"], c["ob"], c["stok"], c["side"], c["circuit_dist"], c["cs_rank"], c.get("atr", 0.0), c.get("vwap_info"), c.get("obi_info"))
        for c in ranked
    ]


    slots = MAX_POSITIONS - len(portfolio.positions)
    top_guarded = filter_by_sector_guard(candidate_tuples, used_sectors, slots)

    if top_guarded:
        tg.send_scan_complete(len(raw_candidates), top_guarded, wave=2)
    return top_guarded

# ── Live Monitoring Loop ─────────────────────────────────────

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "live_state.json"
CMD_FILE = DATA_DIR / "control_cmd.json"

def publish_live_state(portfolio: VirtualPortfolio, regime: str = "UNKNOWN", vix: float = 0.0, status: str = "RUNNING"):
    """Atomically publish live portfolio state for the dedicated Telegram Daemon."""
    try:
        positions_copy = {}
        total_open_pnl = 0.0
        for sym, pos in list(portfolio.positions.items()):
            side = pos.get("side", "BUY")
            entry = pos.get("entry", 0.0)
            ltp = pos.get("last_ltp", entry)
            qty = pos.get("qty", 0)
            pnl = (ltp - entry) * qty if side == "BUY" else (entry - ltp) * qty
            total_open_pnl += pnl
            positions_copy[sym] = {
                "side": side,
                "entry": entry,
                "last_ltp": ltp,
                "qty": qty,
                "pnl": round(pnl, 2),
                "sl": pos.get("sl", 0.0),
                "target": pos.get("target", 0.0),
                "sector": pos.get("sector", "General"),
                "breakeven_locked": pos.get("breakeven_locked", False),
                "atr": pos.get("atr", 0.0),
                "exec_type": pos.get("exec_type", "Standard")
            }
        
        slips = [t.get("slippage_bps", 0) for t in portfolio.tca_history]
        avg_slip = round(float(np.mean(slips)), 2) if slips else 0.0

        state_data = {
            "engine_status": status,
            "timestamp": datetime.now().strftime("%d-%b-%Y %H:%M:%S IST"),
            "timestamp_epoch": time.time(),
            "positions": positions_copy,
            "unrealized_pnl": round(total_open_pnl, 2),
            "realized_pnl": round(portfolio.daily_pnl, 2),
            "total_pnl": round(portfolio.daily_pnl + total_open_pnl, 2),
            "regime": regime,
            "vix": round(vix, 2),
            "pcr": getattr(portfolio, "last_pcr", 1.0),
            "avg_slippage_bps": avg_slip,
            "tca_records_count": len(portfolio.tca_history),
            "paused": tg.bot_paused
        }
        temp_file = STATE_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        print(f"[IPC STATE ERROR] {e}")

def check_control_commands(portfolio: VirtualPortfolio, price_feed: ShoonyaPriceFeed, token_map: dict, regime: str, vix: float = 0.0) -> str:
    """Read IPC commands from tg_daemon.py and execute immediately (<1s)."""
    if not CMD_FILE.exists():
        return "CONTINUE"
    try:
        with open(CMD_FILE, "r", encoding="utf-8") as f:
            cmd_data = json.load(f)
        
        if cmd_data.get("handled"):
            return "CONTINUE"
        
        cmd_name = cmd_data.get("command", "").lower()
        if cmd_name == "squareoff":
            print(f"[IPC CMD] Emergency squareoff requested via Telegram Daemon!")
            n_closed = len(portfolio.positions)
            portfolio.squareoff_all(price_feed, token_map, reason="EMERGENCY /squareoff (TG-DAEMON)")
            cmd_data["handled"] = True
            cmd_data["result"] = f"Closed {n_closed} open position(s)"
            with open(CMD_FILE, "w", encoding="utf-8") as f:
                json.dump(cmd_data, f, indent=2)
            publish_live_state(portfolio, regime, vix, status="SQUAREOFF_DONE")
            return "EXIT"

        elif cmd_name == "pause":
            print("[IPC CMD] Pause requested via Telegram Daemon.")
            tg.bot_paused = True
            cmd_data["handled"] = True
            cmd_data["result"] = "Engine paused"
            with open(CMD_FILE, "w", encoding="utf-8") as f:
                json.dump(cmd_data, f, indent=2)
            publish_live_state(portfolio, regime, vix, status="RUNNING")

        elif cmd_name == "resume":
            print("[IPC CMD] Resume requested via Telegram Daemon.")
            tg.bot_paused = False
            cmd_data["handled"] = True
            cmd_data["result"] = "Engine resumed"
            with open(CMD_FILE, "w", encoding="utf-8") as f:
                json.dump(cmd_data, f, indent=2)
            publish_live_state(portfolio, regime, vix, status="RUNNING")

    except Exception as e:
        print(f"[IPC CMD ERROR] {e}")
    
    return "CONTINUE"

def monitor_loop(portfolio: VirtualPortfolio, sess: dict, initial_stocks: list, price_feed: ShoonyaPriceFeed, regime: str):
    token_map = {}
    for item in initial_stocks:
        sym, score, ltp, q, sec, ob, tok = item[:7]
        side = item[7] if len(item) > 7 else "BUY"
        circuit_dist = item[8] if len(item) > 8 else 0.0
        cs_rank = item[9] if len(item) > 9 else (90.0 if side == "BUY" else 10.0)
        atr = item[10] if len(item) > 10 else 0.0
        vwap_info = item[11] if len(item) > 11 else None
        obi_info = item[12] if len(item) > 12 else None
        token_map[sym] = tok
        if side == "SHORT":
            portfolio.execute_twap_sor(sym, q, sec, "SHORT", cs_rank, circuit_dist=circuit_dist, atr=atr, vwap_info=vwap_info, obi_info=obi_info)
        else:
            portfolio.execute_twap_sor(sym, q, sec, "BUY", cs_rank, atr=atr, vwap_info=vwap_info, obi_info=obi_info)

    wave2_done = False
    short_killed = False

    # Publish initial state
    publish_live_state(portfolio, regime, vix=0.0, status="RUNNING")

    while True:
        now = datetime.now()

        # 1. Emergency Checks (IPC & Shared Flag)
        res = check_control_commands(portfolio, price_feed, token_map, regime, vix=0.0)
        if res == "EXIT":
            publish_live_state(portfolio, regime, vix=0.0, status="STOPPED")
            return

        if tg.emergency_exit:
            portfolio.squareoff_all(price_feed, token_map, "EMERGENCY /squareoff")
            publish_live_state(portfolio, regime, vix=0.0, status="STOPPED")
            return

        # 2. Daily Loss Circuit Breaker
        if portfolio.daily_loss_hit():
            portfolio.squareoff_all(price_feed, token_map, "DAILY LOSS CIRCUIT BREAKER")
            tg.send_daily_loss_halt(portfolio.daily_pnl)
            publish_live_state(portfolio, regime, vix=0.0, status="HALTED")
            return

        # 3. SHORT GUARD 5: 15:05 PM Pre-Squareoff Kill Switch (Auction Freeze Defense)
        if not short_killed and ((now.hour == SHORT_KILL_SWITCH_HOUR and now.minute >= SHORT_KILL_SWITCH_MIN) or now.hour > SHORT_KILL_SWITCH_HOUR):
            short_syms = [s for s, p in portfolio.positions.items() if p.get("side") == "SHORT"]
            if short_syms:
                print(f"[GUARD 5] 15:05 PM Pre-Squareoff Kill Switch triggered for {len(short_syms)} Short position(s)!")
                portfolio.squareoff_all(price_feed, token_map, reason="PRE-SQUAREOFF 15:05 (AUCTION GUARD)", filter_side="SHORT")
            short_killed = True

        # Tier-5 Feature 1: Pre-Close Slippage Minimizer (Almgren-Chriss 15:08 Adaptive TWAP Exit)
        if portfolio.check_preclose_unwind(now.strftime("%H:%M"), price_feed, token_map):
            publish_live_state(portfolio, regime, vix=0.0, status="STOPPED")
            return

        # 4. Auto Square-Off at 15:15 for any remaining positions (Longs)
        if now.hour > 15 or (now.hour == 15 and now.minute >= 15):
            portfolio.squareoff_all(price_feed, token_map, reason="SQUAREOFF 15:15")
            publish_live_state(portfolio, regime, vix=0.0, status="STOPPED")
            return

        # 5. Wave 2 ORB at 09:45 AM (Only if regime is not CRASH)
        if (not wave2_done and now.hour == WAVE2_SCAN_HOUR and
                now.minute >= WAVE2_SCAN_MIN and len(portfolio.positions) < MAX_POSITIONS and regime != "CRASH"):
            wave2_done = True
            if regime == "BEARISH":
                orb_picks = morning_short_scan(sess, portfolio, -0.30)
                for item in orb_picks:
                    sym, score, ltp, q, sec, ob, tok = item[:7]
                    circuit_dist = item[8] if len(item) > 8 else 0.0
                    cs_rank = item[9] if len(item) > 9 else 10.0
                    atr = item[10] if len(item) > 10 else 0.0
                    vwap_info = item[11] if len(item) > 11 else None
                    obi_info = item[12] if len(item) > 12 else None
                    token_map[sym] = tok
                    portfolio.execute_twap_sor(sym, q, sec, "SHORT", cs_rank, circuit_dist=circuit_dist, atr=atr, vwap_info=vwap_info, obi_info=obi_info)
            else:
                orb_picks = wave2_orb_scan(sess, portfolio)
                for item in orb_picks:
                    sym, score, ltp, q, sec, ob, tok = item[:7]
                    circuit_dist = item[8] if len(item) > 8 else 0.0
                    cs_rank = item[9] if len(item) > 9 else 90.0
                    atr = item[10] if len(item) > 10 else 0.0
                    vwap_info = item[11] if len(item) > 11 else None
                    obi_info = item[12] if len(item) > 12 else None
                    token_map[sym] = tok
                    portfolio.execute_twap_sor(sym, q, sec, "BUY", cs_rank, atr=atr, vwap_info=vwap_info, obi_info=obi_info)

        # 6. Monitor positions using Realtime WebSocket Feed & Flash-Drop Guard
        for sym, sym_token in list(token_map.items()):
            if sym not in portfolio.positions:
                continue
            ltp = price_feed.get_price(sym_token)
            if ltp > 0:
                q = get_quote(sym_token, sess["token"], sess["actid"])
                portfolio.check_and_exit(sym, ltp, quote=q)


        # 7. Publish state & Active sub-second sleep cadence
        publish_live_state(portfolio, regime, vix=0.0, status="RUNNING")
        for _ in range(15):
            res = check_control_commands(portfolio, price_feed, token_map, regime, vix=0.0)
            if res == "EXIT":
                publish_live_state(portfolio, regime, vix=0.0, status="STOPPED")
                return
            if tg.emergency_exit:
                portfolio.squareoff_all(price_feed, token_map, "EMERGENCY /squareoff")
                publish_live_state(portfolio, regime, vix=0.0, status="STOPPED")
                return
            time.sleep(1)

# ── MAIN ────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 68)
    print("  CC AlgoTrading — Nifty Smallcap Intraday Engine v3.4 (Qlib Suite)")
    print(f"  Date   : {date.today().strftime('%d-%b-%Y')}")
    print(f"  Mode   : {'REAL MONEY ⚠️' if REAL_MODE else 'VIRTUAL (Paper Trading) ✅'}")
    print(f"  Capital: ₹{CAPITAL:,.0f}")
    print("=" * 68)

    # 1. Authenticate Shoonya
    sess = get_valid_session()

    # 2. Start 2-Way Telegram Listener
    tg.start_command_listener()

    # 3. Pre-Market 09:08 Sentiment
    premarket_sentiment_gauge(sess)

    # 4. Feature 6 + Short Guard 2: Check Nifty 50 Market Regime
    nifty_pct, regime = check_nifty_regime(sess)
    tg.send_regime_mode(regime, nifty_pct)
    print(f"[REGIME] Nifty 50: {nifty_pct:+.2f}% | Regime Mode: {regime}")

    # 4b. Feature 4: Nifty Option Chain PCR Macro Sentiment Radar
    pcr_val, pcr_regime, _ = get_nifty_pcr_sentiment(sess)
    print(f"[PCR RADAR] Nifty 50 PCR: {pcr_val:.2f} | Macro Bias: {pcr_regime}")

    # 5. Feature 3: India VIX Dynamic Calibration
    dyn_target, dyn_sl, vix = get_vix_adaptive_params(sess)

    # 6. Initialize Portfolio with Dynamic VIX SL / Target & PCR
    portfolio = VirtualPortfolio(CAPITAL, dyn_target, dyn_sl)
    portfolio.last_pcr = pcr_val
    tg.set_portfolio(portfolio)

    # 7. Wave 1 Scan: Directional selection based on Market Regime
    if regime in ("BEARISH", "CRASH"):
        print(f"[REGIME ACTION] Bearish Market Detected ({nifty_pct:+.2f}%). Running SHORT Scan with 5 Zero-Risk Guards...")
        initial_picks = morning_short_scan(sess, portfolio, nifty_pct)
    elif regime == "BULLISH":
        print(f"[REGIME ACTION] Bullish Market Detected ({nifty_pct:+.2f}%). Running LONG Scan...")
        initial_picks = morning_scan(sess, portfolio)
    else:  # NEUTRAL
        print(f"[REGIME ACTION] Neutral Market Detected ({nifty_pct:+.2f}%). Running Dual-Scan...")
        long_picks = morning_scan(sess, portfolio)
        short_picks = morning_short_scan(sess, portfolio, nifty_pct)
        # Combine and take highest conviction
        all_candidates = long_picks + short_picks
        all_candidates.sort(key=lambda x: x[1], reverse=True)
        initial_picks = filter_by_sector_guard(all_candidates, set(), MAX_POSITIONS)

    # 8. Feature 8: Initialize Realtime WebSocket Price Feed
    all_tokens = [item[6] for item in initial_picks if len(item) > 6]
    price_feed = ShoonyaPriceFeed(sess)
    if all_tokens:
        price_feed.start_feed(all_tokens)

    # 9. Execute Monitoring Loop (09:15 → 15:15)
    monitor_loop(portfolio, sess, initial_picks, price_feed, regime)

    # 10. Feature 5: EOD Report + Visual Chart Delivery at 15:30
    time.sleep(15)
    tg.send_eod_report(portfolio.closed_trades, portfolio.daily_pnl)
