#!/usr/bin/env bash
# Bring up the Cockroach Internet stack on a local kind cluster.
#   just k8s-up      (== bash deploy/k8s/up.sh)
# Env knobs:
#   SKIP_BUILD=1       don't rebuild images (just load + apply)
#   INSTALL_INGRESS=1  also install ingress-nginx for kind (needs internet)
#   METRICS_SERVER=1   install metrics-server (needed for the feature-worker HPA)
#   KIND_VERSION       kind release for the direct-download fallback (default v0.24.0)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER=cockroach
KIND_VERSION="${KIND_VERSION:-v0.24.0}"
BIN_DIR="$ROOT/deploy/bin"
export PATH="$BIN_DIR:$PATH"

log()  { printf '\033[1;36m[k8s-up]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[k8s-up] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[k8s-up] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker not found"
command -v kubectl >/dev/null || die "kubectl not found"
docker info >/dev/null 2>&1 || die "docker daemon not reachable"

# ---------------------------------------------------------------- install kind
ensure_kind() {
  if command -v kind >/dev/null 2>&1; then return; fi
  log "kind not found — installing"
  if command -v winget >/dev/null 2>&1; then
    winget install --id Kubernetes.kind -e --accept-source-agreements --accept-package-agreements --silent || warn "winget install failed"
    # winget exposes it via WinGet/Links (may need a new shell) or the package dir itself.
    export PATH="$PATH:$HOME/AppData/Local/Microsoft/WinGet/Links"
    for d in "$HOME"/AppData/Local/Microsoft/WinGet/Packages/Kubernetes.kind_*; do
      [[ -d "$d" ]] && export PATH="$PATH:$d"
    done
    hash -r
    command -v kind >/dev/null 2>&1 && return
  fi
  if command -v go >/dev/null 2>&1; then
    log "trying: go install sigs.k8s.io/kind@$KIND_VERSION"
    GOBIN="$BIN_DIR" go install "sigs.k8s.io/kind@$KIND_VERSION" && command -v kind >/dev/null 2>&1 && return
  fi
  mkdir -p "$BIN_DIR"
  local os arch ext=""
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT) os=windows; ext=.exe ;;
    Darwin) os=darwin ;;
    *) os=linux ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) arch=arm64 ;;
    *) arch=amd64 ;;
  esac
  local url="https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-${os}-${arch}"
  log "downloading $url -> $BIN_DIR/kind$ext"
  curl -fsSL -o "$BIN_DIR/kind$ext" "$url" && chmod +x "$BIN_DIR/kind$ext"
  command -v kind >/dev/null 2>&1 || die "could not install kind; install it manually (https://kind.sigs.k8s.io)"
}
ensure_kind
log "kind: $(kind version)"

# ---------------------------------------------------------------- cluster
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  log "cluster '$CLUSTER' already exists"
else
  log "creating kind cluster '$CLUSTER'"
  kind create cluster --name "$CLUSTER" --config "$ROOT/deploy/kind-cluster.yaml" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

# ---------------------------------------------------------------- images
# service name -> build context (Dockerfile expected at <context>/Dockerfile)
declare -A CONTEXTS=(
  [mqtt-bridge]="$ROOT/services/mqtt-bridge"
  [swarm-gen]="$ROOT/services/swarm-gen"
  [api-gateway]="$ROOT/services/api-gateway"
  [feature-worker]="$ROOT/services/feature-worker"
  [dashboard]="$ROOT/dashboard"
)
BUILT=()
for svc in mqtt-bridge swarm-gen api-gateway feature-worker dashboard; do
  ctx="${CONTEXTS[$svc]}"
  img="cockroach/$svc:dev"
  if [[ ! -f "$ctx/Dockerfile" ]]; then
    warn "no Dockerfile at $ctx — skipping $img (pods for $svc will stay ImagePullBackOff until it exists)"
    continue
  fi
  if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
    log "building $img"
    if [[ "$svc" == "dashboard" ]]; then
      # NEXT_PUBLIC_* is baked into the client bundle; point it at the api-gateway NodePort.
      docker build -t "$img" \
        --build-arg NEXT_PUBLIC_API_URL="${DASHBOARD_API_URL:-http://localhost:30080}" \
        --build-arg NEXT_PUBLIC_MOCK="${DASHBOARD_MOCK:-0}" "$ctx"
    else
      docker build -t "$img" "$ctx"
    fi
  fi
  if docker image inspect "$img" >/dev/null 2>&1; then
    BUILT+=("$img")
  else
    warn "image $img not present; skipping load"
  fi
done
if ((${#BUILT[@]})); then
  log "loading ${#BUILT[@]} image(s) into kind"
  kind load docker-image --name "$CLUSTER" "${BUILT[@]}"
fi

# ---------------------------------------------------------------- addons
if [[ "${INSTALL_INGRESS:-0}" == "1" ]]; then
  log "installing ingress-nginx (kind provider)"
  kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
  kubectl -n ingress-nginx wait --for=condition=Ready pod -l app.kubernetes.io/component=controller --timeout=180s || warn "ingress controller not ready yet"
fi
if [[ "${METRICS_SERVER:-0}" == "1" ]]; then
  log "installing metrics-server (for HPA)"
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  kubectl -n kube-system patch deployment metrics-server --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' || true
fi

# ---------------------------------------------------------------- apply
log "applying kustomize overlay dev"
kubectl apply -k "$ROOT/deploy/k8s/overlays/dev"

log "waiting for redpanda"
kubectl -n cockroach rollout status statefulset/redpanda --timeout=240s || warn "redpanda not ready yet"
kubectl -n cockroach wait --for=condition=complete job/topics-init --timeout=240s || warn "topics-init not complete yet"

for d in mosquitto redpanda-console mqtt-bridge swarm-gen api-gateway feature-worker dashboard; do
  kubectl -n cockroach rollout status "deployment/$d" --timeout=180s || warn "$d not ready (missing image?)"
done

log "pods:"
kubectl -n cockroach get pods -o wide
cat <<EOF

  dashboard     http://localhost:30000
  api-gateway   http://localhost:30080/health   (WS: ws://localhost:30080/ws/grid)
  mosquitto     mqtt://<host-ip>:31883          (ESP32 MQTT_HOST/MQTT_PORT)
  console       kubectl -n cockroach port-forward svc/redpanda-console 18081:8080
  tear down     just k8s-down   (kind delete cluster --name cockroach)
EOF
