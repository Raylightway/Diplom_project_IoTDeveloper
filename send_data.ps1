# send_data.ps1 - Упрощенная версия

param(
    [int]$Count = 20
)

# Получаем API домен из Terraform
Write-Host "Getting API domain from Terraform..." -ForegroundColor Cyan
$ApiDomain = terraform output -raw api_gateway_domain

if (-not $ApiDomain) {
    Write-Host "ERROR: Could not get API domain from Terraform" -ForegroundColor Red
    Write-Host "Please run 'terraform output api_gateway_domain' manually" -ForegroundColor Yellow
    exit 1
}

Write-Host "API Domain: $ApiDomain" -ForegroundColor Green
Write-Host ""

Write-Host "=== Sending $Count test records ===" -ForegroundColor Yellow
Write-Host ""

$success = 0
$fail = 0

for ($i = 1; $i -le $Count; $i++) {
    $device = "device_" + (Get-Random -Minimum 1 -Maximum 5)
    $value = Get-Random -Minimum 20 -Maximum 80
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"
    
    $body = "{`"timestamp`":`"$timestamp`",`"value`":$value,`"device_id`":`"$device`"}"
    
    try {
        $response = Invoke-RestMethod -Uri "https://$ApiDomain/data" -Method Post -Body $body -ContentType "application/json" -ErrorAction Stop
        $success++
        Write-Host "  [$i] ✓ $device = $value" -ForegroundColor Green
    } catch {
        $fail++
        Write-Host "  [$i] ✗ $device = $value - ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
    
    Start-Sleep -Milliseconds 200
}

Write-Host ""
Write-Host "=== Results ===" -ForegroundColor Cyan
Write-Host "✅ Success: $success" -ForegroundColor Green
Write-Host "❌ Failed: $fail" -ForegroundColor Red
Write-Host ""
Write-Host "Total records sent: $success" -ForegroundColor Yellow
