# Private backend deployment

These examples install the backend without creating a public ingress, Cloudflare route, load balancer, or Alexa skill. An absent or empty `ASK_SKILL_ID` keeps every `/alexa` request rejected while `/health` remains available.

## Choose the placement

Run the webhook where the chosen tunnel connector can reach it privately. A native connector on the Docker host can reach a loopback-published container port. A connector on another machine, inside an isolated container, or inside a Kubernetes VM cannot use that host's loopback address.

The repository-root `compose.yaml` builds the image from source and publishes only `127.0.0.1:8091`. Use the Kubernetes example when the connector runs in, or has a deliberate private route to, that cluster. Docker and Kubernetes container runtimes do not automatically share image stores, even on the same physical computer.

## Kubernetes installation

The example uses the generic namespace `home-energy` and does not select a particular node. It uses a locally built image with `imagePullPolicy: Never`. Import that image into every node eligible to run the Pod, or adapt the manifest to a private registry and an immutable image digest. For a local image present on only one node, add your own node placement constraint before applying. Do not commit real node names or other site identifiers to a public fork.

1. Choose the intended Kubernetes context and confirm access:

   ```bash
   : "${KUBE_CONTEXT:?Set the intended Kubernetes context}"
   kubectl --context "$KUBE_CONTEXT" get nodes
   kubectl --context "$KUBE_CONTEXT" create namespace home-energy --dry-run=client -o yaml \
     | kubectl --context "$KUBE_CONTEXT" apply -f -
   ```

2. Build the image from the repository root:

   ```bash
   docker compose build
   ```

   The image is `amazon-echo-home-voice:local`. For k3s, transfer it to each eligible node's containerd using your own SSH destination. Repeat this command for each destination that needs the image:

   ```bash
   : "${K3S_SSH_TARGET:?Set the destination for an eligible k3s node}"
   docker image save amazon-echo-home-voice:local \
     | ssh -o BatchMode=yes "$K3S_SSH_TARGET" 'sudo -n k3s ctr images import -'
   ```

   Verify the imported reference with `sudo -n k3s ctr images list` on each node. For other Kubernetes distributions, use their documented image import procedure. Missing images fail rather than pulling an unrelated public image.

3. Create a Secret named `igw-alexa-env` in namespace `home-energy` through your protected secret workflow. Required keys are `IGW_URL` and `IGW_READ_TOKEN`. Add both `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` when the outbound gateway uses Cloudflare Access. Omit `ASK_SKILL_ID` or leave it empty until your real skill exists; never substitute a placeholder ID or an IGW admin token. Optional bounds are `IGW_TIMEOUT_SECONDS` and `IGW_MAX_AGE_SECONDS`. Keep secret manifests and runtime environment files outside Git.
4. Apply the workload only after the image and Secret exist:

   ```bash
   kubectl --context "$KUBE_CONTEXT" apply -f deploy/k3s.yaml
   kubectl --context "$KUBE_CONTEXT" -n home-energy rollout status deployment/igw-alexa --timeout=180s
   kubectl --context "$KUBE_CONTEXT" -n home-energy port-forward service/igw-alexa 18091:8080
   ```

5. In a second shell, check process health and unsigned rejection:

   ```bash
   curl --fail http://127.0.0.1:18091/health
   curl --silent --output /dev/null --write-out '%{http_code}\n' \
     -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:18091/alexa
   ```

   Without a configured skill, health includes `"skill_configured": false`; unsigned POST returns `400`. These checks do not prove that a real Alexa invocation succeeds.

The workload runs as a non-root user with a read-only filesystem, bounded temporary storage, no Kubernetes API token, and a ClusterIP Service. Outbound HTTPS must reach Amazon's certificate service and IGW. If you rename the namespace, update both resources and all installation commands consistently.

After creating the actual skill, update `ASK_SKILL_ID` in the Secret and restart the deployment so it reads the new environment. Public routing and the Developer Console endpoint are separate steps; follow the main README's Plan A or Plan B and voice test checklist.

## NAS Compose alternative

For a NAS without a source-build workflow, use `deploy/compose.nas.yaml` with an image you have built and reviewed. Check the manifest's image reference and build or import that exact tag into the NAS Docker engine before starting it. Copy the manifest to a private deployment directory of your choice as `compose.yaml`, and place the runtime `.env` alongside it with mode `600`. The manifest prohibits pulls, publishes only NAS loopback port 8091, and caps memory.

The NAS example requests a lower CPU scheduling weight because some NAS kernels lack CPU quota support. A PID limit is requested where supported; inspect your own Docker warnings and cgroup capabilities before relying on its enforcement. These capabilities vary by host.

From the chosen deployment directory, run `docker compose up --no-build -d`. Keep `ASK_SKILL_ID` empty until a real skill exists. Do not run both Compose examples on the same host port.

Check `http://127.0.0.1:8091/health` from the Docker host. Keep the loopback binding; design the connector placement or a private connection deliberately instead of publishing the port on every interface. For a native connector in the same host network namespace, follow [the exact HTTPS route procedure](tunnel-routing.md) or the [Workers VPC relay procedure](worker-vpc/README.md).
