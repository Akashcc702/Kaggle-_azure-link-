import os
import sys
import json
import re
import requests

raw_token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
akashcc_val = os.environ.get("AKASHCC", "").strip()
user_env = os.environ.get("KAGGLE_USERNAME", "").strip()

print(f"[RESOLVER] KAGGLE_API_TOKEN len: {len(raw_token)}")
print(f"[RESOLVER] AKASHCC secret len: {len(akashcc_val)}")
print(f"[RESOLVER] KAGGLE_USERNAME len: {len(user_env)}")

# Collect possible keys
candidate_keys = []
candidate_users = ["akashcc", "Akashcc", "akashcc702", "Akashcc702"]

for val in [akashcc_val, raw_token]:
    if not val:
        continue
    # Check if JSON
    if "{" in val and "}" in val:
        try:
            d = json.loads(val[val.find("{"):val.rfind("}")+1])
            if "username" in d:
                candidate_users.insert(0, d["username"])
            if "key" in d:
                candidate_keys.insert(0, d["key"])
        except Exception as e:
            print("[WARN] JSON parse error:", e)
    
    # Check 32-hex match
    hex_match = re.search(r"[a-f0-9]{32}", val, re.IGNORECASE)
    if hex_match:
        candidate_keys.append(hex_match.group(0).lower())
    
    # Raw value without quotes
    cleaned = val.strip("\"' \t\r\n")
    if cleaned and cleaned not in candidate_keys:
        candidate_keys.append(cleaned)
    
    # If val looks like a username (e.g. alphanumeric < 25 chars without hex)
    if len(cleaned) < 25 and re.match(r"^[a-zA-Z0-9_]+$", cleaned) and not hex_match:
        candidate_users.insert(0, cleaned)

# Dedup
candidate_keys = list(dict.fromkeys(candidate_keys))
candidate_users = list(dict.fromkeys(candidate_users))

print(f"[RESOLVER] Candidate users: {candidate_users}")
print(f"[RESOLVER] Found {len(candidate_keys)} candidate key(s) with lengths: {[len(k) for k in candidate_keys]}")

# Test kernel push payload
with open("kaggle_kernel/train_alpha_gpu.py", "r", encoding="utf-8") as f:
    code_text = f.read()

payload = {
    "slug": "qlib-alpha-gpu-trainer",
    "newTitle": "Qlib Alpha GPU Trainer",
    "text": code_text,
    "language": "python",
    "kernelType": "script",
    "isPrivate": True,
    "enableGpu": True,
    "enableInternet": True
}

verified_user = None
verified_key = None

for u in candidate_users:
    for k in candidate_keys:
        # Test push endpoint directly
        push_url = "https://www.kaggle.com/api/v1/kernels/push"
        r = requests.post(push_url, auth=(u, k), json=payload)
        print(f"[TEST] user='{u}', key_len={len(k)} -> HTTP {r.status_code} | {r.text[:100]}")
        if r.status_code in [200, 201]:
            verified_user = u
            verified_key = k
            print(f"🎉 SUCCESS! Verified working credentials for user '{u}'!")
            break
        elif "has not accepted" in r.text or "terms" in r.text.lower():
            verified_user = u
            verified_key = k
            print(f"[INFO] Auth passed, but terms needed: {r.text}")
            break
    if verified_user:
        break

if not verified_user:
    # Fallback to best guess
    verified_user = candidate_users[0] if candidate_users else "akashcc"
    verified_key = candidate_keys[0] if candidate_keys else raw_token
    print(f"[WARN] No combination returned 200, using best guess: user='{verified_user}', key_len={len(verified_key)}")

# Write to ~/.kaggle/kaggle.json
kaggle_dir = os.path.expanduser("~/.kaggle")
os.makedirs(kaggle_dir, exist_ok=True)
with open(os.path.join(kaggle_dir, "kaggle.json"), "w") as f:
    json.dump({"username": verified_user, "key": verified_key}, f)
os.chmod(os.path.join(kaggle_dir, "kaggle.json"), 0o600)

meta_path = "kaggle_kernel/kernel-metadata.json"
if os.path.exists(meta_path):
    with open(meta_path, "r") as mf:
        mdata = json.load(mf)
    mdata["id"] = f"{verified_user}/qlib-alpha-gpu-trainer"
    with open(meta_path, "w") as mf:
        json.dump(mdata, mf, indent=2)
    print(f"Updated kernel-metadata.json id to: {mdata['id']}")
