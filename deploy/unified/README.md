# One application container for personal Alexa and Cast

This optional image runs the existing personal Alexa webhook and the existing
Cast report service together. It replaces two application containers with one;
it does not change either application's request authentication, add Android/ADB
privileges, or embed an identity provider or database.

Exactly one Cast process owns report jobs, busy state and temporary media URLs.
A separate Gunicorn master runs two Alexa workers. A Python PID 1 starts both
with separate environment dictionaries, forwards shutdown to their process
groups, kills stragglers after the grace period, and reaps adopted descendants
on Linux. If either required child exits, the container exits with failure after
cleaning up its peer. Docker's restart policy restarts the complete application.

Personal Alexa is the only supported Alexa mode in this image. Multi-household
configuration and unknown keys are rejected. Retain the separate acceptance
environment and its encrypted storage until its own retirement/migration is
approved. This package does not manage it.

## Pinned sources and dependencies

- Alexa comes from the reviewed repository checkout used as the build context.
  Pass its commit to the `ALEXA_REVISION` build argument.
- Cast comes from commit
  `9b5d8bc87fe8ca776255bb5a11b55521d3d8221f` of
  [google-home-voice-stats](https://github.com/4alvit/google-home-voice-stats/tree/9b5d8bc87fe8ca776255bb5a11b55521d3d8221f).
  `source-pin.json` pins the archive URL and SHA-256; `fetch_source.py` verifies
  the download before extracting or installing it. Its MIT license is retained
  in the image.
- `requirements.lock` resolves the two applications together and hashes every
  Python artifact. It preserves the repository's Alexa version constraints and
  adds Cast's Pillow/PyChromecast dependencies. Installation ends with `pip check`
  and real imports of both applications.
- Python is pinned by image digest. Debian packages receive the configured
  distribution's current security updates; this is not a bit-for-bit reproducible
  OS image. Publish and deploy the resulting image digest.

The observed older Cast image reported a September source revision that did not
contain its subsequently added Kitten/TLS behavior. Reusing that label as the
complete source would lose functionality. The pinned version above explicitly
contains Kitten failover endpoints and outbound HTTPS verification with a supplied
CA. No inbound TLS listener or relay is required for this transport.

External Kitten and local espeak are supported. Optional on-device Piper models
and its Python extra are not included. Speech inference remains in its existing
service; this image does not train or replace voices.

Build on a capable Linux builder from the repository root:

```sh
docker build --file deploy/unified/Dockerfile \
  --build-arg ALEXA_REVISION="$(git rev-parse HEAD)" \
  --tag igw-voice:reviewed .
docker run --rm --entrypoint python igw-voice:reviewed -m pip check
```

For a NAS with a different CPU architecture, build for that target, for example
`docker buildx build --platform linux/amd64 --load ...`. Avoid a heavy build on a
NAS already under load. Rebuild the dependency lock only as a reviewed change:

```sh
uv pip compile deploy/unified/requirements.in --generate-hashes --universal \
  --python-version 3.14 --no-strip-extras \
  --output-file deploy/unified/requirements.lock
```

## Private runtime configuration

`runtime.env.example` contains Compose interpolation settings only. Copy its
structure to a private operator directory. Put the actual application settings
in **separate** `alexa.env` and `cast.env` files there, using the two corresponding
examples as a key reference. Preserve the current tokens, skill ID, gateway URLs,
Cast target, media URL and speech settings rather than inventing replacements.
Merge an existing Cast `.env` and speech `kitten.env` privately, producing one
literal entry per key. Duplicate or unrecognized entries fail closed.

Files use literal `KEY=value` lines: no `export`, quotes, command substitutions
or shell expansion. Blank lines and comment lines are allowed. Do not source
these files in a shell or put them into build arguments, the repository or logs.
Use a private directory and files readable by container UID/GID `10001:10001`
(for example directory mode 0700 and files 0600 owned by that UID on Linux).
Mount only this dedicated directory, not the entire operator secret store.

Mount the existing speech CA directory read-only at `/run/speech-ca`; preserve
the actual basename in `CAST_KITTEN_CA_FILE`. An empty dedicated directory can
be mounted for deployments using espeak. CA certificates are trust material,
not server private keys; do not mount unrelated private TLS keys.

The supervisor inherits no arbitrary parent environment into the children.
Each app receives only the allowlisted keys from its own file plus a minimal
system environment. In particular, Gunicorn configuration overrides, proxy
variables, Python path injection and the other app's credentials are excluded.
Processes still share one UID/container and mounted files: this reduces accidental
cross-configuration, **not** the security impact of a compromised application.

## Listener and readiness contract

- Alexa binds an explicit IPv4 loopback address, default `127.0.0.1:8091`.
  The public tunnel continues to expose only its existing signed `/alexa` path.
- Cast binds the explicit RFC1918 NAS LAN address in `CAST_BIND_HOST`, typically
  on port 8091. `CAST_MEDIA_BASE_URL` must use exactly the same IP and port.
- Host networking lets these two listeners share a port on different IPs.
  Never use `0.0.0.0`; do not publish the Cast listener through the Alexa tunnel.
- Existing standalone listeners must be stopped before taking over their
  addresses. A preflight bind fails if any requested address is already occupied.
- Health probes request only `/health` and `/healthz`, with no credentials,
  redirects or proxy settings. Readiness requires both enabled applications.
- Default probe timeout is 3 seconds. A brief failed probe changes readiness
  but does not kill an otherwise running service. Continuous failure for
  60 seconds after successful startup fails the container. Initial readiness
  has a 90-second deadline. All values are configurable in Compose.
- Docker reads a small local readiness snapshot, rather than starting another
  network probe. Its 15-second exec timeout allows for slow NAS process startup.
  The snapshot expires after 30 seconds. This proves process/listener health,
  not gateway freshness, working Amazon OAuth, audible playback or Cast reachability.

The Compose file uses features available in Docker Compose 2.20.1 and requires
no `include`, newer `env_file` formats, Docker socket or privileged mode. It
mounts a read-only root filesystem and a bounded tmpfs for report generation.
The 45-second container stop grace exceeds the supervisor's default 12-second
child grace plus bounded probe/cleanup delays.

## Stage without interrupting or playing anything

1. Keep the existing two containers, their source files and image digests.
   Record current routes, exact bind addresses, mounts and configuration hashes
   privately. Do not alter any identity database.
2. Build the image and run the checks below on a separate builder.
3. Prepare a separate private stage configuration directory. Select unused
   high ports: change `UNIFIED_ALEXA_PORT`, `CAST_PORT` **and**
   `CAST_MEDIA_BASE_URL` together. Keep the Cast LAN IP and credentials. Use
   a separate Compose project such as `igw-voice-stage`. The process performs
   no playback on startup; do not invoke the report endpoint during a silent test.
4. Validate configuration without emitting rendered secret content:

   ```sh
   docker compose --env-file /private/stage/runtime.env \
     --file deploy/unified/compose.yaml --project-name igw-voice-stage config --quiet
   docker compose --env-file /private/stage/runtime.env \
     --file deploy/unified/compose.yaml --project-name igw-voice-stage up -d
   ```

5. Check Docker health and the local `/health` and `/healthz` endpoints.
   An unsigned POST to `/alexa` must be rejected. An unauthenticated Cast
   `/v1/status` must return 401. Do not treat these as a signed Amazon or audio
   acceptance test. Confirm the primary services are still healthy.
6. Stop and remove only the staging project before the actual cutover.
   It has no persistent application database. Never use a wildcard container
   stop/prune command.

## Cutover and rollback

Use an approved maintenance window and the verified image digest. Stop the two
old application services, preserving their containers/images/configuration,
then start this project with the **original** listener addresses and ports.
Routes and callers do not need to move when the exact socket endpoints remain.
Verify signed Alexa traffic and a deliberately requested report on the intended
Cast device. Validate speech failover/TLS, media retrieval and Alexa latency while
a report is rendering. The Cast state store remains process-local and in-flight
report URLs are lost on restart, just as for the standalone service.

To roll back, stop this exact project first, then start the original two services
with their saved Compose files and images. Confirm there is exactly one Cast
owner and one listener on each original endpoint. Restore old restart policies
if the deployment procedure changed them. No database restore should be needed
for this personal-mode consolidation.

If automatic host startup is required, keep exactly one desired-state owner
(manual Compose or an explicitly adopted Portainer/Terraform stack). Merely
adding another stack must not leave both the old and new services scheduled
to claim the same ports after reboot.

## Tests and limits

```sh
python3 -m unittest discover -s tests -p 'test_unified*.py' -v
bash scripts/ci.sh syntax
```

The tests use actual local child processes and HTTP listeners to verify startup
readiness, peer termination after a child exits even with code zero, transient
health failures, startup deadlines and bounded cleanup. Linux additionally tests
an uncooperative child and grandchild being killed and reaped. Unit tests cover
credential scoping, mode/bind rejection, occupied listeners and stale readiness.

Those process fixtures do not implement a real Cast device or Amazon signing
service. Existing application suites still own request authentication, media
range/expiry and gateway behavior. A successful build and fixture suite do not
prove audible playback or response latency on the NAS; the staged and physical
acceptance checks remain necessary.
