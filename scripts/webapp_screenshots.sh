#!/usr/bin/env bash
# Visual self-check: screenshot the three main pages at two viewports in both
# colour schemes, plus a horizontal-overflow probe at every supported width.
#
# Requires a headless Chromium in the environment; this script installs nothing.
set -u

PORT="${PORT:-8765}"
TOKEN="${TOKEN:-verify-token-2f9a}"
BASE="http://127.0.0.1:${PORT}"
OUT="${OUT:-/tmp/webapp-shots}"
CHROME="${CHROME:-/snap/bin/chromium}"
TICKER="${TICKER:-AAPL}"
# The card to photograph is whichever card the API lists first for the ticker.
CARD_ID="${CARD_ID:-$(curl -s -H "Host: 127.0.0.1:${PORT}" \
  "${BASE}/api/cards?ticker=${TICKER}&token=${TOKEN}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["cards"][0]["card_id"])')}"
echo "ticker=${TICKER} card=${CARD_ID}"

mkdir -p "${OUT}"
rm -f "${OUT}"/*.png

shoot () { # name width height scheme hash
  local name="$1" width="$2" height="$3" scheme="$4" hash="$5"
  "${CHROME}" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
    --force-device-scale-factor=1 \
    --window-size="${width},${height}" \
    --virtual-time-budget=9000 \
    --force-color-profile=srgb \
    --screenshot="${OUT}/${name}.png" \
    --user-data-dir="$(mktemp -d)" \
    --blink-settings="preferredColorScheme=${scheme}" \
    "${BASE}/?token=${TOKEN}#${hash}" >/dev/null 2>&1
  if [ -s "${OUT}/${name}.png" ]; then
    echo "OK   ${name}.png  ${width}x${height} ${scheme}"
  else
    echo "FAIL ${name}.png"
  fi
}

for scheme in light dark; do
  shoot "overview-1440-${scheme}"  1440 900  "${scheme}" "/overview"
  shoot "company-1440-${scheme}"   1440 900  "${scheme}" "/company/${TICKER}"
  shoot "card-1440-${scheme}"      1440 900  "${scheme}" "/cards/${CARD_ID}"
  shoot "overview-390-${scheme}"   390  844  "${scheme}" "/overview"
  shoot "company-390-${scheme}"    390  844  "${scheme}" "/company/${TICKER}"
  shoot "card-390-${scheme}"       390  844  "${scheme}" "/cards/${CARD_ID}"
  shoot "jobs-1440-${scheme}"      1440 900  "${scheme}" "/jobs"
  shoot "cards-1440-${scheme}"     1440 900  "${scheme}" "/cards"
done

echo
echo "screenshots in ${OUT}"
