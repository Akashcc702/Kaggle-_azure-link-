#!/bin/bash
# ============================================================
# CC AlgoTrading — Crontab Setup v3.0 (Automated Mon-Fri Schedule)
# crontab_setup.sh - Run ONCE on Azure VM
# ============================================================

BOT_DIR="$HOME/azure_bot"
PYTHON="$HOME/algo_env/bin/python3"
LOG_DIR="$BOT_DIR/logs"

mkdir -p "$LOG_DIR"

# Add clean schedule (Mon-Fri only)
cat << 'CRON' | crontab -
# CC AlgoTrading Mon-Fri Automated Schedule
# 08:30 AM IST: Daily System Health Watchdog Heartbeat
30 8 * * 1-5 /home/azureuser/algo_env/bin/python3 /home/azureuser/azure_bot/system_watchdog.py --mode heartbeat >> /home/azureuser/azure_bot/logs/watchdog.log 2>&1

# @reboot: Auto-start 24x7 Telegram Daemon Service upon VM Boot
@reboot /bin/bash -c "sudo systemctl start tg_daemon.service 2>/dev/null || true"

# 08:35 AM IST: Pre-Market GPU Alpha Weights Cloud Sync (Pulls latest Kaggle GPU weights)
35 8 * * 1-5 cd /home/azureuser/azure_bot && git pull origin main 2>/dev/null || true

# 08:45 AM IST: Headless Chrome OAuth Login (Zombie Reaped)
45 8 * * 1-5 /home/azureuser/algo_env/bin/python3 /home/azureuser/azure_bot/oauth_headless_login.py >> /home/azureuser/azure_bot/logs/login.log 2>&1

# 09:15 AM IST: Launch Full Intraday Engine v3.0 (8 Master Features)
15 9 * * 1-5 /home/azureuser/algo_env/bin/python3 /home/azureuser/azure_bot/smallcap_intraday_engine.py >> /home/azureuser/azure_bot/logs/engine.log 2>&1

# 12:00 PM IST: Mid-Day Shoonya Session Validation & Auto-Recovery
0 12 * * 1-5 /home/azureuser/algo_env/bin/python3 /home/azureuser/azure_bot/system_watchdog.py --mode session_check >> /home/azureuser/azure_bot/logs/watchdog.log 2>&1

# 23:55 PM IST: Daily Log Rotation
55 23 * * * /usr/sbin/logrotate -s /home/azureuser/azure_bot/logs/logrotate.status /etc/logrotate.d/algo_bot >/dev/null 2>&1

# 02:00 AM IST Saturday: Weekend Garbage Collection & Temp Cleanup
0 2 * * 6 rm -f /tmp/shoonya_debug*.png /tmp/*.tmp 2>/dev/null
CRON

echo "========================================"
echo " CC AlgoTrading Crontab UPDATED v3.0!"
echo "========================================"
crontab -l
