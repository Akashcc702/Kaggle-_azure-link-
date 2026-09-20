# ============================================================
# CC AlgoTrading - Telegram Alerts Module
# telegram_alerts.py
# v3.0 — 2-Way Interactive Bot + Visual Chart Delivery
# ============================================================
import requests
import configparser
import threading
from pathlib import Path
from datetime import datetime

CONFIG_FILE = Path(__file__).parent / "config.ini"
config = configparser.ConfigParser()
config.read_string(open(CONFIG_FILE, "r", encoding="utf-8-sig").read())

BOT_TOKEN = config.get("TELEGRAM", "BOT_TOKEN", fallback="")
CHAT_ID   = config.get("TELEGRAM", "CHAT_ID",   fallback="")

BASE_URL      = f"https://api.telegram.org/bot{BOT_TOKEN}"
_last_update  = 0

# ── Shared Control Flags ─────────────────────────────────────
emergency_exit  = False   # /squareoff → exit all positions now
bot_paused      = False   # /pause     → stop new buys
_portfolio_ref  = None    # Set by engine so /status can read it

def _send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to Telegram. Returns True if successful."""
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"{BASE_URL}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=8
        )
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

def _send_photo(photo_path: str, caption: str = "") -> bool:
    """Send an image to Telegram via sendPhoto API."""
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        p = Path(photo_path)
        if not p.exists():
            return False
        with open(p, "rb") as f:
            resp = requests.post(
                f"{BASE_URL}/sendPhoto",
                data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                files={"photo": f},
                timeout=25
            )
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"[TELEGRAM PHOTO ERROR] {e}")
        return False

def now_ist() -> str:
    return datetime.now().strftime("%d-%b-%Y %H:%M:%S IST")

# ── 2-Way Command Listener (runs in background thread) ───────

def _handle_command(text: str):
    """Process incoming Telegram commands."""
    global emergency_exit, bot_paused
    text = text.strip().lower()

    if text == "/squareoff":
        emergency_exit = True
        _send(
            f"🚨 <b>EMERGENCY SQUAREOFF TRIGGERED!</b>\n"
            f"⚡ All positions will be closed NOW.\n"
            f"🕐 {now_ist()}"
        )
        print("[TELEGRAM CMD] /squareoff received — emergency exit activated!")

    elif text == "/status":
        if _portfolio_ref is None:
            _send("ℹ️ Bot not yet running or no active session.")
            return
        port = _portfolio_ref
        if not port.positions:
            _send(
                f"📊 <b>Bot Status</b>\n"
                f"📌 Open Positions: None\n"
                f"💰 Today's Realized P&L: ₹{port.daily_pnl:+.2f}\n"
                f"⏸️ Paused: {'Yes' if bot_paused else 'No'}\n"
                f"🕐 {now_ist()}"
            )
        else:
            lines = []
            total_pnl = 0.0
            for sym, pos in port.positions.items():
                entry = pos["entry"]
                ltp = pos.get("last_ltp", entry)
                side = pos.get("side", "BUY")
                if side == "SHORT":
                    pnl = (entry - ltp) * pos["qty"]
                    sl_dist = ((pos["sl"] - entry) / entry) * 100
                    tgt_dist = ((pos["target"] - entry) / entry) * 100
                    tag = "📉 SHORT"
                else:
                    pnl = (ltp - entry) * pos["qty"]
                    sl_dist = ((pos["sl"] - entry) / entry) * 100
                    tgt_dist = ((pos["target"] - entry) / entry) * 100
                    tag = "📈 LONG"
                total_pnl += pnl
                sec = pos.get("sector", "General")
                trail_tag = " 🔒" if pos.get("breakeven_locked") else ""
                lines.append(
                    f"  {tag} <code>{sym}</code> ({sec}){trail_tag}\n"
                    f"     Entry:₹{entry:.2f} | LTP:₹{ltp:.2f} | P&L:₹{pnl:+.2f}\n"
                    f"     SL:₹{pos['sl']:.2f} ({sl_dist:+.1f}%) | TGT:₹{pos['target']:.2f} ({tgt_dist:+.1f}%)"
                )
            emoji = "🟢" if total_pnl >= 0 else "🔴"
            _send(
                f"📊 <b>Live Status — {len(port.positions)} Position(s)</b>\n\n"
                + "\n".join(lines) + "\n\n"
                f"{emoji} <b>Open P&L: ₹{total_pnl:+.2f}</b>\n"
                f"💰 Realized: ₹{port.daily_pnl:+.2f}\n"
                f"⏸️ Paused: {'Yes' if bot_paused else 'No'}\n"
                f"🕐 {now_ist()}"
            )

    elif text == "/pause":
        bot_paused = True
        _send(f"⏸️ <b>Bot PAUSED</b> — No new buys until /resume\n🕐 {now_ist()}")
        print("[TELEGRAM CMD] /pause received.")

    elif text == "/health":
        try:
            import subprocess
            uptime = subprocess.check_output("uptime", shell=True, text=True).strip()
            free = subprocess.check_output("free -m | grep Mem:", shell=True, text=True).split()
            df = subprocess.check_output("df -h / | tail -n 1", shell=True, text=True).split()
            ram_used = free[2]; ram_total = free[1]
            disk_free = df[3]
            _send(
                f"🟢 <b>Live System Health</b>\n"
                f"💻 Load: <code>{uptime.split('load average:')[1].strip()}</code>\n"
                f"🧠 RAM: <b>{ram_used}MB / {ram_total}MB</b>\n"
                f"💾 Disk Free: <b>{disk_free}</b>\n"
                f"🕐 {now_ist()}"
            )
        except Exception as e:
            _send(f"⚠️ Health check error: {e}")

    elif text == "/rollback":
        try:
            import subprocess
            res = subprocess.run(["bash", str(Path(__file__).parent / "rollback.sh")], capture_output=True, text=True)
            if res.returncode == 0:
                _send(f"🛡️ <b>System ROLLBACK SUCCESS!</b>\nAll files reverted to verified Gold v3.0 in 0.5s.\n🕐 {now_ist()}")
            else:
                _send(f"⚠️ Rollback error: {res.stderr[:200]}")
        except Exception as e:
            _send(f"⚠️ Rollback error: {e}")

    elif text == "/help":
        _send(
            f"🤖 <b>CC AlgoTrading Bot Commands (v3.0 Institutional)</b>\n\n"
            f"/status    — Live positions, sectors & P&L\n"
            f"/squareoff — Emergency close ALL positions\n"
            f"/pause     — Stop new buys (keep existing)\n"
            f"/resume    — Resume new buys\n"
            f"/health    — Live CPU, RAM & Disk metrics\n"
            f"/rollback  — Instant revert to Gold backup\n"
            f"/help      — Show this menu"
        )

def _poll_loop():
    """Background thread: long-poll Telegram for commands every 5 seconds."""
    global _last_update
    if not BOT_TOKEN:
        return
    while True:
        try:
            resp = requests.get(
                f"{BASE_URL}/getUpdates",
                params={"offset": _last_update + 1, "timeout": 30},
                timeout=35
            ).json()
            for upd in resp.get("result", []):
                _last_update = upd["update_id"]
                msg = upd.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id == str(CHAT_ID):
                    cmd_text = msg.get("text", "")
                    if cmd_text.startswith("/"):
                        _handle_command(cmd_text)
        except Exception as e:
            pass

def is_daemon_running() -> bool:
    """Check if dedicated tg_daemon.py service is running on the system."""
    try:
        import subprocess
        res = subprocess.run("pgrep -f tg_daemon.py", shell=True, capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False

def start_command_listener():
    """Start the background Telegram command polling thread only if tg_daemon is not active."""
    if is_daemon_running():
        print("[TELEGRAM] Dedicated tg_daemon.service is active. Engine delegating command polling to daemon.")
        return
    t = threading.Thread(target=_poll_loop, daemon=True, name="TG-Poll")
    t.start()

def set_portfolio(portfolio):
    """Let the engine register the portfolio for /status reads."""
    global _portfolio_ref
    _portfolio_ref = portfolio

# ── Public Alert Functions ──────────────────────────────────

def send_startup():
    _send(
        f"🚀 <b>CC AlgoTrading Bot v3.2 (Qlib Powered) — STARTED</b>\n"
        f"🕐 {now_ist()}\n"
        f"📍 Azure VM | Static IP: 104.211.100.195\n"
        f"🧠 Quant: <b>Qlib Micro-Alpha30 | CS-Rank (0-100%) | Rolling IC | TWAP Slicer</b>\n"
        f"🛡️ Risk: <b>5 Zero-Risk Guards | VIX Adaptive | Sector Guard | L2 Order Book</b>\n"
        f"💰 Mode: <b>VIRTUAL (Paper Trading)</b>\n\n"
        f"<i>Commands: /status /squareoff /pause /resume /health /rollback /help</i>"
    )

def send_login_success(token_preview: str):
    _send(
        f"✅ <b>Shoonya Login SUCCESS</b>\n"
        f"🔑 Token: <code>{token_preview}...</code>\n"
        f"🕐 {now_ist()}"
    )

def send_login_failed(reason: str):
    _send(
        f"❌ <b>Shoonya Login FAILED</b>\n"
        f"⚠️ Reason: <code>{reason}</code>\n"
        f"🕐 {now_ist()}\n"
        f"🛑 Bot halted. Please check credentials!"
    )

def send_premarket_briefing(nifty_gap: float, sentiment: str):
    emoji = "🟢" if "Bullish" in sentiment else "🔴" if "Bearish" in sentiment else "⚪"
    _send(
        f"🌅 <b>Pre-Market 09:08 AM Briefing</b>\n"
        f"{emoji} Sentiment: <b>{sentiment}</b>\n"
        f"📊 Nifty Indicative Gap: <b>{nifty_gap:+.2f}%</b>\n"
        f"🕐 {now_ist()}"
    )

def send_market_regime_alert(nifty_pct: float):
    _send(
        f"🛑 <b>MARKET REGIME BEARISH HALT</b>\n"
        f"📉 Nifty 50 is down: <b>{nifty_pct:.2f}%</b> (Below -0.70% crash threshold)\n"
        f"🛡️ <b>Action:</b> Long entries blocked for capital protection.\n"
        f"🕐 {now_ist()}"
    )

def send_vix_alert(vix: float, tgt: float, sl: float):
    _send(
        f"⚡ <b>India VIX Volatility Calibrated</b>\n"
        f"📊 India VIX: <b>{vix:.2f}</b>\n"
        f"🎯 Dynamic Target: <b>+{tgt:.1f}%</b> | 🛑 Dynamic SL: <b>-{sl:.1f}%</b>\n"
        f"🕐 {now_ist()}"
    )

def send_scan_complete(n_candidates: int, selected: list, wave: int = 1):
    lines = []
    for i, item in enumerate(selected):
        if isinstance(item, dict):
            sym = item.get("sym", "")
            sec = item.get("sec", "")
            ltp = item.get("ltp", 0.0)
            cs = item.get("cs_rank", 0.0)
            lines.append(f"  {i+1}. <code>{sym}</code> ({sec}) | CS-Rank: <b>{cs:.1f}%</b> | ₹{ltp:.2f}")
        elif len(item) >= 10:
            sym, score, ltp, q, sec, ob, stok, side, cdist, cs = item[:10]
            lines.append(f"  {i+1}. <code>{sym}</code> ({sec}) | CS-Rank: <b>{cs:.1f}%</b> | ₹{ltp:.2f}")
        elif len(item) >= 5:
            sym = item[0]
            ltp = item[2]
            sec = item[4] if len(item) > 4 else "Others"
            lines.append(f"  {i+1}. <code>{sym}</code> ({sec}) | ₹{ltp:.2f}")
        else:
            lines.append(f"  {i+1}. <code>{item[0]}</code>")

    stocks_text = "\n".join(lines) if lines else "  No stocks qualified."
    wave_tag = f" (Wave {wave} ORB)" if wave > 1 else " (Wave 1 Alpha)"
    _send(
        f"🔍 <b>Qlib Quant Scan Complete{wave_tag}</b>\n"
        f"📊 Qualified: {n_candidates} → Top {len(selected)} Selected (CS-Rank & Sector Guarded):\n"
        f"{stocks_text}\n"
        f"🕐 {now_ist()}"
    )

def send_virtual_buy(symbol: str, ltp: float, qty: int, sl: float, target: float, sector: str, sor_saving: float = 0.0, cs_rank: float = 0.0):
    sor_text = f"\n💡 Smart SOR Saving: ₹{sor_saving:.2f}/sh (Slippage Prevented)" if sor_saving > 0 else ""
    cs_text = f"\n🌟 Qlib CS-Rank : <b>{cs_rank:.1f}%</b> (Top Decile Alpha)" if cs_rank > 0 else ""
    _send(
        f"📈 <b>[SMART BUY]</b> <code>{symbol}</code>\n"
        f"🏢 Sector    : {sector}\n"
        f"💵 Entry LTP : ₹{ltp:.2f}{sor_text}{cs_text}\n"
        f"📦 Qty       : {qty} shares\n"
        f"🛑 Stop Loss : ₹{sl:.2f} (-{abs((sl-ltp)/ltp*100):.1f}%)\n"
        f"🎯 Target    : ₹{target:.2f} (+{abs((target-ltp)/ltp*100):.1f}%)\n"
        f"🕐 {now_ist()}"
    )

def send_virtual_sell(symbol: str, entry: float, exit_price: float, qty: int, reason: str):
    pnl = (exit_price - entry) * qty
    pct = (exit_price - entry) / entry * 100
    emoji = "✅" if pnl >= 0 else "❌"
    _send(
        f"{emoji} <b>[VIRTUAL LONG EXIT]</b> <code>{symbol}</code>\n"
        f"📌 Entry  : ₹{entry:.2f}\n"
        f"📌 Exit   : ₹{exit_price:.2f}\n"
        f"📦 Qty    : {qty}\n"
        f"💰 P&L    : ₹{pnl:+.2f} ({pct:+.2f}%)\n"
        f"📝 Reason : {reason}\n"
        f"🕐 {now_ist()}"
    )

def send_virtual_short(symbol: str, ltp: float, qty: int, sl: float, target: float, sector: str, sor_saving: float = 0.0, circuit_dist: float = 0.0, cs_rank: float = 0.0):
    sor_text = f"\n💡 Smart SOR Saving: ₹{sor_saving:.2f}/sh (Slippage Prevented)" if sor_saving > 0 else ""
    circuit_text = f"\n🛡️ Upper Circuit Buffer: {circuit_dist:.1f}% (Zero-Auction Guard)" if circuit_dist > 0 else ""
    cs_text = f"\n🔻 Qlib CS-Rank : <b>{cs_rank:.1f}%</b> (Bottom Decile Relative Weakness)" if cs_rank > 0 else ""
    _send(
        f"🔻 <b>[SMART SHORT SELL]</b> <code>{symbol}</code>\n"
        f"🏢 Sector    : {sector}\n"
        f"💵 Entry LTP : ₹{ltp:.2f}{sor_text}{circuit_text}{cs_text}\n"
        f"📦 Qty       : {qty} shares\n"
        f"🛑 Stop Loss : ₹{sl:.2f} (+{abs((sl-ltp)/ltp*100):.1f}% above entry)\n"
        f"🎯 Target    : ₹{target:.2f} (-{abs((target-ltp)/ltp*100):.1f}% below entry)\n"
        f"🕐 {now_ist()}"
    )

def send_virtual_cover(symbol: str, entry: float, exit_price: float, qty: int, reason: str):
    pnl = (entry - exit_price) * qty
    pct = (entry - exit_price) / entry * 100
    emoji = "✅" if pnl >= 0 else "❌"
    _send(
        f"{emoji} <b>[VIRTUAL SHORT COVER]</b> <code>{symbol}</code>\n"
        f"📌 Short Entry : ₹{entry:.2f}\n"
        f"📌 Buy Cover   : ₹{exit_price:.2f}\n"
        f"📦 Qty         : {qty}\n"
        f"💰 P&L         : ₹{pnl:+.2f} ({pct:+.2f}%)\n"
        f"📝 Reason      : {reason}\n"
        f"🕐 {now_ist()}"
    )

def send_flash_vacuum_alert(symbol: str, ltp: float, vel_pct: float, dt: float, side: str = "BUY"):
    action = "EMERGENCY MARKET SELL (IOC)" if side == "BUY" else "EMERGENCY MARKET COVER (IOC)"
    _send(
        f"🚨 <b>CIRCUIT FREEZE VACUUM GUARD TRIGGERED!</b>\n"
        f"⚡ <code>{symbol}</code> ({side}) moved <b>{vel_pct:+.2f}%</b> in {dt:.1f}s!\n"
        f"📉 Order Book Liquidity Collapse / Vacuum Detected.\n"
        f"🛡️ <b>Action:</b> {action} executed in &lt;100ms.\n"
        f"💵 Execution LTP: ₹{ltp:.2f}\n"
        f"🕐 {now_ist()}"
    )

def send_regime_mode(regime: str, nifty_pct: float):
    if regime == "BULLISH":
        emoji = "🟢"
        action = "Engine configured for <b>LONG TRADES ONLY</b>."
    elif regime == "BEARISH":
        emoji = "🔴"
        action = "Engine configured for <b>SHORT TRADES ONLY</b> (5 Zero-Risk Guards Active)."
    elif regime == "CRASH":
        emoji = "🚨"
        action = "CRASH REGIME! Long entries blocked. Highly-selective Shorts only."
    else:
        emoji = "⚪"
        action = "Engine configured for <b>BI-DIRECTIONAL</b> (Long + Short with 5 Guards)."

    _send(
        f"{emoji} <b>MARKET REGIME: {regime}</b>\n"
        f"📊 Nifty 50 Change: <b>{nifty_pct:+.2f}%</b>\n"
        f"🛡️ Mode: {action}\n"
        f"🕐 {now_ist()}"
    )

def send_trail_lock(symbol: str, old_sl: float, new_sl: float, ltp: float):
    _send(
        f"🔒 <b>Breakeven LOCKED</b> <code>{symbol}</code>\n"
        f"💵 LTP: ₹{ltp:.2f}\n"
        f"🛑 SL: ₹{old_sl:.2f} → ₹{new_sl:.2f} (Profit Protected!)\n"
        f"🕐 {now_ist()}"
    )

def send_trail_update(symbol: str, old_sl: float, new_sl: float, ltp: float):
    _send(
        f"📈 <b>Trailing SL Updated</b> <code>{symbol}</code>\n"
        f"💵 LTP: ₹{ltp:.2f}\n"
        f"🛑 SL: ₹{old_sl:.2f} → ₹{new_sl:.2f}\n"
        f"🕐 {now_ist()}"
    )

def send_daily_loss_halt(total_loss: float):
    _send(
        f"🚨 <b>MAX DAILY LOSS HIT — BOT HALTED!</b>\n"
        f"🔴 Total Loss Today: ₹{total_loss:+.2f}\n"
        f"🛑 All positions squared off. Trading stopped for today.\n"
        f"🕐 {now_ist()}"
    )

def send_eod_chart(trades: list, total_pnl: float):
    """Generate and send visual P&L and Trade Performance chart."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), facecolor="#131722")
        fig.suptitle(f"CC AlgoTrading Intraday Performance — {datetime.now().strftime('%d-%b-%Y')}",
                     color="#ffffff", fontsize=14, fontweight="bold")

        if trades:
            symbols = [f"{t['symbol']}\n({t['reason'][:8]})" for t in trades]
            pnls = [t["pnl"] for t in trades]
            colors = ["#00e676" if p >= 0 else "#ff5252" for p in pnls]

            # Top: Trade P&Ls
            bars = ax1.bar(symbols, pnls, color=colors, width=0.45)
            ax1.axhline(0, color="#ffffff", linestyle="--", linewidth=0.8, alpha=0.5)
            ax1.set_facecolor("#1e222d")
            ax1.tick_params(colors="#ffffff")
            ax1.set_title("Trade-by-Trade Realized P&L (₹)", color="#b2b5be", fontsize=11)
            for bar in bars:
                h = bar.get_height()
                ax1.annotate(f"₹{h:+.1f}",
                             xy=(bar.get_x() + bar.get_width() / 2, h),
                             xytext=(0, 3 if h >= 0 else -12),
                             textcoords="offset points",
                             ha="center", va="bottom" if h >= 0 else "top",
                             color="#ffffff", fontsize=9, fontweight="bold")

            # Bottom: Cumulative Equity Curve
            cum_pnl = [0.0]
            for p in pnls:
                cum_pnl.append(cum_pnl[-1] + p)
            ax2.plot(range(len(cum_pnl)), cum_pnl, color="#29b6f6", marker="o", linewidth=2.2)
            ax2.fill_between(range(len(cum_pnl)), cum_pnl, 0, color="#29b6f6", alpha=0.15)
            ax2.set_facecolor("#1e222d")
            ax2.tick_params(colors="#ffffff")
            ax2.set_title(f"Cumulative Intraday Equity Curve (Net P&L: ₹{total_pnl:+.2f})", color="#b2b5be", fontsize=11)
            ax2.set_xlabel("Trade Number", color="#ffffff")
        else:
            ax1.set_facecolor("#1e222d")
            ax1.text(0.5, 0.5, "No Trades Executed Today (Capital 100% Protected)",
                     color="#ffffff", ha="center", va="center", fontsize=12)
            ax2.set_facecolor("#1e222d")

        plt.tight_layout()
        chart_path = "/tmp/eod_chart.png"
        plt.savefig(chart_path, dpi=120, facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close()

        caption = (
            f"📊 <b>EOD Visual Performance Report</b>\n"
            f"{'🟢' if total_pnl >= 0 else '🔴'} Total P&L: <b>₹{total_pnl:+.2f}</b>\n"
            f"🕐 {now_ist()}"
        )
        _send_photo(chart_path, caption)
    except Exception as e:
        print(f"[CHART ERROR] {e}")

def send_eod_report(trades: list, total_pnl: float):
    # Sends textual summary, then pushes the visual chart!
    if not trades:
        body = "  No trades executed today (Capital 100% Protected)."
    else:
        lines = []
        for t in trades:
            sym    = t.get("symbol", "?")
            pnl    = t.get("pnl", 0)
            reason = t.get("reason", "")
            emoji  = "✅" if pnl >= 0 else "❌"
            lines.append(f"  {emoji} {sym}: ₹{pnl:+.2f} ({reason})")
        body = "\n".join(lines)

    total_emoji = "🟢" if total_pnl >= 0 else "🔴"
    _send(
        f"📊 <b>EOD Report — {datetime.now().strftime('%d-%b-%Y')}</b>\n\n"
        f"{body}\n\n"
        f"{total_emoji} <b>Total Virtual P&L: ₹{total_pnl:+.2f}</b>\n"
        f"🕐 {now_ist()}\n"
        f"💰 Mode: VIRTUAL (Paper Trading)"
    )
    # Also deliver visual chart!
    send_eod_chart(trades, total_pnl)

def send_squareoff_alert(n_positions: int):
    _send(
        f"🔔 <b>Auto Square-Off Triggered</b>\n"
        f"📌 {n_positions} open position(s) squared off at 03:15 PM\n"
        f"🕐 {now_ist()}"
    )

def send_info(msg: str):
    _send(f"ℹ️ {msg}\n🕐 {now_ist()}")

def send_scale_out_alert(symbol: str, ltp: float, scale_qty: int, pnl: float, rem_qty: int, new_sl: float, side: str = "BUY"):
    icon = "📈 LONG" if side == "BUY" else "📉 SHORT"
    _send(
        f"🎯 <b>Partial Scale-Out & Risk-Free Runner Active!</b>\n"
        f"{icon} <code>{symbol}</code> reached Target-1 (+1.2%) at ₹{ltp:.2f}\n"
        f"💰 <b>Locked 50% Profit: ₹{pnl:+.2f}</b> (Exited {scale_qty} Qty)\n"
        f"🔒 <b>Stop-Loss moved to Breakeven: ₹{new_sl:.2f}</b>\n"
        f"🏃 Remaining {rem_qty} Qty running as 100% Risk-Free Runner to final target!\n"
        f"🕐 {now_ist()}"
    )

def send_pyramid_scale_in(symbol: str, entry: float, added_qty: int, total_qty: int, ltp: float, sl: float, side: str = "BUY"):
    icon = "🚀 SUPER-RUNNER PYRAMID (LONG)" if side == "BUY" else "🔻 SUPER-RUNNER PYRAMID (SHORT)"
    _send(
        f"⚡ <b>{icon}</b> <code>{symbol}</code>\n"
        f"📈 Momentum Surge: Gain >= +0.80% reached at ₹{ltp:.2f}\n"
        f"🔒 <b>Initial SL Locked at Cost: ₹{sl:.2f} (Zero Rupee Risk)</b>\n"
        f"➕ <b>Scaled In +25% Size: +{added_qty} shares</b> (Total: {total_qty})\n"
        f"🛡️ <b>Capital Risk on Tranche: ₹0.00</b> (100% Risk-Free Expansion)\n"
        f"🕐 {now_ist()}"
    )

