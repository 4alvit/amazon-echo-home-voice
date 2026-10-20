#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-all}"
case "$mode" in syntax|unit|container|unified|worker|keycloak|terraform|all) ;; *) echo 'Usage: scripts/ci.sh [syntax|unit|container|unified|worker|keycloak|terraform|all]' >&2; exit 2 ;; esac
if [[ "$mode" == syntax || "$mode" == all ]]; then python3 scripts/validate-source.py; fi
if [[ "$mode" == unit || "$mode" == all ]]; then
  PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m unittest discover -s tests -v
  python3 -m json.tool skill-package/interactionModels/custom/en-US.json > /dev/null
  bash -n scripts/deploy-lambda.sh
fi
if [[ "$mode" == container || "$mode" == all ]]; then
  docker build --tag home-energy-test .
  docker run --rm --entrypoint python home-energy-test -c 'from ask_sdk_webservice_support.verifier import RequestVerifier; RequestVerifier()'
  docker run --rm --volume "$PWD/tests:/tests:ro" --volume "$PWD/scripts:/scripts:ro" --volume "$PWD/deploy/multi-household:/deploy/multi-household:ro" --volume "$PWD/deploy/test:/deploy/test:ro" --volume "$PWD/deploy/unified:/deploy/unified:ro" --entrypoint python home-energy-test -m unittest discover -s /tests -v
fi
if [[ "$mode" == unified || "$mode" == all ]]; then
  unified_revision="$(git rev-parse HEAD)"
  unified_image="home-energy-unified-test:${unified_revision}"
  docker build --file deploy/unified/Dockerfile \
    --build-arg "ALEXA_REVISION=${unified_revision}" --tag "$unified_image" .
  docker run --rm --network none --entrypoint python "$unified_image" -m pip check
  unified_fixtures="$(mktemp -d "$PWD/.unified-ci.XXXXXX")"
  trap 'rm -rf -- "$unified_fixtures"' EXIT
  # Exclude ignored private deployment files even when this mode runs locally.
  git ls-files --cached --others --exclude-standard -z -- \
    tests scripts deploy/multi-household deploy/test deploy/unified \
    | tar --null -T - -cf - | tar -xf - -C "$unified_fixtures"
  # The built Linux image exercises both dependency sets and subreaper semantics.
  # Only source fixtures are mounted; no Docker socket, credentials or LAN access.
  docker run --rm --network none --read-only \
    --cap-drop ALL --security-opt no-new-privileges:true \
    --memory 768m --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 \
    --volume "$unified_fixtures/tests:/tests:ro" \
    --volume "$unified_fixtures/scripts:/scripts:ro" \
    --volume "$unified_fixtures/deploy:/deploy:ro" \
    --entrypoint python "$unified_image" -m unittest discover -s /tests -v
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
