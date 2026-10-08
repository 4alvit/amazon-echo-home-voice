# TLS certificate key policy

Owned TLS contexts retain CA and hostname verification and inspect the actual
verified chain, including its selected trust anchor, before application data.
RSA moduli must contain at least 2048 significant bits; EC keys need at least
224 bits; DSA requires p >= 2048 and q >= 224. Ed25519 and Ed448 are accepted.
Unknown algorithms or runtimes without an accessible verified chain fail closed.
OpenSSL security level 2 alone can accept a 2047-bit RSA modulus.

CPython 3.11 and 3.12 use the private `_sslobj.get_verified_chain` interface;
newer CPython versions may expose the public equivalent. This runtime contract
is tested rather than inferred from the Python version. Alternative Python
implementations are not implicitly supported.

The public-key decoder uses cryptography except on Intel macOS, where the
native Security framework reads key metadata without changing trust decisions.
The Intel backend accepts RSA and EC only. No system trust store is modified.

The TLS policy and Darwin metadata decoder are adapted from the MIT-licensed
victron-venus/inverter-dashboard implementation, copyright 2026 victron-venus.
The project MIT license also applies to these adaptations. This policy does
not establish the strength of inbound TLS terminators or unrelated transports.

## Voice adapter deployment

The pinned public gateway connection, personal gateway opener, OAuth opener,
Amazon certificate downloader and Cloudflare route-planning reader all use
owned contexts. Run `scripts/prepare-tunnel-route.py` with the Python environment
where this project is installed, as for the CLI; it now imports the shared
policy. These clients retain their existing hostname, URL, redirect, proxy and
deadline policies. No process-wide TLS hooks are installed.

The CLI and personal Lambda adapter now require cryptography on their Linux
runtime. The Lambda `src/requirements.txt` is generated from the core dependency
set with hashes. `deploy-lambda.sh` builds in the pinned Linux/amd64 SAM Python
3.12 image; Docker and AWS SAM CLI are required for that existing deploy entry
point. The build does not itself deploy. CI runs a separate build-only container
without AWS credentials, imports the packaged handler and runs real loopback
TLS cases from the resulting package. Do not reuse old dependency-free Lambda
ZIP files with these sources.

The TLS tests use synthetic local endpoints to exercise the private pinned
connection and request functions. They do not bypass public-host admission in
production or contact an actual household, identity provider or Amazon service.
The public relay/Cloudflare and AWS inbound TLS configurations are separate
operator/provider responsibilities. This change is not a project-wide OpenSSF
attestation.


The pinned Linux SAM image cannot decode the test-only EC192 public root key.
The suite independently verifies that fixture's issuer and signatures, confirms
native key decoding fails while RSA2048/EC256 controls succeed, and still
requires every product client to reject it before application bytes. This is
native-provider rejection evidence, not a successful EC192 handshake or a
claim that the post-handshake guard ran. All supported fixture chains retain
the successful low-strength oracle requirement; no cases are skipped.
