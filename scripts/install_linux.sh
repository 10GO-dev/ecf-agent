#!/bin/bash
# Script de instalación para Linux (systemd)
# Ejecutar con sudo

set -e

SERVICE_NAME="ecf-agent"
SERVICE_USER="ecfagent"
INSTALL_DIR="/opt/ecf-agent"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

print_status() {
    case $2 in
        "success") echo -e "${GREEN}✓ $1${NC}" ;;
        "error") echo -e "${RED}✗ $1${NC}" ;;
        "warning") echo -e "${YELLOW}! $1${NC}" ;;
        *) echo -e "${CYAN}→ $1${NC}" ;;
    esac
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_status "Este script requiere permisos de root" "error"
        echo "Ejecute con: sudo $0"
        exit 1
    fi
}

install_service() {
    print_status "Instalando ECF Agent..."

    # Crear usuario del servicio
    if ! id "$SERVICE_USER" &>/dev/null; then
        print_status "Creando usuario $SERVICE_USER..."
        useradd --system --no-create-home --shell /bin/false "$SERVICE_USER"
    fi

    # Crear directorio de instalación
    print_status "Copiando archivos a $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
    cp -r "$PROJECT_ROOT"/* "$INSTALL_DIR/"
    
    # Crear directorios de datos y logs
    mkdir -p "$INSTALL_DIR/data"
    mkdir -p "$INSTALL_DIR/logs"
    mkdir -p "$INSTALL_DIR/config/mappings"

    # Crear entorno virtual
    print_status "Creando entorno virtual Python..."
    cd "$INSTALL_DIR"
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt

    # Verificar configuración
    if [ ! -f "$INSTALL_DIR/config/config.yaml" ]; then
        print_status "Creando configuración por defecto..." "warning"
        cp "$INSTALL_DIR/config/config.example.yaml" "$INSTALL_DIR/config/config.yaml"
        echo "¡IMPORTANTE! Edite $INSTALL_DIR/config/config.yaml con sus credenciales"
    fi

    # Ajustar permisos
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    chmod 600 "$INSTALL_DIR/config/config.yaml"
    chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true

    # Crear archivo de servicio systemd
    print_status "Creando servicio systemd..."
    cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=ECF Data Collection Agent
Documentation=https://github.com/tekservices/ecf-agent
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$INSTALL_DIR/venv/bin:/usr/bin
ExecStart=$INSTALL_DIR/venv/bin/python -m src.main run --config $INSTALL_DIR/config/config.yaml
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=10

# Seguridad
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR/data $INSTALL_DIR/logs
PrivateTmp=true

# Logging
StandardOutput=append:$INSTALL_DIR/logs/service.log
StandardError=append:$INSTALL_DIR/logs/service-error.log

[Install]
WantedBy=multi-user.target
EOF

    # Recargar systemd
    systemctl daemon-reload
    
    print_status "Servicio instalado" "success"
    echo ""
    echo "Próximos pasos:"
    echo "  1. Edite la configuración: nano $INSTALL_DIR/config/config.yaml"
    echo "  2. Inicie el servicio: sudo systemctl start $SERVICE_NAME"
    echo "  3. Habilite inicio automático: sudo systemctl enable $SERVICE_NAME"
}

uninstall_service() {
    print_status "Desinstalando ECF Agent..."

    # Detener y deshabilitar servicio
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true

    # Eliminar archivo de servicio
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload

    # Preguntar si eliminar datos
    read -p "¿Eliminar también los datos y configuración? (s/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Ss]$ ]]; then
        rm -rf "$INSTALL_DIR"
        print_status "Datos eliminados" "warning"
    else
        print_status "Datos conservados en $INSTALL_DIR" "info"
    fi

    # Eliminar usuario
    userdel "$SERVICE_USER" 2>/dev/null || true

    print_status "Servicio desinstalado" "success"
}

start_service() {
    print_status "Iniciando ECF Agent..."
    systemctl start "$SERVICE_NAME"
    print_status "Servicio iniciado" "success"
}

stop_service() {
    print_status "Deteniendo ECF Agent..."
    systemctl stop "$SERVICE_NAME"
    print_status "Servicio detenido" "success"
}

show_status() {
    systemctl status "$SERVICE_NAME" --no-pager || true
    echo ""
    echo "Logs recientes:"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null || \
        tail -20 "$INSTALL_DIR/logs/ecf-agent.log" 2>/dev/null || \
        echo "No hay logs disponibles"
}

show_logs() {
    journalctl -u "$SERVICE_NAME" -f
}

show_help() {
    cat << EOF
ECF Agent - Script de Instalación para Linux

Uso:
    sudo $0 install     Instala el servicio
    sudo $0 uninstall   Desinstala el servicio
    sudo $0 start       Inicia el servicio
    sudo $0 stop        Detiene el servicio
    sudo $0 restart     Reinicia el servicio
    sudo $0 status      Muestra el estado
    sudo $0 logs        Muestra logs en tiempo real

Requisitos:
    - Python 3.9+
    - systemd
    - Permisos de root

EOF
}

# Main
check_root

case "${1:-}" in
    install)
        install_service
        ;;
    uninstall)
        uninstall_service
        ;;
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    restart)
        stop_service
        start_service
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    *)
        show_help
        ;;
esac
