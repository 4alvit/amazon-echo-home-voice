# Install the reviewed backend without enabling a skill

This directory provides a private k3s deployment. It creates no ingress, Cloudflare route, public load balancer, or Alexa skill. An absent or empty `ASK_SKILL_ID` keeps all `/alexa` requests rejected while `/health` remains available.

## Placement

The reviewed image was built in the Synology NAS Docker engine as `igw-alexa:voice-review`. The k3s node named `syn` is a separate Ubuntu VM at `192.168.175.130`; its containerd cannot use the NAS Docker image store automatically.

For a service that will eventually share the existing IGW/tunnel infrastructure, use `k3s.yaml` on node `syn`. For a temporary installation check, NAS Compose is simpler: its host port is already restricted to `127.0.0.1:8091`, and it needs no image transfer. Choose one placement; neither proves that a skill is enabled or that an Echo can speak.

## k3s installation

An operator must choose the authorized personal cluster context and SSH identity. These commands are instructions, not an automatic deployment script.

1. Confirm `KUBE_CONTEXT` refers to the intended home cluster and its node is named `syn`:

   ```bash
   : "${KUBE_CONTEXT:?Set the authorized home cluster context}"
   kubectl --context "$KUBE_CONTEXT" get node syn
   kubectl --context "$KUBE_CONTEXT" get namespace synology-apps
   ```

2. Transfer the reviewed image from the NAS Docker engine into the VM's k3s containerd. This streams image bytes without copying runtime secrets:

   ```bash
   : "${K3S_SSH_TARGET:?Set the SSH destination for the syn Ubuntu VM}"
   ssh -o BatchMode=yes synology \
     'sudo -n /var/packages/ContainerManager/target/usr/bin/docker image save igw-alexa:voice-review' \
     | ssh -o BatchMode=yes "$K3S_SSH_TARGET" \
       'sudo -n k3s ctr images import -'
   ```

   Verify the imported reference on that VM with `sudo -n k3s ctr images list`. The manifest uses `imagePullPolicy: Never`, so missing imports fail rather than pulling an unrelated public image. For later releases, use an immutable version tag or your private registry and update the manifest explicitly.

3. Create a separate Secret named `igw-alexa-env` in namespace `synology-apps` using your protected secret workflow. Required keys: `IGW_URL` and `IGW_READ_TOKEN`. Add both `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` when the outbound gateway uses Access. Omit `ASK_SKILL_ID` or set it to an empty value until developer registration and skill creation are finished. Do not use a placeholder ID or IGW admin token. Optional bounds are `IGW_TIMEOUT_SECONDS` and `IGW_MAX_AGE_SECONDS`.
4. Apply the workload only after the image and Secret exist:

   ```bash
   kubectl --context "$KUBE_CONTEXT" apply -f deploy/k3s.yaml
   kubectl --context "$KUBE_CONTEXT" -n synology-apps rollout status deployment/igw-alexa --timeout=180s
   kubectl --context "$KUBE_CONTEXT" -n synology-apps port-forward service/igw-alexa 18091:8080
   ```

5. From a second local shell, check health and fail-closed behavior:

   ```bash
   curl --fail http://127.0.0.1:18091/health
   curl --silent --output /dev/null --write-out '%{http_code}\n' \
     -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:18091/alexa
   ```

   Expected: health JSON includes `"skill_configured": false`; unsigned POST returns `400`. This verifies installation and rejection behavior only.

The workload uses a non-root user, a read-only filesystem, a bounded temporary volume, no Kubernetes API token, and only a ClusterIP Service. It needs outbound HTTPS for Amazon certificates and IGW. No inbound Cloudflare Access policy is added here.

After the real skill exists, update `ASK_SKILL_ID` in the Secret and restart the deployment so new environment values take effect. Configuring a public HTTPS route and the Alexa Developer Console endpoint is a separate operator action. Follow the main README's voice test checklist afterward.

## NAS-only alternative

For the reviewed image, copy `deploy/compose.nas.yaml` to `/volume1/docker/igw-alexa/compose.yaml` and place the protected `.env` alongside it with mode `600`. The manifest pins the local image tag, prohibits pulls, binds port 8091 to NAS loopback, and caps memory. It requests a lower CPU scheduling weight because Synology kernels may lack the CPU quota controller. A PID limit is requested where supported; the verified Synology host reports that its PID cgroup controller is unavailable and discards that limit. Do not treat it as enforced on that host. From that directory, run `docker compose up --no-build -d`. It creates only the dedicated `igw-alexa` container. Keep `ASK_SKILL_ID` empty until a real skill exists.

The repository-root `compose.yaml` is the build-from-source alternative and uses the same loopback binding. Do not run both manifests on the same host port.

Check `http://127.0.0.1:8091/health` from the NAS. A tunnel running inside the Ubuntu VM cannot reach the NAS loopback port; do not widen that binding just to make it reachable. Move the service to k3s or design a private connection explicitly when the real Alexa endpoint is ready.
