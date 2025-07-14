# This script is designed to be run by a GitHub Action.
# It automatically updates the manifest.json file.

$manifestFile = "manifest.json"

# --- Get the commit message from the push that triggered this action ---
# This will be used as the "notes" for the new version.
$commitMessage = (git log -1 --pretty=%B).Trim()
Write-Host "Triggering commit message: $commitMessage"

# --- Read the manifest file ---
if (-not (Test-Path $manifestFile)) {
    Write-Error "manifest.json not found!"
    exit 1
}
$manifest = Get-Content $manifestFile | ConvertFrom-Json

# --- Calculate new file hashes ---
$newRulesHash = (Get-FileHash db/rules.json).Hash
$newReplacementsHash = (Get-FileHash db/replacements.json).Hash
$newDbHash = (Get-FileHash db/db.json).Hash

# --- Check if an update is actually needed ---
# This prevents empty commits if, for example, two PRs are merged quickly.
if (($manifest.files.rules.sha -eq $newRulesHash) -and `
    ($manifest.files.replacements.sha -eq $newReplacementsHash) -and `
    ($manifest.files.dictionary.sha -eq $newDbHash)) {
    Write-Host "No data file changes detected. Exiting."
    exit 0
}

# --- Data has changed, proceed with update ---
Write-Host "Data files have changed. Updating manifest..."

# Update hashes
$manifest.files.rules.sha = $newRulesHash
$manifest.files.replacements.sha = $newReplacementsHash
$manifest.files.dictionary.sha = $newDbHash

# Update notes with the latest commit message
$manifest.notes = $commitMessage

# Increment the version number (e.g., 1.2.1 -> 1.2.2)
$versionParts = $manifest.version.Split('.')
$versionParts[2] = [int]$versionParts[2] + 1
$manifest.version = $versionParts -join '.'

# --- Save the updated manifest file ---
$manifest | ConvertTo-Json -Depth 10 | Set-Content $manifestFile

Write-Host "Manifest successfully updated to version $($manifest.version)"