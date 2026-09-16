import os
import sys
import json
import requests

raw_token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
user = os.environ.get("KAGGLE_USERNAME", "").strip() or "akashcc"
key = ""

print(f"[DIAG] raw_token length: {len(raw_token)}")
has_brace = raw_token.startswith("{")
has_colon = ":" in raw_token
print(f"[DIAG] starts with brace: {has_brace}")
print(f"[DIAG] contains colon: {has_colon}")

if has_brace:
    try:
        data = json.loads(raw_token)
        user = data.get("username", user)
        key = data.get("key", "")
    except Exception as e:
        print("[WARN] Failed to parse JSON token:", e)

if not key:
    if has_colon:
        parts = raw_token.split(":", 1)
        user = parts[0].strip()
        key = parts[1].strip()
    else:
        key = raw_token

print(f"[DIAG] Initial username: {user}")
print(f"[DIAG] Initial key length: {len(key)}")

candidate_users = [user, "Akashcc702", "akashcc702", "akashcc", "akashccakashcc10", "akash"]
candidate_users = list(dict.fromkeys(candidate_users))

success_user = None
for u in candidate_users:
    url = "https://www.kaggle.com/api/v1/datasets/list?page=1"
    resp = requests.get(url, auth=(u, key))
    print(f"[AUTH TEST] user=\"{u}\" -> HTTP {resp.status_code} | body: {resp.text[:120]}")
    if resp.status_code == 200:
        success_user = u
        break

if success_user:
    user = success_user
    print(f"SUCCESS! Valid Kaggle credentials verified for user: {user}")
else:
    print("WARNING: Candidate usernames did not match or key needs review.")

kaggle_dir = os.path.expanduser("~/.kaggle")
os.makedirs(kaggle_dir, exist_ok=True)
creds_file = os.path.join(kaggle_dir, "kaggle.json")
with open(creds_file, "w") as f:
    json.dump({"username": user, "key": key}, f)
os.chmod(creds_file, 0o600)

meta_path = "kaggle_kernel/kernel-metadata.json"
if os.path.exists(meta_path):
    with open(meta_path, "r") as mf:
        mdata = json.load(mf)
    mdata["id"] = f"{user}/qlib-alpha-gpu-trainer"
    with open(meta_path, "w") as mf:
        json.dump(mdata, mf, indent=2)
    print(f"Updated kernel-metadata.json id to: {mdata['id']}")
