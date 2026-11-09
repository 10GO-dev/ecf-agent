# ==========================================
# ECF Agent Installer Script (Windows)
# ==========================================
# Instala y configura el Agente ECF como servicio de Windows usando NSSM.
# Requiere ejecutar como Administrador.

[CmdletBinding()]
param (
    [string]$InstanceName = "",
    [string]$InstallPath = "",
    [string]$ServiceName = ""
)

# 1. Verificar privilegios de Administrador
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")
if (-not $isAdmin) {
    Write-Error "Este script requiere permisos de Administrador. Abra PowerShell como Administrador e intente nuevamente."
    exit 1
}

# 2. Determinar rutas y nombre de servicio
if ($InstanceName -and -not $ServiceName) {
    $ServiceName = "TGECFAgent-$InstanceName"
} elseif (-not $ServiceName) {
    $ServiceName = "TGECFAgent"
}

if ($InstallPath) {
    $InstallationPath = $InstallPath
} elseif ($InstanceName) {
    $InstallationPath = "C:\TekServices\ECF-Agent-$InstanceName"
} else {
    $InstallationPath = "C:\TekServices\ECF-Agent"
}

$SourceDir = $PSScriptRoot
if (-not $SourceDir) {
    $SourceDir = (Get-Location).Path
}

Write-Host "Iniciando instalación del Agente ECF en Windows..." -ForegroundColor Cyan
Write-Host "Directorio de destino: $InstallationPath"
Write-Host "Nombre del servicio:   $ServiceName"

# 3. Preparar directorios de destino
New-Item -ItemType Directory -Force -Path $InstallationPath | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallationPath "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallationPath "data") | Out-Null

# 4. Copiar ejecutable
$SourceExe = Join-Path $SourceDir "ecf-agent.exe"
$TargetExe = Join-Path $InstallationPath "ecf-agent.exe"

if (Test-Path $SourceExe) {
    Copy-Item -Path $SourceExe -Destination $TargetExe -Force
    Write-Host "Ejecutable copiado a $TargetExe" -ForegroundColor Green
} elseif (-not (Test-Path $TargetExe)) {
    Write-Error "No se encontró 'ecf-agent.exe' en '$SourceDir' ni en '$InstallationPath'."
    exit 1
}

# 5. Copiar archivo de configuración / variables de entorno
$TargetEnv = Join-Path $InstallationPath ".env"
$SourceEnv = Join-Path $SourceDir ".env"
$SourceEnvExample = Join-Path $SourceDir ".env.example"

if (-not (Test-Path $TargetEnv)) {
    if (Test-Path $SourceEnv) {
        Copy-Item -Path $SourceEnv -Destination $TargetEnv -Force
        Write-Host "Archivo .env copiado a $TargetEnv" -ForegroundColor Green
    } elseif (Test-Path $SourceEnvExample) {
        Copy-Item -Path $SourceEnvExample -Destination $TargetEnv -Force
        Write-Host "Plantilla .env creada en $TargetEnv" -ForegroundColor Yellow
    }
}

$SourceConfig = Join-Path $SourceDir "config.yaml"
$SourceConfigExample = Join-Path $SourceDir "config.example.yaml"
$TargetConfig = Join-Path $InstallationPath "config.yaml"

if (-not (Test-Path $TargetConfig)) {
    if (Test-Path $SourceConfig) {
        Copy-Item -Path $SourceConfig -Destination $TargetConfig -Force
    } elseif (Test-Path $SourceConfigExample) {
        Copy-Item -Path $SourceConfigExample -Destination (Join-Path $InstallationPath "config.example.yaml") -Force
    }
}

# 6. Gestionar NSSM (Non-Sucking Service Manager)
$NssmPath = Join-Path $InstallationPath "nssm.exe"
$LocalNssm = Join-Path $SourceDir "nssm.exe"
$ToolsNssm = Join-Path $SourceDir "tools\nssm.exe"

if (Test-Path $LocalNssm) {
    Copy-Item -Path $LocalNssm -Destination $NssmPath -Force
    Write-Host "NSSM copiado desde carpeta local a $NssmPath" -ForegroundColor Green
} elseif (Test-Path $ToolsNssm) {
    Copy-Item -Path $ToolsNssm -Destination $NssmPath -Force
    Write-Host "NSSM copiado desde tools\ a $NssmPath" -ForegroundColor Green
} elseif (-not (Test-Path $NssmPath)) {
    Write-Host "Descargando NSSM..." -ForegroundColor Cyan
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
    $ZipPath = Join-Path $env:TEMP "nssm_pkg.zip"
    $ExtractDir = Join-Path $env:TEMP "nssm_extracted"
    try {
        $NssmUrl = "https://community.chocolatey.org/api/v2/package/NSSM/2.24.101"
        Invoke-WebRequest -Uri $NssmUrl -OutFile $ZipPath -TimeoutSec 30
        Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force
        $InnerZip = Join-Path $ExtractDir "tools\nssm-2.24-101-g897c7ad.zip"
        if (Test-Path $InnerZip) {
            $InnerExtract = Join-Path $env:TEMP "nssm_inner"
            Expand-Archive -Path $InnerZip -DestinationPath $InnerExtract -Force
            $NssmBinary = Join-Path $InnerExtract "nssm-2.24-101-g897c7ad\win64\nssm.exe"
            if (Test-Path $NssmBinary) {
                Copy-Item $NssmBinary -Destination $NssmPath -Force
                Write-Host "NSSM instalado correctamente en $NssmPath" -ForegroundColor Green
            }
            Remove-Item -Path $InnerExtract -Recurse -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Warning "No se pudo descargar automáticamente NSSM: $_"
        Write-Warning "Puede descargar nssm.exe manualmente y colocarlo en: $NssmPath"
    } finally {
        Remove-Item -Path $ZipPath -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $NssmPath)) {
    Write-Error "No se pudo inicializar NSSM. No es posible registrar el servicio de Windows."
    exit 1
}

# 7. Instalar y configurar servicio en NSSM
Write-Host "Configurando servicio $ServiceName..." -ForegroundColor Cyan
& $NssmPath stop $ServiceName 2>$null
& $NssmPath remove $ServiceName confirm 2>$null

& $NssmPath install $ServiceName "$TargetExe" "run"
& $NssmPath set $ServiceName AppParameters "run"
& $NssmPath set $ServiceName AppDirectory "$InstallationPath"
& $NssmPath set $ServiceName Description "Agente de Recolección de Comprobantes Fiscales Electrónicos - TekServices"
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout "$InstallationPath\logs\service.log"
& $NssmPath set $ServiceName AppStderr "$InstallationPath\logs\service.log"
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateOnline 1
& $NssmPath set $ServiceName AppRotateSeconds 86400
& $NssmPath set $ServiceName AppRotateBytes 5242880
& $NssmPath set $ServiceName AppRestartDelay 10000

# 8. Validar binario y estado
$Version = "desconocida"
try {
    $VersionOutput = & "$TargetExe" --version 2>$null
    if ($VersionOutput) {
        $Version = $VersionOutput.Trim()
    }
} catch {}

$HasValidConfig = $false
if (Test-Path $TargetEnv) {
    $EnvContent = Get-Content -Path $TargetEnv -Raw
    if ($EnvContent -and ($EnvContent -notmatch "your_api_key_here")) {
        $HasValidConfig = $true
    }
}

if ($HasValidConfig) {
    Write-Host "Iniciando servicio $ServiceName..." -ForegroundColor Green
    & $NssmPath start $ServiceName
} else {
    Write-Host "Servicio registrado pero NO iniciado (requiere configurar credenciales en .env)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "INSTALACIÓN COMPLETADA EXITOSAMENTE (WINDOWS)" -ForegroundColor Cyan
Write-Host "Servicio:   $ServiceName"
Write-Host "Versión:    $Version"
Write-Host "Directorio: $InstallationPath"
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Pasos siguientes:"
Write-Host "1. Configure sus credenciales en: $TargetEnv"
Write-Host "2. Valide su conexión a BD y API:"
Write-Host "   & '$TargetExe' validate"
Write-Host "3. Inicie el servicio de Windows:"
Write-Host "   & '$NssmPath' start $ServiceName  (o Start-Service $ServiceName)"
Write-Host "4. Consulte los registros del servicio en:"
Write-Host "   Get-Content '$InstallationPath\logs\service.log' -Wait -Tail 30"
Write-Host "==================================================="
