# ⚡ CC AlgoTrading – Kaggle Free GPU + Azure Intraday Model Pipeline

[![Kaggle GPU Retrain](https://github.com/Akashcc702/Kaggle-_azure-link-/actions/workflows/kaggle_gpu_train.yml/badge.svg)](https://github.com/Akashcc702/Kaggle-_azure-link-/actions/workflows/kaggle_gpu_train.yml)
[![Hardware](https://img.shields.io/badge/Hardware-NVIDIA%20Tesla%20T4%20(16GB%20VRAM)-76B900?logo=nvidia)](https://www.kaggle.com)
[![Target VM](https://img.shields.io/badge/Azure%20VM-Central%20India%20(104.211.100.195)-0078D4?logo=microsoftazure)](https://portal.azure.com)

Automated institutional quant training pipeline connecting **Google Kaggle Free GPU (NVIDIA Tesla T4 16GB)** with **GitHub Actions** and our **Live Azure Intraday Trading Engine (`104.211.100.195`)**.

---

## 🎯 System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. GitHub Actions (.github/workflows/kaggle_gpu_train.yml)             │
│    • Triggers: Weekly Sunday 20:00 IST or Telegram /retrain_gpu        │
│    • Pushes kernel to Google Kaggle via Kaggle API                     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (kaggle kernels push)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 2. Google Kaggle Cloud (NVIDIA Tesla T4 GPU - 16GB VRAM)               │
│    • 113-Stock Cross-Sectional Panel Calibration                       │
│    • 30 Micro-Alpha Factors Modified Gram-Schmidt Orthogonalization    │
│    • Generalized Cross-Validation (GCV) Ridge Shrinkage (λ*)           │
│    • Generates: alpha_weights.json & daily_top_whitelist.json         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (kaggle kernels output)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 3. Automated Deployment to Azure VM (104.211.100.195)                  │
│    • Validates Institutional Quality Gate (Mean IC > 0.05)             │
│    • SCP transfer directly to /home/azureuser/azure_bot/               │
│    • Restarts tg_daemon.service on Azure VM                            │
│    • Sends instant Telegram notification to Trader                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 📱 Telegram Bot Commands

| Command | Action | Execution Time |
| :--- | :--- | :---: |
| `/retrain_gpu` | Dispatches on-demand Kaggle GPU training run | ~6 - 8 mins |
| `/gpu_status` | Checks real-time Kaggle GPU workflow status | Instant (< 0.5s) |
| `/credits` | Inspects Azure for Students cloud cost & runway | Instant (< 0.05s) |
| `/status` | Shows live trading engine and active positions | Instant |

---

## 🛡️ Institutional Quality Gates
1. **Factor Monotonicity Gate**: Monotonic rank spread between Q5 (top alpha) and Q1 (bottom alpha) $> +0.04$.
2. **Mean IC Threshold**: Minimum cross-sectional Information Coefficient $IC \ge 0.0400$.
3. **Sector Diversity Cap**: Maximum 2 stocks per sector in top whitelist to prevent sector concentration contagion.
4. **Watchdog Timeout**: Hard 15-minute timeout on Kaggle kernel executions to preserve free GPU quota.
