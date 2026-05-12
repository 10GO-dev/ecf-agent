#!/bin/bash

# ==========================================
# ECF Agent Installer Script (Source Code)
# ==========================================
# Instala el Agente ECF usando el código fuente y un entorno virtual.
# Requiere ejecutar con sudo.

INSTALL_DIR="/opt/ecf-agent"
SERVICE_NAME="ecf-agent"
USER_NAME="ecf-agent"

# 1. Verificar root
if [ "$EUID" -ne 0 ]; then 
  echo "Por favor, ejecute como root (sudo ./install-source.sh)"
  exit 1
fi

echo "Iniciando instalación del Agente ECF desde fuente..."

# 2. Crear usuario de servicio (si no existe)
if ! id "$USER_NAME" &>/dev/null; then
    echo "Creando usuario de servicio ($USER_NAME)..."
    useradd -r -s /bin/false $USER_NAME
fi

# 3. Preparar directorios
echo "Preparando directorio $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/data"
mkdir -p "$INSTALL_DIR/src"

# 4. Copiar archivos fuente
echo "Copiando archivos..."
cp -r src/* "$INSTALL_DIR/src/"
cp requirements.txt "$INSTALL_DIR/"

if [ -f "config/config.yaml" ]; then
    cp config/config.yaml "$INSTALL_DIR/"
    echo "Configuración copiada desde config/."
elif [ -f "config.yaml" ]; then
    cp config.yaml "$INSTALL_DIR/"
    echo "Configuración copiada desde raíz."
else
    echo "ADVERTENCIA: No se encontró config.yaml. Deberás crear uno en $INSTALL_DIR/config.yaml"
fi

# 5. Configurar entorno virtual
echo "Configurando entorno virtual en $INSTALL_DIR/venv..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# 6. Asignar permisos
echo "Asignando permisos..."
chown -R $USER_NAME:$USER_NAME "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"

# 7. Crear archivo de servicio Systemd
echo "Creando servicio systemd..."
cat > /etc/systemd/system/$SERVICE_NAME.service <<EOF
[Unit]
Description=ECF Agent Service
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$INSTALL_DIR
# Ejecutar usando el python del entorno virtual
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/src/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# 8. Recargar y habilitar servicio
echo "Habilitando servicio..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE_NAME

echo ""
echo "==================================================="
echo "INSTALACIÓN COMPLETADA EXITOSAMENTE (FUENTE)"
echo "==================================================="
echo "1. El agente está corriendo desde: $INSTALL_DIR/src/main.py"
echo "2. Entorno virtual en: $INSTALL_DIR/venv"
echo "3. Ver logs: journalctl -u $SERVICE_NAME -f"
echo "==================================================="
