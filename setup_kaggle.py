import os
import sys
import json
import re
import requests

raw_token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
user = os.environ.get("KAGGLE_USERNAME", "").strip()

print(f"[EXTRACTOR] Raw token repr: {repr(raw_token)}")

# 1. Clean quotes, spaces, brackets
cleaned = raw_token.strip("\"' \t\r\n")

# 2. Check if it is JSON
key = ""
if "{" in cleaned and "}" in cleaned:
    try:
        data = json.loads(cleaned[cleaned.find("{"):cleaned.rfind("}")+1])
        user = data.get("username", user)
        key = data.get("key", "")
        print(f"[EXTRACTOR] Extracted from JSON: user={user}, key_len={len(key)}")
    except Exception as e:
        print("[EXTRACTOR] JSON parse failed:", e)

# 3. Check regex for 32-hex key
hex_match = re.search(r"[a-f0-9]{32}", cleaned, re.IGNORECASE)
if hex_match:
    key = hex_match.group(0).lower()
    print(f"[EXTRACTOR] Found 32-hex key: {key[:4]}...{key[-4:]} (len: {len(key)})")
else:
    print(f"[EXTRACTOR] No 32-hex match found. Cleaned len: {len(cleaned)}")
    key = cleaned

# 4. Check for username in raw_token if present
user_match = re.search(r"\"username\"\s*:\s*\"([^\"]+)\"", raw_token)
if user_match:
    user = user_match.group(1)
    print(f"[EXTRACTOR] Found username in string: {user}")

if not user:
    user = "Akashcc702"

print(f"[EXTRACTOR] Final user: {user}, Final key len: {len(key)}")

# Test authenticated call that STRICTLY requires valid auth:
# GET https://www.kaggle.com/api/v1/users/akashcc (or similar)
auth_url = "https://www.kaggle.com/api/v1/datasets/mine"
test_users = [user, user.lower(), "akashcc", "Akashcc702", "akashcc702"]
test_users = list(dict.fromkeys(test_users))

valid_user = None
for u in test_users:
    r = requests.get(auth_url, auth=(u, key))
    print(f"[MINE TEST] user={u} -> HTTP {r.status_code} | {r.text[:80]}")
    if r.status_code == 200:
        valid_user = u
        print(f"🎉 VALIDATED CREDENTIALS FOR USER: {u}")
        break

if valid_user:
    user = valid_user

kaggle_dir = os.path.expanduser("~/.kaggle")
os.makedirs(kaggle_dir, exist_ok=True)
with open(os.path.join(kaggle_dir, "kaggle.json"), "w") as f:
    json.dump({"username": user, "key": key}, f)
os.chmod(os.path.join(kaggle_dir, "kaggle.json"), 0o600)

meta_path = "kaggle_kernel/kernel-metadata.json"
if os.path.exists(meta_path):
    with open(meta_path, "r") as mf:
        mdata = json.load(mf)
    mdata["id"] = f"{user}/qlib-alpha-gpu-trainer"
    with open(meta_path, "w") as mf:
        json.dump(mdata, mf, indent=2)
