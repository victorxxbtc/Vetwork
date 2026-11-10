#!/usr/bin/env bash
# Register one demo agent and post one review job on a deployed testnet contract.
set -euo pipefail
: "${SWARM:?}"; : "${ME:?set ME to your testnet account}"
PH=$(printf 'You audit a grant team record.' | shasum -a 256 | cut -d' ' -f1)
near contract call-function as-transaction "$SWARM" register_agent \
  json-args "{\"name\":\"ledger\",\"section\":\"history\",\"prompt_hash\":\"$PH\",\"knowledge_base\":\"dated-context\",\"tools\":[\"sputnik.history\"],\"public_key\":null}" \
  prepaid-gas '100 Tgas' attached-deposit '1.1 NEAR' sign-as "$ME" network-config testnet sign-with-keychain send
near contract call-function as-transaction "$SWARM" post_job \
  json-args '{"dao":"marketing.sputnik-dao.near","proposal_id":1005,"description":"Grant review","commit_secs":600,"reveal_secs":600}' \
  prepaid-gas '30 Tgas' attached-deposit '2 NEAR' sign-as "$ME" network-config testnet sign-with-keychain send
