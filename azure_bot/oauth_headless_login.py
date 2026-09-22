# ============================================================
# CC AlgoTrading - Headless Selenium OAuth Login
# oauth_headless_login.py
#
# PROVEN WORKING METHOD (Verified 14-Sep-2026 on Azure VM):
#   - Shoonya OAuth page has 3 visible inputs: [0]=UserID, [1]=PWD, [2]=OTP
#   - All 3 filled in ONE step (not 2-step), then LOGIN clicked once
#   - FN227254_U2 with 104.211.100.195 whitelisted → stat=Ok ✅
#
# Runs at 08:45 AM IST daily via cron. Zero manual touch.
# ============================================================
import os, sys, time, json, hashlib, configparser
import urllib.parse, pyotp, requests
from pathlib import Path

BASE_DIR     = Path(__file__).parent
CONFIG_FILE  = BASE_DIR / "config.ini"
SESSION_FILE = BASE_DIR / ".shoonya_session.json"

# ── Load Config ─────────────────────────────────────────────
config = configparser.ConfigParser()
config.read_string(open(CONFIG_FILE, "r", encoding="utf-8-sig").read())

USER_ID     = config.get("SHOONYA", "USER_ID")
PASSWORD    = config.get("SHOONYA", "PASSWORD")
VENDOR_CODE = config.get("SHOONYA", "VENDOR_CODE")
API_SECRET  = config.get("SHOONYA", "API_SECRET")
TOTP_SECRET = config.get("SHOONYA", "TOTP_SECRET")
BOT_TOKEN   = config.get("TELEGRAM", "BOT_TOKEN", fallback="")
CHAT_ID     = config.get("TELEGRAM", "CHAT_ID",   fallback="")

# ── Telegram Alert ───────────────────────────────────────────
def tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=8
        )
    except Exception as e:
        print(f"[WARN] Telegram error: {e}")

# ── Check Existing Session ───────────────────────────────────
def session_valid():
    if not SESSION_FILE.exists():
        print("[INFO] No session file found.")
        return False
    try:
        sess  = json.loads(SESSION_FILE.read_text())
        token = sess.get("token", "")
        uid   = sess.get("actid", USER_ID)
        saved_date = sess.get("date", "")
        today = time.strftime("%Y-%m-%d")

        # Session must be from today
        if saved_date != today:
            print(f"[INFO] Session is from {saved_date}, today is {today}. Refreshing...")
            return False

        # Verify session is still alive via GetQuotes (BHEL NSE token 438)
        r = requests.post(
            "https://api.shoonya.com/NorenWClientAPI/GetQuotes",
            data=f'jData={json.dumps({"ordersource":"API","uid":uid,"exch":"NSE","token":"438"})}&jKey={token}',
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=8
        ).json()

        if r.get("stat") == "Ok":
            ltp = r.get("lp", "?")
            print(f"[OK] Session still VALID. BHEL LTP: Rs.{ltp}")
            return True
        else:
            print(f"[INFO] Session invalid: {r.get('emsg','?')}")
            return False
    except Exception as e:
        print(f"[INFO] Session check error: {e}")
        return False

# ── Headless Selenium OAuth ──────────────────────────────────
def headless_login():
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager

    print(f"[INFO] Starting Headless Chrome OAuth for {VENDOR_CODE}...")

    # Pre-clean any leftover zombie chrome processes
    if sys.platform != "win32":
        os.system("pkill -9 -f chrome 2>/dev/null || true")
        os.system("pkill -9 -f chromedriver 2>/dev/null || true")

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1366,768")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )

    auth_code = None
    try:
        # ── Step 1: Open Shoonya OAuth page ─────────────────
        auth_url = (
            f"https://api.shoonya.com/OAuthlogin/authorize/oauth"
            f"?client_id={VENDOR_CODE}"
        )
        driver.get(auth_url)
        print(f"[INFO] Opened OAuth URL for client_id={VENDOR_CODE}")

        # Wait for React SPA to render login form (proven: 8s sufficient)
        time.sleep(8)

        # ── Step 2: Find VISIBLE inputs only ─────────────────
        all_inputs = driver.find_elements(By.TAG_NAME, "input")
        visible = [
            i for i in all_inputs
            if i.is_displayed() and i.is_enabled()
        ]
        print(f"[INFO] Visible inputs found: {len(visible)}")

        if len(visible) < 3:
            driver.save_screenshot("/tmp/shoonya_debug.png")
            raise Exception(
                f"Expected 3 visible inputs, got {len(visible)}. "
                f"Screenshot: /tmp/shoonya_debug.png"
            )

        # ── Step 3: Fill User ID, Password, OTP in ONE step ──
        # PROVEN structure:
        #   visible[0] = USER ID  (type=text)
        #   visible[1] = PASSWORD (type=password)
        #   visible[2] = OTP/TOTP (type=password, labeled as OTP)
        totp_code = pyotp.TOTP(TOTP_SECRET).now()

        visible[0].clear()
        visible[0].send_keys(USER_ID)

        visible[1].clear()
        visible[1].send_keys(PASSWORD)

        visible[2].clear()
        visible[2].send_keys(totp_code)

        print(f"[INFO] Entered: UserID={USER_ID} | Password=*** | TOTP={totp_code}")

        # ── Step 4: Click LOGIN button ────────────────────────
        btns = driver.find_elements(By.TAG_NAME, "button")
        visible_btns = [
            b for b in btns
            if b.is_displayed() and b.is_enabled()
        ]
        clicked = False
        for btn in visible_btns:
            if any(x in btn.text.upper() for x in ["LOGIN", "SIGN IN", "SUBMIT"]):
                btn.click()
                print(f"[INFO] Clicked button: '{btn.text.strip()}'")
                clicked = True
                break

        if not clicked:
            raise Exception(f"LOGIN button not found. Buttons: {[b.text for b in visible_btns]}")

        # Wait for redirect with auth code
        time.sleep(6)

        # ── Step 5: Capture Auth Code from redirect URL ───────
        current_url = driver.current_url
        print(f"[INFO] Redirect URL: {current_url[:100]}...")

        parsed = urllib.parse.urlparse(current_url)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" not in params:
            # Save debug screenshot
            driver.save_screenshot("/tmp/shoonya_debug.png")
            body_text = driver.find_element(By.TAG_NAME, "body").text[:200]
            raise Exception(
                f"Auth code NOT in redirect URL.\n"
                f"URL: {current_url}\n"
                f"Body: {body_text}\n"
                f"Screenshot: /tmp/shoonya_debug.png"
            )

        auth_code = params["code"][0]
        print(f"[OK] Auth Code captured: {auth_code[:10]}...")

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        if sys.platform != "win32":
            os.system("pkill -9 -f chrome 2>/dev/null || true")
            os.system("pkill -9 -f chromedriver 2>/dev/null || true")
        print("[INFO] Chrome closed & all zombie processes reaped. RAM 100% freed.")

    if not auth_code:
        raise Exception("Auth code is None after browser quit")

    # ── Step 6: Exchange Auth Code → Session Token ────────────
    print("[INFO] Calling GenAcsTok to get session token...")
    checksum = hashlib.sha256(
        f"{VENDOR_CODE}{API_SECRET}{auth_code}".encode("utf-8")
    ).hexdigest()

    payload = json.dumps({"code": auth_code, "checksum": checksum})
    resp = requests.post(
        "https://api.shoonya.com/NorenWClientAPI/GenAcsTok",
        data="jData=" + payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=12
    ).json()

    if resp.get("stat") == "Ok" and "susertoken" in resp:
        token = resp["susertoken"]
        sess  = {
            "token": token,
            "actid": USER_ID,
            "date":  time.strftime("%Y-%m-%d")
        }
        SESSION_FILE.write_text(json.dumps(sess, indent=2))
        print(f"[OK] SESSION TOKEN SAVED! Token: {token[:15]}...")

        tg(
            f"✅ <b>Shoonya Headless OAuth — LOGIN SUCCESS</b>\n"
            f"🔑 Key: <code>{VENDOR_CODE}</code>\n"
            f"📅 {time.strftime('%d-%b-%Y %H:%M IST')}\n"
            f"📍 Azure VM | 104.211.100.195\n"
            f"💰 Mode: <b>VIRTUAL (Paper Trading)</b>"
        )
        return token

    else:
        reason = resp.get("emsg", str(resp))
        print(f"[ERROR] GenAcsTok FAILED: {reason}")
        raise Exception(f"GenAcsTok failed: {reason}")


def resilient_headless_login(max_retries: int = 3, backoffs: list = None) -> str:
    """
    Resilient OAuth Login Engine with 3-Attempt Exponential Backoff.
    Protects against transient HTTP 502/504 Bad Gateway or Selenium timeout glitches.
    Guarantees zombie process reaping between attempts.
    """
    if backoffs is None:
        backoffs = [10, 25]
    
    last_err = None
    for attempt in range(1, max_retries + 1):
        print(f"\n[OAUTH ATTEMPT {attempt}/{max_retries}] Initiating Headless OAuth Login...")
        try:
            tok = headless_login()
            if tok:
                print(f"✅ [OAUTH SUCCESS] Attempt {attempt} succeeded!")
                return tok
        except Exception as e:
            last_err = e
            print(f"⚠️ [OAUTH WARN] Attempt {attempt} failed: {e}")
            if sys.platform != "win32":
                os.system("pkill -9 -f chrome 2>/dev/null || true")
                os.system("pkill -9 -f chromedriver 2>/dev/null || true")
            
            if attempt < max_retries:
                wait_sec = backoffs[attempt - 1] if (attempt - 1) < len(backoffs) else 30
                print(f"⏳ Waiting {wait_sec}s before retry attempt {attempt + 1}...")
                time.sleep(wait_sec)
    
    # If all attempts exhausted
    print(f"❌ [OAUTH FATAL] All {max_retries} login attempts exhausted. Error: {last_err}")
    tg(
        f"❌ <b>Shoonya Login FAILED (All {max_retries} Retries Exhausted)</b>\n"
        f"⚠️ {last_err}\n"
        f"🔑 Key: <code>{VENDOR_CODE}</code>\n"
        f"📍 Azure VM | 104.211.100.195"
    )
    sys.exit(1)


# ── MAIN ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  CC AlgoTrading — Headless Selenium OAuth Login v3.6")
    print("=" * 60)

    if session_valid():
        print("[OK] Today's session is valid. Nothing to do.")
        sys.exit(0)

    print("[INFO] No valid session. Starting Resilient OAuth login (3-attempt backoff)...")
    token = resilient_headless_login()
    print(f"[OK] Authentication complete. Token: {token[:15]}...")
    print("[OK] Bot ready.")
