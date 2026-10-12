#!/usr/bin/env bash
# Deploy AI Financial Agent to Azure Container Apps with Bicep. No portal clicks.
#
#   ./deploy.sh deploy    build + push the image, create everything in Azure, check the link
#   ./deploy.sh update    build + push a new image and swap it in (secrets are kept)
#   ./deploy.sh destroy   delete everything this script created (one resource group)
#
# Settings (environment variables, all optional except DOCKERHUB_USER for deploy/update):
#   DOCKERHUB_USER  your Docker Hub user name; the image is <user>/ai-financial-agent (public)
#   LOCATION        Azure region (default eastus)       RESOURCE_GROUP (default rg-ai-financial-agent)
#   APP_PASSWORD    shared team password, 12+ characters (asked for, hidden, if not set)
#   ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID (optional)
#   IMAGE_TAG, APP_NAME   override the image tag (default: the commit) and the app name
#   DRY_RUN=1       print what would run, change nothing
#   YES=1           skip the "deploy to this subscription?" question
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${ROOT:-$(cd "$HERE/../.." && pwd)}"
LOCATION="${LOCATION:-eastus}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-ai-financial-agent}"
APP_NAME="${APP_NAME:-ai-financial-agent}"
DRY_RUN="${DRY_RUN:-0}"
PARAMS_FILE=""

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() { if [ "$DRY_RUN" = "1" ]; then printf '+ %s\n' "$*"; else "$@"; fi; }
usage() { printf 'Usage: %s deploy | update | destroy\n' "$0" >&2; exit 2; }

preflight() {  # preflight TOOL...: each tool must exist (Docker must also be running); Azure login is always needed
  [ "$DRY_RUN" = "1" ] && return 0
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null || die "$tool not found (install it and try again)"
    if [ "$tool" = "docker" ]; then
      docker info >/dev/null 2>&1 || die "Docker is not running: start Docker Desktop"
    fi
  done
  az account show >/dev/null 2>&1 || die "Not signed in to Azure: run 'az login'"
}

docker_hub_login_ok() {  # fail now, not after a ten minute build
  [ "$DRY_RUN" = "1" ] && return 0
  python3 - "${DOCKER_CONFIG:-$HOME/.docker}/config.json" <<'PY' || die "Not signed in to Docker Hub: run 'docker login' and try again"
import json, sys

try:
    auths = json.load(open(sys.argv[1])).get("auths", {})
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if any("docker.io" in registry for registry in auths) else 1)
PY
}

confirm_subscription() {
  [ "$DRY_RUN" = "1" ] || [ "${YES:-0}" = "1" ] && return 0
  local name
  name="$(az account show --query name -o tsv)"
  printf 'Azure subscription: %s\nRegion: %s   Resource group: %s\n' "$name" "$LOCATION" "$RESOURCE_GROUP"
  [ -t 0 ] || die "No terminal to confirm on (set YES=1 to skip the question)"
  read -rp "Continue? [y/N] " answer
  [ "$answer" = "y" ] || [ "$answer" = "Y" ] || die "Cancelled"
}

need_image() {
  : "${DOCKERHUB_USER:?Set DOCKERHUB_USER to your Docker Hub user name, for example: DOCKERHUB_USER=me ./deploy.sh deploy}"
  local tag
  tag="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
  if [ -z "${IMAGE_TAG:-}" ] && [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
    tag="$tag-dirty$(date +%H%M%S)"   # a new tag every time, so Azure never sees "the same image" and skips it
    printf 'Note: uncommitted changes are included, so the image is tagged %s\n' "$tag" >&2
  fi
  IMAGE="$DOCKERHUB_USER/ai-financial-agent:$tag"
}

ask_secret() {  # ask_secret VARIABLE "prompt": keep the value if already set, otherwise ask without echoing
  local var="$1" prompt="$2" value="${!1:-}"
  if [ -z "$value" ] && [ "$DRY_RUN" = "1" ]; then
    value="dry-run-placeholder"   # a dry run changes nothing, so it never asks for a real secret
  elif [ -z "$value" ]; then
    [ -t 0 ] || die "$var is not set and there is no terminal to ask on"
    read -rs -p "$prompt: " value
    printf '\n' >&2
  fi
  printf -v "$var" '%s' "$value"
}

build_and_push() {
  docker_hub_login_ok
  say "Building the image for linux/amd64 (Azure Container Apps does not run arm64; this is slower on an Apple Silicon Mac)"
  run docker buildx build --platform linux/amd64 --load -t "$IMAGE" "$ROOT"
  say "Pushing $IMAGE (public on Docker Hub; it holds the app code and sample data, no secrets)"
  run docker push "$IMAGE"
}

write_params() {  # a private temporary file, so the secrets never appear in a command line
  if [ "$DRY_RUN" = "1" ]; then
    PARAMS_FILE="/tmp/ai-financial-agent-params.json"
    printf '+ (write %s, readable only by you, deleted when the script ends)\n' "$PARAMS_FILE"
    return 0
  fi
  umask 077
  PARAMS_FILE="$(mktemp "${TMPDIR:-/tmp}/ai-financial-agent-params.XXXXXX")"
  trap 'rm -f "$PARAMS_FILE"' EXIT
  # Typed answers are plain shell variables, not exported: hand them to the helper explicitly.
  IMAGE="docker.io/$IMAGE" APP_NAME="$APP_NAME" APP_PASSWORD="${APP_PASSWORD:-}" ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    ANTHROPIC_WORKSPACE_ID="${ANTHROPIC_WORKSPACE_ID:-}" python3 - "$PARAMS_FILE" <<'PY'
import json, os, sys

for name in ("APP_PASSWORD", "ANTHROPIC_API_KEY"):
    if not os.environ.get(name):
        sys.exit(f"ERROR: {name} is empty: refusing to deploy a secret-less app")

def value(name):
    return {"value": os.environ.get(name, "")}

json.dump({
    "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
    "contentVersion": "1.0.0.0",
    "parameters": {
        "image": value("IMAGE"), "appName": value("APP_NAME"),
        "appPassword": value("APP_PASSWORD"), "anthropicApiKey": value("ANTHROPIC_API_KEY"),
        "anthropicWorkspaceId": value("ANTHROPIC_WORKSPACE_ID"),
    },
}, open(sys.argv[1], "w"))
PY
}

check_link() {  # $1 = URL. Expect 401 without the password; with it (when we know it) expect 200
  local url="$1" code i
  [ "$DRY_RUN" = "1" ] && { printf '+ check %s answers 401 without the password and 200 with it\n' "$url"; return 0; }
  say "Waiting for the app to answer (the first start pulls the image and can take up to three minutes)"
  for i in $(seq 1 36); do
    code="$(curl -s --max-time 60 -o /dev/null -w '%{http_code}' "$url/" || true)"
    [ "$code" = "401" ] && break
    sleep 5
  done
  [ "$code" = "401" ] || die "Expected 401 without the password, got '$code'. Logs: az containerapp logs show -g $RESOURCE_GROUP -n $APP_NAME --follow"
  printf 'OK: without the password the link answers 401.\n'
  if [ -n "${APP_PASSWORD:-}" ]; then
    local escaped="${APP_PASSWORD//\\/\\\\}"; escaped="${escaped//\"/\\\"}"
    code="$(printf 'user = "team:%s"\n' "$escaped" | curl -s --max-time 60 -K - -o /dev/null -w '%{http_code}' "$url/api/health" || true)"
    [ "$code" = "200" ] || die "Expected 200 with the password, got '$code'"
    printf 'OK: with the password the app answers 200.\n'
  fi
}

cmd_deploy() {
  preflight az docker python3; need_image; confirm_subscription
  ask_secret APP_PASSWORD "Choose the shared team password (12+ characters, hidden)"
  [ "${#APP_PASSWORD}" -ge 12 ] || die "APP_PASSWORD must be at least 12 characters"
  ask_secret ANTHROPIC_API_KEY "Paste your Anthropic API key (hidden)"
  [ -n "$ANTHROPIC_API_KEY" ] || die "ANTHROPIC_API_KEY is empty"
  build_and_push
  say "Creating resource group $RESOURCE_GROUP in $LOCATION"
  run az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
  write_params
  say "Deploying main.bicep (log workspace, Container Apps environment, the app)"
  local url
  if [ "$DRY_RUN" = "1" ]; then
    run az deployment group create --resource-group "$RESOURCE_GROUP" --template-file "$HERE/main.bicep" \
      --parameters "@$PARAMS_FILE" --query properties.outputs.appUrl.value --output tsv
    url="https://$APP_NAME.example.azurecontainerapps.io"
  else
    url="$(az deployment group create --resource-group "$RESOURCE_GROUP" --template-file "$HERE/main.bicep" \
      --parameters "@$PARAMS_FILE" --query properties.outputs.appUrl.value --output tsv)"
  fi
  check_link "$url"
  say "Done. Share this link and, separately, the password:"
  printf '  %s\n\nUpdate later:  DOCKERHUB_USER=%s %s update\nRemove all:    %s destroy\n' "$url" "${DOCKERHUB_USER}" "$0" "$0"
}

cmd_update() {
  preflight az docker; need_image; confirm_subscription
  build_and_push
  say "Swapping in the new image"
  run az containerapp update --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" --image "docker.io/$IMAGE" --output none
  local url="(see: az containerapp show -g $RESOURCE_GROUP -n $APP_NAME --query properties.configuration.ingress.fqdn -o tsv)"
  [ "$DRY_RUN" = "1" ] || url="https://$(az containerapp show --resource-group "$RESOURCE_GROUP" --name "$APP_NAME" --query properties.configuration.ingress.fqdn --output tsv)"
  check_link "$url"
}

cmd_destroy() {
  preflight az
  say "This deletes resource group $RESOURCE_GROUP and everything in it (the app, its logs, its secrets)."
  if [ "$DRY_RUN" != "1" ]; then
    [ -t 0 ] || die "No terminal to confirm on"
    read -rp "Type the resource group name to confirm: " typed
    [ "$typed" = "$RESOURCE_GROUP" ] || die "Name did not match, nothing deleted"
  fi
  run az group delete --name "$RESOURCE_GROUP" --yes
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then   # run the command only when executed, not when sourced by the tests
  case "${1:-}" in
    deploy)  cmd_deploy ;;
    update)  cmd_update ;;
    destroy) cmd_destroy ;;
    *)       usage ;;
  esac
fi
