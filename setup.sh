#!/bin/bash

echo "================================================"
echo "  DOWNLOAD TOOLKIT — SETUP"
echo "================================================"

# ─── UPDATE & INSTALL PACKAGES ───────────────────
echo ""
echo "[*] Updating packages..."
pkg update -y && pkg upgrade -y

echo ""
echo "[*] Installing required packages..."
pkg install python git aria2 tmux termux-api -y

# ─── INSTALL PYTHON DEPENDENCIES ─────────────────
echo ""
echo "[*] Installing Python dependencies..."
pip install requests beautifulsoup4 yt-dlp

# ─── CLONE OR UPDATE REPO ────────────────────────
echo ""
if [ -d "$HOME/download-toolkit" ]; then
    echo "[*] Toolkit already exists — updating..."
    cd "$HOME/download-toolkit" && git pull
else
    echo "[*] Cloning toolkit..."
    git clone https://github.com/owoborodebukumi-art/download-toolkit.git "$HOME/download-toolkit"
fi

# ─── SET UP AUTO-LAUNCH ──────────────────────────
echo ""
echo "[*] Setting up auto-launch..."
echo 'python ~/download-toolkit/main.py' > "$HOME/.bashrc"

# ─── DONE ─────────────────────────────────────────
echo ""
echo "================================================"
echo "  SETUP COMPLETE!"
echo "  Close and reopen Termux to start downloading"
echo "================================================"
