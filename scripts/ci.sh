#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-all}"
case "$mode" in syntax|unit|container|worker|keycloak|terraform|all) ;; *) echo 'Usage: scripts/ci.sh [syntax|unit|container|worker|keycloak|terraform|all]' >&2; exit 2 ;; esac
if [[ "$mode" == syntax || "$mode" == all ]]; then python3 scripts/validate-source.py; fi
if [[ "$mode" == unit || "$mode" == all ]]; then
  PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m unittest discover -s tests -v
  python3 -m json.tool skill-package/interactionModels/custom/en-US.json > /dev/null
  bash -n scripts/deploy-lambda.sh
fi
if [[ "$mode" == container || "$mode" == all ]]; then
  docker build --tag home-energy-test .
  docker run --rm --entrypoint python home-energy-test -c 'from ask_sdk_webservice_support.verifier import RequestVerifier; RequestVerifier()'
  docker run --rm --volume "$PWD/tests:/tests:ro" --volume "$PWD/scripts:/scripts:ro" --volume "$PWD/deploy/multi-household:/deploy/multi-household:ro" --entrypoint python home-energy-test -m unittest discover -s /tests -v
fi
if [[ "$mode" == worker || "$mode" == all ]]; then
  node --test deploy/worker-vpc/relay.test.mjs deploy/account-linking-vpc/relay.test.mjs
fi

if [[ "$mode" == terraform || "$mode" == all ]]; then
  python3 scripts/validate-terraform.py
fi

if [[ "$mode" == keycloak || "$mode" == all ]]; then
  PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" python3 tests/integration/keycloak_smoke.py
fi
