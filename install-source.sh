#!/bin/bash

# ==========================================
# ECF Agent Installer Script (Source Code)
# ==========================================
# Instala el Agente ECF usando el código fuente y un entorno virtual.
# Requiere ejecutar con sudo.

INSTANCE_NAME=""
SELECTED_DRIVER=""

while [ $# -gt 0 ]; do
    case "$1" in
        --driver|-d)
            SELECTED_DRIVER=$(echo "$2" | tr '[:upper:]' '[:lower:]')
            shift 2
            ;;
        --driver=*)
            SELECTED_DRIVER=$(echo "${1#*=}" | tr '[:upper:]' '[:lower:]')
            shift 1
            ;;
        -*)
            echo "Opción no reconocida: $1"
            echo "Uso: sudo ./install-source.sh [nombre-instancia] [--driver mysql|postgres|sqlserver|all]"
            exit 1
            ;;
        *)
            if [ -z "$INSTANCE_NAME" ]; then
                INSTANCE_NAME="$1"
            fi
            shift 1
            ;;
    esac
done

if [ -z "$INSTANCE_NAME" ]; then
    INSTALL_DIR="/opt/ecf-agent"
    SERVICE_NAME="ecf-agent"
    USER_NAME="ecf-agent"
    echo "Instalando instancia por defecto..."
else
    INSTALL_DIR="/opt/ecf-agent-$INSTANCE_NAME"
    SERVICE_NAME="ecf-agent-$INSTANCE_NAME"
    USER_NAME="ecf-agent-$INSTANCE_NAME"
    echo "Instalando instancia: $INSTANCE_NAME..."
fi

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

CREATED_ENV=0
if [ ! -f "$INSTALL_DIR/.env" ] && [ -f ".env.example" ]; then
    cp .env.example "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    CREATED_ENV=1
    echo "Plantilla .env creada en $INSTALL_DIR/.env (permisos 600)."
fi

if [ -f "config/config.yaml" ]; then
    cp config/config.yaml "$INSTALL_DIR/"
elif [ -f "config.yaml" ]; then
    cp config.yaml "$INSTALL_DIR/"
else
    echo "Modo Zero-YAML: Los parámetros operativos y queries se sincronizarán desde TekServices Cloud."
fi

# 5. Configurar entorno virtual
echo "Buscando versión compatible de Python 3 con soporte SSL..."

is_python_valid() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        return 1
    fi
    local ver=$("$cmd" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo "0.0")
    local major=$(echo "$ver" | cut -d. -f1)
    local minor=$(echo "$ver" | cut -d. -f2)
    if [ "$major" -ne 3 ] || [ "$minor" -lt 8 ]; then
        return 1
    fi
    local ssl_ok=$("$cmd" -c 'import ssl; print("1")' 2>/dev/null || echo "0")
    if [ "$ssl_ok" != "1" ]; then
        echo "Aviso: $cmd (v$ver) no tiene el módulo SSL habilitado, omitiendo..."
        return 1
    fi
    return 0
}

PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    if is_python_valid "$cmd"; then
        PYTHON_CMD=$cmd
        VERSION=$($cmd -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        echo "Encontrado Python compatible: $PYTHON_CMD (Versión $VERSION con SSL)"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "No se encontró un Python 3 (>= 3.8) con SSL funcional. Intentando instalar paquetes del sistema..."
    if command -v apt-get &>/dev/null; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y
        apt-get install -y python3 python3-venv python3-pip ca-certificates libssl-dev
    elif command -v dnf &>/dev/null; then
        dnf install -y python3 python3-pip ca-certificates openssl-devel
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-pip ca-certificates openssl-devel
    fi

    for cmd in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
        if is_python_valid "$cmd"; then
            PYTHON_CMD=$cmd
            VERSION=$($cmd -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
            echo "Encontrado Python compatible tras actualización: $PYTHON_CMD (Versión $VERSION con SSL)"
            break
        fi
    done
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: No se encontró una versión compatible de Python 3 (>= 3.8) con soporte SSL activo."
    echo "Sugerencia: Ejecute el instalador binario ./install-binary.sh que no requiere Python en el sistema."
    exit 1
fi

echo "Configurando entorno virtual usando $PYTHON_CMD en $INSTALL_DIR/venv..."
if ! $PYTHON_CMD -m venv "$INSTALL_DIR/venv" 2>/dev/null; then
    echo "Creando entorno virtual con paquetes auxiliares..."
    if command -v apt-get &>/dev/null; then
        apt-get update -y && apt-get install -y python3-venv
    fi
    $PYTHON_CMD -m venv "$INSTALL_DIR/venv" || {
        echo "ERROR: No se pudo crear el entorno virtual en $INSTALL_DIR/venv."
        exit 1
    }
fi

detect_driver_from_env() {
    local env_file="$1"
    if [ -f "$env_file" ]; then
        local found=$(grep -E '^[[:space:]]*DB_DRIVER=' "$env_file" | head -n1 | cut -d= -f2- | tr -d ' "\r\n' | tr '[:upper:]' '[:lower:]')
        if [ -n "$found" ]; then
            echo "$found"
            return 0
        fi
    fi
    return 1
}

if [ -z "$SELECTED_DRIVER" ]; then
    DEFAULT_DRIVER=$(detect_driver_from_env "$INSTALL_DIR/.env" 2>/dev/null || detect_driver_from_env ".env" 2>/dev/null || echo "mysql")

    case "$DEFAULT_DRIVER" in
        postgres|postgresql)
            default_opt="2"
            default_desc="2 [PostgreSQL]"
            ;;
        sqlserver|mssql|odbc)
            default_opt="3"
            default_desc="3 [SQL Server]"
            ;;
        all|todos)
            default_opt="4"
            default_desc="4 [Todos]"
            ;;
        *)
            default_opt="1"
            default_desc="1 [MySQL]"
            ;;
    esac

    if [ -t 0 ] || [ -e /dev/tty ]; then
        echo ""
        echo "=================================================="
        echo " Seleccione el motor de base de datos para el ERP "
        echo "=================================================="
        echo " 1) MySQL / MariaDB (Recomendado / Pure Python)"
        echo " 2) PostgreSQL"
        echo " 3) Microsoft SQL Server (ODBC / pyodbc)"
        echo " 4) Todos los motores"
        echo "=================================================="
        if [ -e /dev/tty ]; then
            read -rp "Seleccione una opción [1-4] (Por defecto: $default_desc): " driver_opt </dev/tty || driver_opt=""
        else
            read -rp "Seleccione una opción [1-4] (Por defecto: $default_desc): " driver_opt || driver_opt=""
        fi

        case "$driver_opt" in
            1|mysql|mariadb)
                SELECTED_DRIVER="mysql"
                ;;
            2|postgres|postgresql)
                SELECTED_DRIVER="postgres"
                ;;
            3|sqlserver|mssql|odbc)
                SELECTED_DRIVER="sqlserver"
                ;;
            4|all|todos)
                SELECTED_DRIVER="all"
                ;;
            "")
                SELECTED_DRIVER="$DEFAULT_DRIVER"
                ;;
            *)
                echo "Opción no reconocida. Usando por defecto: $DEFAULT_DRIVER"
                SELECTED_DRIVER="$DEFAULT_DRIVER"
                ;;
        esac
    else
        SELECTED_DRIVER="$DEFAULT_DRIVER"
        echo "Modo no interactivo: usando driver '$SELECTED_DRIVER'"
    fi
fi

if [ -f "$INSTALL_DIR/.env" ] && [ "$SELECTED_DRIVER" != "all" ]; then
    sed -i "s/^DB_DRIVER=.*/DB_DRIVER=$SELECTED_DRIVER/" "$INSTALL_DIR/.env"
fi

echo "Instalando dependencias base..."
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip --no-cache-dir 2>/dev/null || true

grep -v -E '^(pymysql|psycopg2|pyodbc|cx_Oracle)' "$INSTALL_DIR/requirements.txt" > "$INSTALL_DIR/requirements.base.txt"
if ! "$INSTALL_DIR/venv/bin/python" -m pip install --no-cache-dir -r "$INSTALL_DIR/requirements.base.txt"; then
    rm -f "$INSTALL_DIR/requirements.base.txt"
    echo "ERROR: Falló la instalación de dependencias base."
    exit 1
fi
rm -f "$INSTALL_DIR/requirements.base.txt"

case "$SELECTED_DRIVER" in
    mysql|mariadb)
        echo "Instalando driver MySQL (pymysql)..."
        "$INSTALL_DIR/venv/bin/python" -m pip install --no-cache-dir "pymysql>=1.1"
        ;;
    postgres|postgresql)
        echo "Instalando driver PostgreSQL (psycopg2-binary)..."
        "$INSTALL_DIR/venv/bin/python" -m pip install --no-cache-dir "psycopg2-binary>=2.9"
        ;;
    sqlserver|mssql|odbc)
        echo "Instalando driver SQL Server (pyodbc)..."
        if ! echo '#include <sql.h>' | gcc -E - &>/dev/null; then
            echo "Instalando cabeceras unixODBC requeridas para pyodbc..."
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -y && apt-get install -y unixodbc-dev g++ || true
            elif command -v dnf &>/dev/null; then
                dnf install -y unixODBC-devel gcc-c++ || true
            elif command -v yum &>/dev/null; then
                yum install -y unixODBC-devel gcc-c++ || true
            fi
        fi
        if ! "$INSTALL_DIR/venv/bin/python" -m pip install --no-cache-dir "pyodbc>=5.0"; then
            echo "ERROR: Falló la instalación de pyodbc. Instale unixodbc-dev o ejecute ./install-binary.sh"
            exit 1
        fi
        ;;
    sqlite)
        echo "Driver SQLite listo (integrado en Python)."
        ;;
    all)
        echo "Instalando todos los drivers disponibles..."
        "$INSTALL_DIR/venv/bin/python" -m pip install --no-cache-dir "pymysql>=1.1" "psycopg2-binary>=2.9"
        if ! echo '#include <sql.h>' | gcc -E - &>/dev/null; then
            if command -v apt-get &>/dev/null; then
                export DEBIAN_FRONTEND=noninteractive
                apt-get update -y && apt-get install -y unixodbc-dev g++ || true
            elif command -v dnf &>/dev/null; then
                dnf install -y unixODBC-devel gcc-c++ || true
            fi
        fi
        "$INSTALL_DIR/venv/bin/python" -m pip install --no-cache-dir "pyodbc>=5.0" || {
            echo "Aviso: No se pudo compilar pyodbc. Se instaló soporte para MySQL y PostgreSQL."
        }
        ;;
    *)
        echo "Driver desconocido '$SELECTED_DRIVER'. Instalando MySQL por defecto..."
        "$INSTALL_DIR/venv/bin/python" -m pip install --no-cache-dir "pymysql>=1.1"
        ;;
esac

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
# Ejecutar usando el python del entorno virtual como módulo
ExecStart=$INSTALL_DIR/venv/bin/python -m src.main run
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

VERSION="desconocida"
if [ -f "src/__init__.py" ]; then
    VERSION=$(grep "__version__" src/__init__.py | cut -d'"' -f2)
fi

echo ""
echo "==================================================="
echo "INSTALACIÓN COMPLETADA EXITOSAMENTE (FUENTE)"
echo "Instancia: ${INSTANCE_NAME:-por defecto}"
echo "Servicio: $SERVICE_NAME"
echo "Versión: $VERSION"
echo "Driver ERP: $SELECTED_DRIVER"
echo "Directorio: $INSTALL_DIR"
echo "==================================================="
echo "Pasos siguientes:"
echo "1. Configure el archivo $INSTALL_DIR/.env"
echo "2. Verifique el estado: sudo systemctl status $SERVICE_NAME"
echo "3. Verifique los logs: journalctl -u $SERVICE_NAME -f"
echo "==================================================="
