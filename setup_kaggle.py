import os
import sys
import json
import requests

raw_token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
user = os.environ.get("KAGGLE_USERNAME", "").strip() or "akashcc"
key = ""

if raw_token.startswith("{"):
    try:
        data = json.loads(raw_token)
        user = data.get("username", user)
        key = data.get("key", "")
    except Exception:
        pass

if not key:
    if ":" in raw_token:
        parts = raw_token.split(":", 1)
        user = parts[0].strip()
        key = parts[1].strip()
    else:
        key = raw_token

print(f"[DIAG] Testing user: {user[:3]}***, key len: {len(key)}")

# Test API call to kernels/push directly to capture the full error message
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

push_url = "https://www.kaggle.com/api/v1/kernels/push"
r = requests.post(push_url, auth=(user, key), json=payload)
print(f"[DIRECT KERNEL PUSH TEST] HTTP {r.status_code}")
print(f"[DIRECT KERNEL PUSH RESPONSE] {r.text}")

# Write credentials to ~/.kaggle/kaggle.json
kaggle_dir = os.path.expanduser("~/.kaggle")
os.makedirs(kaggle_dir, exist_ok=True)
with open(os.path.join(kaggle_dir, "kaggle.json"), "w") as f:
    json.dump({"username": user, "key": key}, f)
os.chmod(os.path.join(kaggle_dir, "kaggle.json"), 0o600)
