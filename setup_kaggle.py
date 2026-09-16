import os
import sys
import json
import re
import requests

raw_token = os.environ.get("AKASHCC", "").strip() or os.environ.get("KAGGLE_API_TOKEN", "").strip()
user = os.environ.get("KAGGLE_USERNAME", "").strip() or "akashcc"

print(f"[AUTH RESOLVER] Token Length: {len(raw_token)}")
print(f"[AUTH RESOLVER] Token Prefix: {raw_token[:6] if len(raw_token) >= 6 else 'N/A'}")

# 1. Try Bearer auth test
bearer_headers = {"Authorization": f"Bearer {raw_token}"}
r_bearer = requests.get("https://www.kaggle.com/api/v1/datasets/list?mine=true", headers=bearer_headers)
print(f"[BEARER TEST] HTTP {r_bearer.status_code} | {r_bearer.text[:80]}")

# 2. Extract 32-hex key if present
hex_match = re.search(r"[a-f0-9]{32}", raw_token, re.IGNORECASE)
key_32 = hex_match.group(0).lower() if hex_match else raw_token

# Try Basic Auth test with candidate users
candidate_users = [user, "akashcc", "Akashcc", "Akashcc702", "akashcc702"]
candidate_users = list(dict.fromkeys(candidate_users))

working_user = None
working_key = None

for u in candidate_users:
    r_basic = requests.get("https://www.kaggle.com/api/v1/datasets/list?mine=true", auth=(u, key_32))
    print(f"[BASIC TEST] user='{u}', key_32={key_32[:4]}... -> HTTP {r_basic.status_code} | {r_basic.text[:80]}")
    if r_basic.status_code == 200:
        working_user = u
        working_key = key_32
        print(f"🎉 VALIDATED CREDENTIALS FOR USER '{u}'")
        break

if not working_user:
    working_user = "akashcc"
    working_key = key_32

# Configure ~/.kaggle/kaggle.json
kaggle_dir = os.path.expanduser("~/.kaggle")
os.makedirs(kaggle_dir, exist_ok=True)
with open(os.path.join(kaggle_dir, "kaggle.json"), "w") as f:
    json.dump({"username": working_user, "key": working_key}, f)
os.chmod(os.path.join(kaggle_dir, "kaggle.json"), 0o600)

meta_path = "kaggle_kernel/kernel-metadata.json"
if os.path.exists(meta_path):
    with open(meta_path, "r") as mf:
        mdata = json.load(mf)
    mdata["id"] = f"{working_user}/qlib-alpha-gpu-trainer"
    with open(meta_path, "w") as mf:
        json.dump(mdata, mf, indent=2)
    print(f"Updated kernel-metadata.json id to: {mdata['id']}")
