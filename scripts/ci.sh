#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-all}"
case "$mode" in syntax|unit|container|worker|all) ;; *) echo 'Usage: scripts/ci.sh [syntax|unit|container|worker|all]' >&2; exit 2 ;; esac
if [[ "$mode" == syntax || "$mode" == all ]]; then python3 scripts/validate-source.py; fi
if [[ "$mode" == unit || "$mode" == all ]]; then
  PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m unittest discover -s tests -v
  python3 -m json.tool skill-package/interactionModels/custom/en-US.json > /dev/null
  bash -n scripts/deploy-lambda.sh
fi
if [[ "$mode" == container || "$mode" == all ]]; then
  docker build --tag home-energy-test .
  docker run --rm --entrypoint python home-energy-test -c 'from ask_sdk_webservice_support.verifier import RequestVerifier; RequestVerifier()'
  docker run --rm --volume "$PWD/tests:/tests:ro" --volume "$PWD/scripts:/scripts:ro" --entrypoint python home-energy-test -m unittest discover -s /tests -v
fi
if [[ "$mode" == worker || "$mode" == all ]]; then
  node --test deploy/worker-vpc/relay.test.mjs
fi
