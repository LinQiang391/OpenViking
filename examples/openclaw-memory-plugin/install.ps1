param(
  [string]$Ref = $env:OV_MEMORY_VERSION
)

$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) {
  Write-Host "[openviking-installer] $Message"
}

function Fail([string]$Message) {
  throw "[openviking-installer] ERROR: $Message"
}

function Require-Command([string]$Command) {
  if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
    Fail "Missing required command: $Command"
  }
}

if ($args -contains '-h' -or $args -contains '--help') {
@"
OpenViking Memory Installer for OpenClaw (Windows)

Usage:
  iwr https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/ocm@<version>/examples/openclaw-memory-plugin/install.ps1 -UseBasicParsing | iex

Environment:
  OV_MEMORY_VERSION      Override git ref used to download setup helper
  OV_MEMORY_REPO         Override GitHub repo (default: volcengine/OpenViking)
  OV_MEMORY_DEFAULT_REF  Fallback ref when OV_MEMORY_VERSION is not set (default: main)
  OPENVIKING_GITHUB_RAW  Override raw base URL used by helper and installer
  SKIP_CHECKSUM=1        Skip SHA256 checksum verification

All trailing arguments are forwarded to the setup helper.
"@ | Write-Host
  exit 0
}

Require-Command node
if (-not (Get-Command openclaw -ErrorAction SilentlyContinue)) {
  Fail 'OpenClaw is not installed. Install it first: npm install -g openclaw'
}

$repo = if ($env:OV_MEMORY_REPO) { $env:OV_MEMORY_REPO } else { 'volcengine/OpenViking' }
$defaultRef = if ($env:OV_MEMORY_DEFAULT_REF) { $env:OV_MEMORY_DEFAULT_REF } else { 'main' }
$resolvedRef = if ($Ref) { $Ref } elseif ($env:OV_MEMORY_VERSION) { $env:OV_MEMORY_VERSION } else { $defaultRef }
if (-not $resolvedRef) {
  Fail 'Resolved ref is empty; set OV_MEMORY_VERSION.'
}
if (-not $env:OV_MEMORY_VERSION -and -not $PSBoundParameters.ContainsKey('Ref')) {
  Write-Info "OV_MEMORY_VERSION is not set; using default ref: $resolvedRef"
}

if ($env:OPENVIKING_GITHUB_RAW) {
  $rawBase = $env:OPENVIKING_GITHUB_RAW
} else {
  $rawBase = "https://raw.githubusercontent.com/$repo/$resolvedRef"
  $env:OPENVIKING_GITHUB_RAW = $rawBase
}

$helperRel = 'examples/openclaw-memory-plugin/setup-helper/cli.js'
$checksumRel = "$helperRel.sha256"
$helperUrl = "$rawBase/$helperRel"
$checksumUrl = "$rawBase/$checksumRel"
$skipChecksum = $env:SKIP_CHECKSUM -eq '1'

$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("openviking-installer-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpDir | Out-Null

try {
  $helperPath = Join-Path $tmpDir 'cli.js'
  $checksumPath = Join-Path $tmpDir 'cli.js.sha256'
  $pkgPath = Join-Path $tmpDir 'package.json'

  Write-Info "Using ref: $resolvedRef"
  Write-Info 'Downloading setup helper...'
  Invoke-WebRequest -Uri $helperUrl -OutFile $helperPath -UseBasicParsing

  if (-not $skipChecksum) {
    Write-Info 'Downloading checksum...'
    Invoke-WebRequest -Uri $checksumUrl -OutFile $checksumPath -UseBasicParsing
    $expected = (Get-Content $checksumPath -Raw).Trim().Split()[0].ToLowerInvariant()
    if (-not $expected) {
      Fail "Checksum file is empty: $checksumUrl"
    }
    $actual = (Get-FileHash -Algorithm SHA256 $helperPath).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
      Fail 'Checksum mismatch for setup helper'
    }
    Write-Info 'Checksum verification passed'
  } else {
    Write-Info 'Skipping checksum verification (SKIP_CHECKSUM=1)'
  }

  Set-Content -Path $pkgPath -Value '{"type":"module"}' -Encoding ASCII
  Write-Info 'Running setup helper...'
  & node $helperPath @args
}
finally {
  Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}
