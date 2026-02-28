# OpenClaw Memory Installer Release Guide

This document describes the manual release process for OpenClaw OpenViking memory one-click installers.

## Scope

Release artifact tag prefix: `ocm@x.y.z`

Files covered by this release flow:

- `examples/openclaw-memory-plugin/install.sh`
- `examples/openclaw-memory-plugin/install.ps1`
- `examples/openclaw-memory-plugin/setup-helper/cli.js`
- `examples/openclaw-memory-plugin/setup-helper/cli.js.sha256`

## Release Steps

1. Update helper code and docs as needed.
2. Regenerate checksum file:

```bash
cd examples/openclaw-memory-plugin/setup-helper
shasum -a 256 cli.js | awk '{print tolower($1) "  cli.js"}' > cli.js.sha256
```

3. Verify checksum locally:

```bash
cd examples/openclaw-memory-plugin/setup-helper
EXPECTED="$(awk 'NR==1 {print $1}' cli.js.sha256)"
ACTUAL="$(shasum -a 256 cli.js | awk '{print tolower($1)}')"
test "$EXPECTED" = "$ACTUAL"
```

4. Commit all changes.
5. Create and push release tag:

```bash
git tag ocm@x.y.z
git push origin ocm@x.y.z
```

6. Publish GitHub release notes and include one-click commands:

```bash
# Linux/macOS
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@x.y.z/examples/openclaw-memory-plugin/install.sh | OV_MEMORY_VERSION=ocm@x.y.z bash

# Windows PowerShell
$env:OV_MEMORY_VERSION='ocm@x.y.z'; iwr https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@x.y.z/examples/openclaw-memory-plugin/install.ps1 -UseBasicParsing | iex
```

## Notes

- `SKIP_CHECKSUM=1` is available for troubleshooting only; keep checksum enabled by default.
- `OV_MEMORY_VERSION` must match the tag when using `install.sh` via pipe to guarantee helper/version consistency.
