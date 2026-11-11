#!/usr/bin/env bash
# Build and deploy the swarm contract to testnet. Needs a funded testnet account
# that you control, logged in with near-cli-rs. Nothing here touches mainnet.
#   SWARM=vetwork-yourname.testnet REVIEWER=you.testnet OPERATOR=you.testnet ./scripts/deploy-testnet.sh
set -euo pipefail
: "${SWARM:?set SWARM to the contract account}"
: "${REVIEWER:?set REVIEWER}"
: "${OPERATOR:?set OPERATOR}"
cd "$(dirname "$0")/../contract"
cargo near build non-reproducible-wasm
near contract deploy "$SWARM" use-file target/near/vetwork_swarm.wasm \
  with-init-call new json-args "{\"owner\":\"$REVIEWER\",\"reviewer\":\"$REVIEWER\",\"operator\":\"$OPERATOR\"}" \
  prepaid-gas '100 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-keychain send
echo "Deployed. Put SWARM_CONTRACT_ID=$SWARM in .env"
