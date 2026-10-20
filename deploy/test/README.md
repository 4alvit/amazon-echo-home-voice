# Opt-in multi-household test deployment

Use this for a deliberately started development deployment, separate from the
working personal Alexa service. It reuses the production definitions in
[`../multi-household/compose.yaml`](../multi-household/compose.yaml), including
their pinned images, application commands, authentication and database setup.
It does not change the production template or start any service automatically.

Requires Docker Compose **2.24.4 or newer**. The `!override` port lists replace
the inherited production bindings, rather than exposing both sets of ports.
Check `docker compose version` on the intended host before using this file;
older NAS packages may not support `!override`. Do not remove that tag to make
an older parser accept the file, because that would also expose the inherited
production ports.
All four long-running services have profile `test` and `restart: "no"`.
The initializer has only profile `setup` and also never restarts automatically.
This is a persistent test deployment, not the disposable synthetic integration
test in `tests/integration/keycloak_smoke.py`.

## Prepare isolated configuration

From this directory, run the production configuration generator with an
explicit output directory:

```sh
python3 ../multi-household/prepare_config.py --output-dir .
```

Follow the [multi-household preparation guide](../multi-household/README.md),
using a separate development skill, identity origin and portal origin. Run the
realm generator from **this directory** so `.env`, `private/alexa-redirects.txt`
and `private/home-energy-realm.json` belong to the test deployment:

```sh
python3 ../multi-household/generate_realm.py --env-file .env \
  --redirects-file private/alexa-redirects.txt \
  --output private/home-energy-realm.json
```

Prepare `private/data` and the realm import ownership as described in that
guide. The application shares a test-local SQLite file in `private/data`.
PostgreSQL uses a project-scoped named volume. Never point this deployment at
the production SQLite directory or reuse a production identity database.

Inherited build paths resolve to the repository root. The app uses its own
`home-energy-test:local` image tag, so a test build does not replace the
production template's local image tag. The overridden bind
mounts resolve to **`deploy/test/private`**, independent of the shell's current
directory. The helper always supplies the same absolute Compose path, project
directory and environment file. An alternate `--env-file` changes secrets and
configuration only; it does **not** relocate the bind-mounted data directory.
Do not run two project names concurrently against the same test data directory.

Only loopback test ports are published: identity `28085`, Alexa `28091`, portal
`28092`. These differ from the existing acceptance stack's 18085/18091/18092
bindings, so the two deployments can coexist without that port collision.
Check for other host services using the new ports before starting the stack.
Public TLS routes, callback URLs and skill endpoints must explicitly
target this test deployment. PostgreSQL has no published port. The helper does
not publish routes, create accounts, edit the working skill or change production.

## Explicit lifecycle

From the repository root:

```sh
python3 scripts/test-stack.py             # Help only; no Docker action
python3 scripts/test-stack.py check
python3 scripts/test-stack.py initialize   # Once, for a new test SQLite database
python3 scripts/test-stack.py up
python3 scripts/test-stack.py status
python3 scripts/test-stack.py stop
```

`check` renders and validates Compose without contacting the Docker daemon or
printing resolved secrets. `initialize` explicitly runs only the one-shot
database initialization job; `up` never silently initializes or replaces data.
`up` explicitly enables the `test` profile. `stop` preserves containers, SQLite,
realm imports and named volumes. There is deliberately no `down --volumes`
operation in the helper. Stop the stack when the development session ends;
profiles and `restart: "no"` do not impose a timeout on a running session.

The default project is `home-energy-test`. The helper only accepts that name or
`home-energy-test-<suffix>` to avoid selecting an existing production project.
Use the same `--project-name` and `--env-file` for every lifecycle command. Shell
`COMPOSE_*`, `ASK_*`, OAuth and other application variables are not inherited by
the Compose child: the explicitly selected environment file is authoritative.
Docker context/connection variables are preserved, so verify your selected
Docker context before an action that starts or stops containers.

## Existing test stacks need a deliberate migration

Adding this file does **not** change existing containers, restart policies,
project names, routes or data. A `restart: unless-stopped` container from the old
Compose project retains that policy until it is explicitly reconfigured.
Do not run this helper expecting it to stop an existing `igw-alexa-test` project.

First identify that project's exact Compose owner and dependent skill/routes,
take consistent SQLite and PostgreSQL backups, preserve the SQLite encryption
key, and confirm whether accounts or connections must be retained. Then stop
the old project through its original owner without deleting volumes. Either
keep it stopped or migrate its state and routes to the new isolated test
deployment after validating restoration. Keep the old configuration available
for rollback; importing a realm JSON is not a backup of its live users.

## Source-only validation

```sh
python3 -m unittest discover -s tests -p test_test_stack.py -v
```

The tests render Compose with synthetic environment values and inspect the
inherited commands, paths, ports, profiles, restart policies and isolation.
They never start containers or use live credentials. Render tests skip when
Docker Compose is unavailable; run them with a supported plugin before merging.
