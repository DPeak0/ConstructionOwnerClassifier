param(
    [Parameter(Mandatory = $true)]
    [switch]$ConfirmFormalRelease,
    [string]$ReleaseNotes = "正式版本发布"
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmFormalRelease) {
    throw "Formal releases require -ConfirmFormalRelease."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is required."
}

gh auth status
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated."
}
gh workflow run release.yml --repo DPeak0/ConstructionOwnerClassifier -f "release_notes=$ReleaseNotes"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start the formal release workflow."
}
Write-Host "Formal release workflow started: https://github.com/DPeak0/ConstructionOwnerClassifier/actions"
