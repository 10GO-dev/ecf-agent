# ECF Data Collection Agent

Agente de recolección de comprobantes fiscales electrónicos (e-CF) para instalación en servidores de clientes.

## ¿Qué hace este agente?

1. **Conecta** a la base de datos del sistema de facturación del cliente
2. **Ejecuta** una query SQL configurada que retorna facturas en formato JSON
3. **Comprime** cada factura (MessagePack + zstd + base64)
4. **Envía** al servidor de TekServices en batches
5. **Reintenta** automáticamente las facturas que fallen

```mermaid
graph LR
    BD[(BD Cliente)] -->|Query SQL| Agent[ECF Agent]
    Agent -->|MessagePack| Compress[zstd + base64]
    Compress -->|POST JSON| API[TekServices API]
    Agent -.->|Reintentos| Queue[(SQLite)]
    Queue -.-> Agent
```

## Características

- 🔌 Soporte multi-BD: MySQL, PostgreSQL, SQL Server, Oracle, SQLite
- 📦 Compresión optimizada (MessagePack + zstd + base64)
- 🔁 Cola de reintentos con SQLite
- ⚙️ Instalación como servicio (Windows/Linux)
- 📊 CLI para gestión y monitoreo

## Instalación Rápida

```bash
# 1. Clonar/copiar el proyecto
cd ecf-agent

# 2. Crear entorno virtual
python -m venv venv
.\venv\Scripts\activate   # Windows
source venv/bin/activate  # Linux/macOS

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Instalar compresión zstd (recomendado)
pip install msgpack zstandard

# 5. Configurar
cp config/config.example.yaml config/config.yaml
# Editar config.yaml con credenciales de BD y API

# 6. Validar configuración
python -m src.main validate

# 7. Ejecutar
python -m src.main run
```

## Configuración

### Variables de Entorno

```bash
# .env
ECF_API_KEY=your_api_key_here
DB_PASSWORD=your_db_password
```

### Métodos de Compresión

| Método | Descripción | Tamaño | Velocidad |
|--------|-------------|--------|-----------|
| `zstd` (default) | MessagePack + zstd + base64 | ~35% más pequeño | 5-10x más rápido |
| `gzip` | JSON + gzip + base64 | Base | Moderada |
| `none` | Sin compresión | 100% | N/A |

```yaml
# config.yaml
api:
  compression: "zstd"  # Opciones: zstd, gzip, none
```

## Descompresión en el Servidor (Node.js)

El agente envía datos comprimidos con **Base64 + zstd + MessagePack**. Para descomprimir:

```javascript
const { decode } = require('@msgpack/msgpack');
const { ZstdCodec } = require('zstd-codec');

/**
 * Descomprime invoice_data recibido del agente
 * @param {string} compressedBase64 - Datos en formato base64
 * @param {string} compressionMethod - "zstd" | "gzip" | "none"
 * @returns {Promise<object>} - Objeto JSON de la factura
 */
async function decompressInvoiceData(compressedBase64, compressionMethod = 'zstd') {
    if (compressionMethod === 'none') {
        return JSON.parse(compressedBase64);
    }
    
    if (compressionMethod === 'gzip') {
        const zlib = require('zlib');
        const compressedBuffer = Buffer.from(compressedBase64, 'base64');
        const decompressed = zlib.gunzipSync(compressedBuffer);
        return JSON.parse(decompressed.toString('utf-8'));
    }
    
    // zstd (default)
    return new Promise((resolve, reject) => {
        ZstdCodec.run(zstd => {
            try {
                // 1. Base64 → Buffer (bytes comprimidos)
                const compressedBuffer = Buffer.from(compressedBase64, 'base64');
                
                // 2. zstd decompress → bytes MessagePack
                const simple = new zstd.Simple();
                const decompressed = simple.decompress(compressedBuffer);
                
                // 3. MessagePack decode → objeto JavaScript
                const data = decode(decompressed);
                
                resolve(data);
            } catch (error) {
                reject(error);
            }
        });
    });
}

// Uso en Express endpoint
app.post('/private/ecf/dgii-send', async (req, res) => {
    const { compression, invoices } = req.body;
    
    for (const invoice of invoices) {
        const invoiceData = await decompressInvoiceData(
            invoice.invoice_data,
            invoice.compression || compression
        );
        
        console.log('Factura descomprimida:', invoiceData);
        // { ECF: {...}, Detalles: [...], FormasPago: [...] }
    }
    
    res.json({ success: true });
});
```

### Dependencias del servidor

```bash
npm install @msgpack/msgpack zstd-codec
```

## Query SQL

El agente soporta dos modos de extracción de datos: **Nativo (Recomendado)** y **Pre-ensamblado**.

### 1. Extracción DB-Agnostic (Nativo)

Este método es universal y funciona en cualquier base de datos (MySQL, PostgreSQL, SQL Server, Oracle, SQLite) sin requerir funciones JSON específicas del motor.

La configuración define una consulta principal para las cabeceras y subconsultas usando un comodín de IDs para evitar problemas de N+1 queries. El agente agrupará y ensamblará el JSON final en memoria y de forma nativa.

```yaml
database:
  # 1. Consulta principal de cabeceras (requiere alias si nombres de columna no coinciden)
  query: |
    SELECT *,
           transaccionid as id,
           encf as ecf_number,
           RNCComprador as rnc_buyer,
           MontoTotal as total_amount
    FROM interfazencf
    WHERE estado = 'A'
    ORDER BY transaccionid ASC
    LIMIT {batch_size}

  # 2. Subconsultas DB-Agnostic para elementos hijos
  details_query: |
    SELECT * FROM interfazencfdet WHERE transaccionid IN ({ids})
  
  taxes_query: |
    SELECT * FROM ecftipoimpadic WHERE transaccionid IN ({ids})
    
  payments_query: |
    SELECT * FROM ecfformapago WHERE transaccionid IN ({ids})
```



## Uso

```bash
# Servicio continuo
python -m src.main run

# Una sola ejecución (debug)
python -m src.main once --debug

# Validar configuración
python -m src.main validate

# Ver cola de reintentos
python -m src.main status
```

## Instalación como Servicio

### Windows
```powershell
.\scripts\install_windows.ps1 -Install
.\scripts\install_windows.ps1 -Start
```

### Linux
```bash
sudo ./scripts/install_linux.sh install
sudo systemctl start ecf-agent
```

## Estructura del Proyecto

```
ecf-agent/
├── src/
│   ├── main.py          # Entry point y CLI
│   ├── config.py        # Carga de configuración
│   ├── database/        # Conectores de BD
│   ├── sender/          # Cliente API + compresión
│   ├── queue/           # Cola de reintentos
│   └── scheduler/       # Jobs programados
├── config/
│   └── config.example.yaml
├── scripts/             # Instalación Windows/Linux
├── logs/
└── data/               # SQLite para reintentos
```

## License

MIT
