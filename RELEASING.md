# Releasing

## Release-candidate fuzz gate

Public RCs run the shared, bounded fuzz gate declared in `.github/fuzz.json`.
A failing campaign blocks that RC; fix and reissue an RC rather than bypassing it.
Beta, pull-request, push and scheduled workflows do not start fuzz campaigns.
Stable promotion uses the accepted RC source and evidence. See
[the full fuzzing guide](docs/fuzzing.md) for target scope, isolation, receipts and reproduction.
