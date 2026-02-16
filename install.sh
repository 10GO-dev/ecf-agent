#!/bin/bash

# ==========================================
# ECF Agent Installer Script (Linux)
# ==========================================
# Instala el Agente ECF como servicio systemd.
# Requiere ejecutar con sudo.

INSTALL_DIR="/opt/ecf-agent"
SERVICE_NAME="ecf-agent"
USER_NAME="ecf-agent"

# 1. Verificar root
if [ "$EUID" -ne 0 ]; then 
  echo "Por favor, ejecute como root (sudo ./install.sh)"
  exit 1
fi

# 2. Crear usuario de servicio (si no existe)
if ! id "$USER_NAME" &>/dev/null; then
    echo "Creando usuario de servicio ($USER_NAME)..."
    useradd -r -s /bin/false $USER_NAME
fi

# 3. Crear directorio de instalación
echo "Creando directorio $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/data"

# 4. Copiar archivos (asumiendo que están en el directorio actual)
if [ -f "ecf-agent" ]; then
    cp ecf-agent "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/ecf-agent"
    echo "Binario copiado."
else
    echo "ADVERTENCIA: No se encontró el binario 'ecf-agent' en el directorio actual."
fi

if [ -f "config.yaml" ]; then
    cp config.yaml "$INSTALL_DIR/"
    echo "Configuración copiada."
fi

# Asignar permisos
chown -R $USER_NAME:$USER_NAME "$INSTALL_DIR"

# 5. Crear archivo de servicio Systemd
echo "Creando servicio systemd..."
cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=ECF Agent Service
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/ecf-agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 6. Recargar y habilitar servicio
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

echo ""
echo "==================================================="
echo "INSTALACIÓN COMPLETADA EXITOSAMENTE"
echo "==================================================="
echo "1. Edita la configuración en: $INSTALL_DIR/config.yaml"
echo "2. Reinicia el servicio: sudo systemctl restart $SERVICE_NAME"
echo "3. Ver logs: journalctl -u $SERVICE_NAME -f"
echo "==================================================="
