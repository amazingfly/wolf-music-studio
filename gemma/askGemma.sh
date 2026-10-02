#!/usr/bin/env bash

# ==============================================================================
# CONFIGURATION DEFAULTS
# ==============================================================================
SERVER_HOST="127.0.0.1"
SERVER_PORT="8080"
TEMPERATURE="0.7"
MAX_TOKENS="4096"
SYSTEM_PROMPT=""

# ==============================================================================
# USAGE HELP
# ==============================================================================
usage() {
    cat << HELP_EOF
Usage: $(basename "$0") [OPTIONS] "YOUR PROMPT HERE"

Options:
  -H, --host HOST        Server host address (Default: $SERVER_HOST)
  -p, --port PORT        Server port (Default: $SERVER_PORT)
  -t, --temp TEMP        Temperature setting (Default: $TEMPERATURE)
  -m, --max-tokens N     Max tokens to generate (Default: $MAX_TOKENS)
  -s, --system PROMPT    Optional system prompt
  -h, --help             Show this help message
HELP_EOF
    exit 0
}

# ==============================================================================
# COMMAND LINE FLAG PARSING
# ==============================================================================
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -H|--host)       SERVER_HOST="$2"; shift 2 ;;
        -p|--port)       SERVER_PORT="$2"; shift 2 ;;
        -t|--temp)       TEMPERATURE="$2"; shift 2 ;;
        -m|--max-tokens) MAX_TOKENS="$2"; shift 2 ;;
        -s|--system)     SYSTEM_PROMPT="$2"; shift 2 ;;
        -h|--help)       usage ;;
        -*|--*)          echo "Unknown option: $1"; usage ;;
        *)               POSITIONAL_ARGS+=("$1"); shift ;;
    esac
done

set -- "${POSITIONAL_ARGS[@]}"
PROMPT="$*"

if [ -z "$PROMPT" ]; then
    if [ ! -t 0 ]; then
        PROMPT=$(cat)
    else
        echo -n "Enter prompt: "
        read -r PROMPT
    fi
fi

if [ -z "$PROMPT" ]; then
    echo "❌ Error: Prompt cannot be empty."
    exit 1
fi

# ==============================================================================
# API EXECUTION
# ==============================================================================
API_URL="http://${SERVER_HOST}:${SERVER_PORT}/v1/chat/completions"

if [ -n "$SYSTEM_PROMPT" ]; then
    PAYLOAD=$(jq -n \
        --arg sys "$SYSTEM_PROMPT" \
        --arg user "$PROMPT" \
        --argjson temp "$TEMPERATURE" \
        --argjson max_tok "$MAX_TOKENS" \
        '{
            messages: [
                {role: "system", content: $sys},
                {role: "user", content: $user}
            ],
            temperature: $temp,
            max_tokens: $max_tok,
            stream: true
        }')
else
    PAYLOAD=$(jq -n \
        --arg user "$PROMPT" \
        --argjson temp "$TEMPERATURE" \
        --argjson max_tok "$MAX_TOKENS" \
        '{
            messages: [
                {role: "user", content: $user}
            ],
            temperature: $temp,
            max_tokens: $max_tok,
            stream: true
        }')
fi

TOKEN_COUNT=0
START_TIME=$(date +%s.%N)

# Process substitution (< <(...)) keeps while loop in main shell scope
while IFS= read -r line; do
    line="${line%$'\r'}"
    if [[ "$line" == data:* ]]; then
        json_data="${line#data: }"
        [ "$json_data" = "[DONE]" ] && break
        token=$(echo "$json_data" | jq -r '.choices[0].delta.content // empty' 2>/dev/null)
        if [ -n "$token" ]; then
            printf "%s" "$token"
            ((TOKEN_COUNT++))
        fi
    fi
done < <(curl -s -N "$API_URL" -H "Content-Type: application/json" -d "$PAYLOAD")

END_TIME=$(date +%s.%N)
ELAPSED=$(awk "BEGIN {print $END_TIME - $START_TIME}")

if [ "$TOKEN_COUNT" -gt 0 ]; then
    TPS=$(awk "BEGIN {if ($ELAPSED > 0) printf \"%.2f\", $TOKEN_COUNT / $ELAPSED; else print \"0.00\"}")
    TIME_FORMATTED=$(awk "BEGIN {printf \"%.1f\", $ELAPSED}")
    echo -e "\n\n\033[90m[Stats: ${TOKEN_COUNT} tokens generated in ${TIME_FORMATTED}s (${TPS} t/s)]\033[0m"
fi
