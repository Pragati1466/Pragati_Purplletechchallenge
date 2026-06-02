#!/usr/bin/env bash
# ============================================================
# run.sh — Process all CCTV clips for Brigade Road Bangalore
#
# Usage:
#   ./pipeline/run.sh [api_url]
#
# Examples:
#   ./pipeline/run.sh                          # file-only output
#   ./pipeline/run.sh http://localhost:8000    # stream to API live
# ============================================================
set -euo pipefail

API_URL="${1:-}"
MODEL="${MODEL:-yolov8n.pt}"
OUTPUT_DIR="output/events"
LAYOUT="data/store_layout.json"
FOOTAGE_DIR="CCTV Footage"
STORE_ID="ST1008"
DATE="2026-04-10"

mkdir -p "$OUTPUT_DIR"

echo "============================================"
echo " Store Intelligence - Detection Pipeline"
echo " Store     : $STORE_ID (Brigade Road, Bangalore)"
echo " Date      : $DATE"
echo " Layout    : $LAYOUT"
echo " API URL   : ${API_URL:-<not set, file-only>}"
echo " Model     : $MODEL"
echo "============================================"

# Camera mapping: filename → camera_id, start_time (from POS data: store opens ~12:00)
declare -A CAM_IDS=(
    ["CAM 1"]="CAM_ENTRY_01"
    ["CAM 2"]="CAM_FLOOR_01"
    ["CAM 3"]="CAM_FLOOR_02"
    ["CAM 4"]="CAM_BILLING_01"
    ["CAM 5"]="CAM_FLOOR_03"
)

# Approximate clip start times based on POS data (first transaction at 12:15)
# Cameras likely started recording around 12:00
declare -A CAM_STARTS=(
    ["CAM 1"]="${DATE}T12:00:00Z"
    ["CAM 2"]="${DATE}T12:00:00Z"
    ["CAM 3"]="${DATE}T12:00:00Z"
    ["CAM 4"]="${DATE}T12:00:00Z"
    ["CAM 5"]="${DATE}T12:00:00Z"
)

TOTAL_EVENTS=0

for cam_name in "CAM 1" "CAM 2" "CAM 3" "CAM 4" "CAM 5"; do
    cam_id="${CAM_IDS[$cam_name]}"
    start_time="${CAM_STARTS[$cam_name]}"
    video_file="${FOOTAGE_DIR}/${cam_name}.mp4"
    output_file="${OUTPUT_DIR}/${STORE_ID}_${cam_id}.jsonl"

    if [[ ! -f "$video_file" ]]; then
        echo "  [SKIP] Not found: $video_file"
        continue
    fi

    echo ""
    echo "── Processing $cam_name → $cam_id ──"
    echo "   Video : $video_file"
    echo "   Output: $output_file"

    api_arg=""
    if [[ -n "$API_URL" ]]; then
        api_arg="--api-url $API_URL"
    fi

    python3 pipeline/detect.py \
        --video "$video_file" \
        --store-id "$STORE_ID" \
        --camera-id "$cam_id" \
        --layout "$LAYOUT" \
        --output "$output_file" \
        --model "$MODEL" \
        --start-time "$start_time" \
        $api_arg

    count=$(wc -l < "$output_file" 2>/dev/null || echo 0)
    TOTAL_EVENTS=$((TOTAL_EVENTS + count))
    echo "   ✓ $count events emitted"
done

# Merge all JSONL files
MERGED="output/events.jsonl"
cat "$OUTPUT_DIR"/${STORE_ID}_*.jsonl > "$MERGED" 2>/dev/null || true
echo ""
echo "============================================"
echo " Total events emitted : $TOTAL_EVENTS"
echo " Merged output        : $MERGED"
echo "============================================"

# Seed POS transactions into API if URL provided
if [[ -n "$API_URL" ]]; then
    echo ""
    echo "Seeding POS transactions into API..."
    python3 data/seed_pos_transactions.py --api-url "$API_URL"
fi

echo ""
echo "✓ Done! Dashboard: ${API_URL:-http://localhost:8000}/dashboard"
