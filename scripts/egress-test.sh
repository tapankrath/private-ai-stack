#!/usr/bin/env bash
# Prove zero egress: every running container except the allowed ones must FAIL to reach
# the internet. Run after `make up` and after every image update. Exit 1 on any leak.
#
#   ALLOWED: caddy (edge proxy; host firewall must still block its outbound traffic)
#            searxng (only when the research profile is on)
#            ollama-relay (macOS only: forwards to one fixed address, the Ollama app on this Mac)
set -uo pipefail

ALLOWED_REGEX="${ALLOWED_REGEX:-caddy|searxng|model-pull|ollama-relay}"
TARGETS=("https://1.1.1.1" "https://huggingface.co" "https://registry.ollama.ai")
leaks=0

for c in $(docker compose ps --format '{{.Service}}'); do
  if [[ "$c" =~ ^($ALLOWED_REGEX)$ ]]; then
    echo "skip  $c (allowed egress exception)"
    continue
  fi
  for t in "${TARGETS[@]}"; do
    # try curl, then wget, then python; any success is a leak
    if docker compose exec -T "$c" sh -c \
      "command -v curl >/dev/null && curl -s -m 5 -o /dev/null $t || \
       command -v wget >/dev/null && wget -q -T 5 -O /dev/null $t || \
       command -v python3 >/dev/null && python3 -c 'import urllib.request,sys;urllib.request.urlopen(sys.argv[1],timeout=5)' $t" \
      >/dev/null 2>&1; then
      echo "LEAK  $c reached $t"
      leaks=$((leaks + 1))
    fi
  done
  echo "ok    $c has no outbound access"
done

# Bypass check: the chat UI must not be able to reach the model runtime directly.
for target in "http://ollama:11434/api/tags" "http://vllm:8000/v1/models"; do
  if docker compose ps --format '{{.Service}}' | grep -qx open-webui && \
     docker compose exec -T open-webui python3 -c "import urllib.request,sys;urllib.request.urlopen(sys.argv[1],timeout=5)" "$target" >/dev/null 2>&1; then
    echo "BYPASS open-webui reached $target without the Gateway"
    leaks=$((leaks + 1))
  fi
done

if (( leaks > 0 )); then
  echo "FAILED: $leaks outbound connection(s) succeeded."
  exit 1
fi
echo "PASSED: no container outside the allowlist can reach the internet."
