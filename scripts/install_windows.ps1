# Script de instalación para Windows
# Ejecutar como Administrador

param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status
)

$ServiceName = "ECFAgent"
$ServiceDisplayName = "ECF Data Collection Agent"
$Description = "Agente de recolección de comprobantes fiscales electrónicos (e-CF)"
$ScriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptPath
$PythonPath = "$ProjectRoot\venv\Scripts\python.exe"
$MainScript = "$ProjectRoot\src\main.py"

function Write-Status {
    param($Message, $Type = "Info")
    
    switch ($Type) {
        "Success" { Write-Host "✓ $Message" -ForegroundColor Green }
        "Error" { Write-Host "✗ $Message" -ForegroundColor Red }
        "Warning" { Write-Host "! $Message" -ForegroundColor Yellow }
        default { Write-Host "→ $Message" -ForegroundColor Cyan }
    }
}

function Test-Administrator {
    $currentUser = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Install-Service {
    Write-Status "Instalando ECF Agent como servicio Windows..."
    
    # Verificar que existe el entorno virtual
    if (-not (Test-Path $PythonPath)) {
        Write-Status "No se encontró el entorno virtual. Creando..." "Warning"
        
        Set-Location $ProjectRoot
        python -m venv venv
        & "$ProjectRoot\venv\Scripts\pip.exe" install -r requirements.txt
    }
    
    # Verificar configuración
    $ConfigPath = "$ProjectRoot\config\config.yaml"
    if (-not (Test-Path $ConfigPath)) {
        Write-Status "No se encontró config.yaml. Copie config.example.yaml a config.yaml y configure." "Error"
        exit 1
    }
    
    # Usar NSSM para crear el servicio (alternativa popular para servicios Python)
    $NssmPath = "$ScriptPath\nssm.exe"
    
    if (Test-Path $NssmPath) {
        # Instalar con NSSM
        & $NssmPath install $ServiceName $PythonPath "-m src.main run --config $ConfigPath"
        & $NssmPath set $ServiceName AppDirectory $ProjectRoot
        & $NssmPath set $ServiceName DisplayName $ServiceDisplayName
        & $NssmPath set $ServiceName Description $Description
        & $NssmPath set $ServiceName Start SERVICE_AUTO_START
        & $NssmPath set $ServiceName AppStdout "$ProjectRoot\logs\service.log"
        & $NssmPath set $ServiceName AppStderr "$ProjectRoot\logs\service-error.log"
        
        Write-Status "Servicio instalado con NSSM" "Success"
    }
    else {
        # Alternativa: crear tarea programada
        Write-Status "NSSM no encontrado, creando tarea programada..." "Warning"
        
        $Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "-m src.main run --config `"$ConfigPath`"" -WorkingDirectory $ProjectRoot
        $Trigger = New-ScheduledTaskTrigger -AtStartup
        $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        
        Register-ScheduledTask -TaskName $ServiceName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description $Description -Force
        
        Write-Status "Tarea programada creada" "Success"
    }
}

function Uninstall-Service {
    Write-Status "Desinstalando ECF Agent..."
    
    $NssmPath = "$ScriptPath\nssm.exe"
    
    if (Test-Path $NssmPath) {
        & $NssmPath stop $ServiceName
        & $NssmPath remove $ServiceName confirm
    }
    else {
        Stop-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $ServiceName -Confirm:$false -ErrorAction SilentlyContinue
    }
    
    Write-Status "Servicio desinstalado" "Success"
}

function Start-ECFService {
    Write-Status "Iniciando ECF Agent..."
    
    $NssmPath = "$ScriptPath\nssm.exe"
    
    if (Test-Path $NssmPath) {
        & $NssmPath start $ServiceName
    }
    else {
        Start-ScheduledTask -TaskName $ServiceName
    }
    
    Write-Status "Servicio iniciado" "Success"
}

function Stop-ECFService {
    Write-Status "Deteniendo ECF Agent..."
    
    $NssmPath = "$ScriptPath\nssm.exe"
    
    if (Test-Path $NssmPath) {
        & $NssmPath stop $ServiceName
    }
    else {
        Stop-ScheduledTask -TaskName $ServiceName
    }
    
    Write-Status "Servicio detenido" "Success"
}

function Get-ECFStatus {
    Write-Status "Estado del ECF Agent:"
    
    $NssmPath = "$ScriptPath\nssm.exe"
    
    if (Test-Path $NssmPath) {
        & $NssmPath status $ServiceName
    }
    else {
        $task = Get-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
        if ($task) {
            Write-Host "  Estado: $($task.State)"
            Write-Host "  Última ejecución: $((Get-ScheduledTaskInfo -TaskName $ServiceName).LastRunTime)"
        }
        else {
            Write-Status "Servicio no instalado" "Warning"
        }
    }
}

# Verificar permisos de administrador
if (-not (Test-Administrator)) {
    Write-Status "Este script requiere permisos de Administrador" "Error"
    Write-Host "Ejecute PowerShell como Administrador y vuelva a intentar."
    exit 1
}

# Ejecutar acción
if ($Install) {
    Install-Service
}
elseif ($Uninstall) {
    Uninstall-Service
}
elseif ($Start) {
    Start-ECFService
}
elseif ($Stop) {
    Stop-ECFService
}
elseif ($Status) {
    Get-ECFStatus
}
else {
    Write-Host @"
ECF Agent - Script de Instalación para Windows

Uso:
    .\install_windows.ps1 -Install     Instala el servicio
    .\install_windows.ps1 -Uninstall   Desinstala el servicio
    .\install_windows.ps1 -Start       Inicia el servicio
    .\install_windows.ps1 -Stop        Detiene el servicio
    .\install_windows.ps1 -Status      Muestra el estado

Requisitos:
    - Ejecutar como Administrador
    - Python 3.9+ instalado
    - Archivo config/config.yaml configurado

"@
}
