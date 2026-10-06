#!/usr/bin/env bash
set -e

# Resolve repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==================================================="
echo "🔷 Starting Dynamics Suite"
echo "==================================================="

# 1. Ensure uv is available in user space (~/.local/bin or ~/.cargo/bin)
if ! command -v uv &> /dev/null; then
    if [ -f "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    elif [ -f "$HOME/.cargo/bin/uv" ]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    else
        echo "📦 'uv' not found. Installing uv in user space (no admin required)..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

# 2. Ensure virtual environment exists (seeded with pip) and satisfies Python >= 3.10
RECREATE_VENV=0
if [ -f ".venv/bin/python3" ]; then
    if ! .venv/bin/python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
        echo "⚠️ Existing virtual environment uses an older Python (< 3.10). Recreating with Python 3.11..."
        RECREATE_VENV=1
    fi
else
    RECREATE_VENV=1
fi

if [ "$RECREATE_VENV" -eq 1 ]; then
    echo "🔨 Creating virtual environment with Python 3.11 using uv (.venv)..."
    uv venv --clear --python 3.11 --seed .venv
fi

# 3. Synchronize / install core requirements
echo "⚡ Checking and installing dependencies with uv..."
uv pip install -r requirements.txt

# Run install_plugins.py to ensure all discovered plugins have dependencies installed
if [ -f "install_plugins.py" ]; then
    .venv/bin/python3 install_plugins.py
fi

# 4. Launch Streamlit app
echo "🚀 Launching Dynamics Suite..."
.venv/bin/streamlit run app.py
