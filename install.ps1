# ==========================================
# ECF Agent Installer Script
# ==========================================
# Este script descarga e instala el Agente ECF como servicio de Windows.
# Requiere ejecutar como Administrador.

$InstallationPath = "C:\TekServices\ECF-Agent"
$AgentUrl = "https://updates.tekservices.com/ecf-agent/ecf-agent.exe" # Reemplazar con URL real
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
$ServiceName = "TGECFAgent"

# 1. Verificar privilegios de administrador
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "Este script debe ejecutarse como Administrador."
    exit 1
}

# 2. Crear directorio de instalación
if (!(Test-Path -Path $InstallationPath)) {
    New-Item -ItemType Directory -Force -Path $InstallationPath | Out-Null
    Write-Host "Directorio creado: $InstallationPath" -ForegroundColor Green
}

# 3. Descargar Agente (Simulado si no hay URL real)
Write-Host "Descargando agente..."
try {
    # Invoke-WebRequest -Uri $AgentUrl -OutFile "$InstallationPath\ecf-agent.exe"
    Write-Warning "NOTA: Debes colocar el archivo 'ecf-agent.exe' en $InstallationPath manualmente si la URL no es válida."
} catch {
    Write-Error "Error descargando agente: $_"
}

# 4. Descargar e instalar NSSM (Gestor de servicios)
$NssmPath = "$InstallationPath\nssm.exe"
if (!(Test-Path $NssmPath)) {
    Write-Host "Descargando NSSM..."
    $ZipPath = "$env:TEMP\nssm.zip"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $ZipPath
    
    # Extraer nssm.exe (asumiendo estructura del zip estándar)
    Expand-Archive -Path $ZipPath -DestinationPath "$env:TEMP\nssm_extracted" -Force
    Copy-Item "$env:TEMP\nssm_extracted\nssm-2.24\win64\nssm.exe" -Destination $NssmPath
    Write-Host "NSSM instalado en $NssmPath" -ForegroundColor Green
}

# 5. Instalar Servicio
Write-Host "Configurando servicio $ServiceName..."
& $NssmPath stop $ServiceName 2>$null
& $NssmPath remove $ServiceName confirm 2>$null

& $NssmPath install $ServiceName "$InstallationPath\ecf-agent.exe"
& $NssmPath set $ServiceName AppDirectory "$InstallationPath"
& $NssmPath set $ServiceName Description "Agente de Recolección de Facturas Electrónicas - TekServices"
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStdout "$InstallationPath\logs\service-out.log"
& $NssmPath set $ServiceName AppStderr "$InstallationPath\logs\service-err.log"
& $NssmPath set $ServiceName AppRotateFiles 1
& $NssmPath set $ServiceName AppRotateOnline 1
& $NssmPath set $ServiceName AppRotateSeconds 86400
& $NssmPath set $ServiceName AppRotateBytes 5242880

# 6. Iniciar Servicio
Write-Host "Iniciando servicio..."
& $NssmPath start $ServiceName

Write-Host "
===================================================
INSTALACIÓN COMPLETADA EXITOSAMENTE
===================================================
1. Edita el archivo de configuración en: $InstallationPath\config.yaml
2. Reinicia el servicio para aplicar cambios: nssm restart $ServiceName
" -ForegroundColor Cyan
