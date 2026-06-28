#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy WireGuard agent to a target VM (PowerShell version).

.DESCRIPTION
    Steps:
      1. Copy agent Python files to VM (with CRLF -> LF fix)
      2. Create directory structure, move files + verify syntax
      3. Upgrade system packages (reboot VM if needed)
      4. Run install.py (wireguard-tools + agent config + systemd unit)

.PARAMETER VmIp
    Target VM IP address (required).

.PARAMETER SshUser
    SSH user (default: ubuntu).

.EXAMPLE
    .\deploy.ps1 192.168.1.104
    .\deploy.ps1 -VmIp 192.168.1.104 -SshUser root

.NOTES
    Prerequisites:
      - SSH key loaded in Windows ssh-agent (Start-Service ssh-agent; ssh-add <key>)
      - Target VM must have Python 3 installed
#>

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$VmIp,

    [Parameter(Position=1)]
    [string]$SshUser = "ubuntu"
)

$SshOpts = @("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10")
$AgentDir = "/opt/unitao"
$AgentPkgDir = "$AgentDir/domain/wireguard"
$SrcDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$Files = @(
    "wg_data.py"
    "wg_agent.py"
    "wg_config_builder.py"
    "wg_key_manager.py"
    "wg_agent_service.py"
    "install.py"
    "prep_image_for_commit.py"
)

Write-Host "=== WireGuard Agent Deploy ==="
Write-Host "  Target: ${SshUser}@${VmIp}"
Write-Host "  Source: ${SrcDir}"
Write-Host ""

# ── Step 1: Copy files to VM (with CRLF -> LF fix) ─────────────────────

Write-Host "[1/4] Copying agent files (CRLF -> LF) ..."
foreach ($f in $Files) {
    $srcPath = Join-Path $SrcDir $f
    # Read file, convert CRLF to LF, write to temp
    $content = (Get-Content -Path $srcPath -Raw -Encoding UTF8) -replace "`r`n", "`n"
    $tmpFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpFile, $content, [System.Text.UTF8Encoding]::new($false))
    # SCP the normalized file
    & scp.exe @SshOpts $tmpFile "${SshUser}@${VmIp}:/tmp/$f" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "SCP failed for $f" }
    Remove-Item $tmpFile
    Write-Host "  $f -> /tmp/$f"
}

# ── Step 2: Create directory structure, move files, verify syntax ───────

Write-Host ""
Write-Host "[2/4] Setting up directory structure ..."

# Create directories and move files
ssh @SshOpts "${SshUser}@${VmIp}" @'
sudo mkdir -p /opt/unitao/domain/wireguard
sudo touch /opt/unitao/domain/__init__.py
sudo touch /opt/unitao/domain/wireguard/__init__.py
for f in wg_data.py wg_agent.py wg_config_builder.py wg_key_manager.py wg_agent_service.py install.py prep_image_for_commit.py; do
    [ -f "/tmp/$f" ] && sudo mv "/tmp/$f" "/opt/unitao/domain/wireguard/$f"
done
sudo chmod +x /opt/unitao/domain/wireguard/*.py
echo "Files moved to /opt/unitao/domain/wireguard/"
'@

# Verify syntax
Write-Host "  Verifying Python syntax ..."
$allOk = $true
foreach ($f in $Files) {
    $remotePath = "/opt/unitao/domain/wireguard/$f"
    # Encode the check command as base64 to avoid shell quoting issues
    $pyCmd = "import py_compile; py_compile.compile('$remotePath', doraise=True); print('OK')"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pyCmd))
    $out = ssh @SshOpts "${SshUser}@${VmIp}" "echo $b64 | base64 -d | sudo python3" 2>&1
    if ($out -match "OK") {
        Write-Host "    $f OK"
    } else {
        Write-Host "  ERROR: $f" -ForegroundColor Red
        Write-Host "  $out"
        $allOk = $false
    }
}
if (-not $allOk) { throw "Syntax check failed" }
Write-Host "  All files OK."

# ── Step 3: System upgrade ───────────────────────────────────────────

Write-Host ""
Write-Host "[3/4] Upgrading system packages ..."
ssh @SshOpts "${SshUser}@${VmIp}" 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y'
$rebootNeeded = ssh @SshOpts "${SshUser}@${VmIp}" '[ -f /var/run/reboot-required ] && echo yes || echo no'
if ($rebootNeeded.Trim() -eq "yes") {
    Write-Host "  Reboot required, restarting VM ..."
    ssh @SshOpts "${SshUser}@${VmIp}" 'sudo reboot' 2>$null
    Write-Host "  Waiting for VM to come back ..."
    Start-Sleep -Seconds 10
    for ($i = 1; $i -le 30; $i++) {
        $ready = ssh @SshOpts "${SshUser}@${VmIp}" 'echo ready' 2>$null
        if ($ready -eq "ready") {
            Write-Host "  VM is back online."
            break
        }
        Write-Host "  ... $($i*5)s"
        Start-Sleep -Seconds 5
    }
} else {
    Write-Host "  No reboot required."
}

# ── Step 4: Run install.py ───────────────────────────────────────────

Write-Host ""
Write-Host "[4/4] Running install.py ..."
ssh @SshOpts "${SshUser}@${VmIp}" "sudo python3 ${AgentPkgDir}/install.py --network-config ${AgentDir}/wireguard_network.json"

Write-Host ""
Write-Host "=== Deploy complete ==="
Write-Host ""
Write-Host "  Next steps:"
Write-Host "    1. SSH into VM:   ssh ${SshUser}@${VmIp}"
Write-Host "    2. Start agent:   sudo systemctl start wg-agent"
Write-Host "    3. Check status:  sudo wg show wg0"
Write-Host "    4. Follow logs:   sudo journalctl -u wg-agent -f"
Write-Host ""
Write-Host "  Orchestrator: Post updated config via REST API:"
Write-Host "    POST /api/v1/vms/<name>/inventory"
Write-Host '    Body: {"name":"wireguard_network","timestamp":"...","data":{"network":{...}}}'
