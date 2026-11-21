#!/bin/sh
# Writes the backend URL into config.js so the same image works in any environment.
set -e
cat > /usr/share/nginx/html/config.js <<CONF
window.__APP_CONFIG__ = { API_URL: "${API_URL:-http://localhost:8000}" };
CONF
