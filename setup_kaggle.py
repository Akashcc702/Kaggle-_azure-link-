import os
import sys
import json
import re
import requests

raw_token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
print(f"[TOKEN ANALYSIS] Length: {len(raw_token)}")
print(f"[TOKEN ANALYSIS] Leading ords: {[ord(c) for c in raw_token[:6]]}")
print(f"[TOKEN ANALYSIS] Trailing ords: {[ord(c) for c in raw_token[-6:]]}")

# Look for 32-char hex key
hex_match = re.search(r"[a-f0-9]{32}", raw_token, re.IGNORECASE)
if hex_match:
    key = hex_match.group(0).lower()
else:
    key = raw_token.strip()

user = "akashcc"
print(f"[TESTING] user={user}, key_len={len(key)}")

# 1. Test official datasets/list with mine=true
url_mine = "https://www.kaggle.com/api/v1/datasets/list?mine=true"
r_mine = requests.get(url_mine, auth=(user, key))
print(f"[MINE TEST] HTTP {r_mine.status_code} | {r_mine.text[:100]}")

# 2. Test kernels/list with mine=true
url_kernels = "https://www.kaggle.com/api/v1/kernels/list?mine=true"
r_kernels = requests.get(url_kernels, auth=(user, key))
print(f"[KERNELS TEST] HTTP {r_kernels.status_code} | {r_kernels.text[:100]}")

# 3. Test whoami or profile
url_user = "https://www.kaggle.com/api/v1/users/akashcc"
r_user = requests.get(url_user, auth=(user, key))
print(f"[USER TEST] HTTP {r_user.status_code} | {r_user.text[:100]}")

# Write credentials to ~/.kaggle/kaggle.json
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
