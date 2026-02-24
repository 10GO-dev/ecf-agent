"""
Script para crear una base de datos SQLite de prueba con la estructura real del cliente.
Basado en: ecf-dgii/script.sql

Ejecutar: python scripts/setup_test_db.py
"""

import json
import sqlite3
import sys
from pathlib import Path

def create_test_database(db_path: str = "./data/test_invoices.db"):
    """Crea la base de datos de prueba con estructura real y datos de ejemplo."""
    
    # Crear directorio si no existe
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Eliminar DB anterior si existe
    # Eliminar DB anterior si existe
    if Path(db_path).exists():
        import time
        max_retries = 3
        for i in range(max_retries):
            try:
                Path(db_path).unlink()
                print(f"🗑️  Base de datos anterior eliminada: {db_path}")
                break
            except PermissionError:
                if i < max_retries - 1:
                    print(f"⚠️  El archivo {db_path} está en uso. Reintentando ({i+1}/{max_retries})...")
                    time.sleep(1)
                else:
                    print(f"❌ Error: No se puede eliminar {db_path}.")
                    print(f"   El archivo está bloqueado por otro proceso (probablemente el agente ejecutándose).")
                    print(f"   Por favor, detenga 'python -m src.main' y vuelva a intentar.")
                    sys.exit(1)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # ====== Crear tablas basadas en script.sql ======
    
    # Tabla principal de facturas (interfazencf)
    cursor.execute("""
        CREATE TABLE interfazencf (
            transaccionid INTEGER NOT NULL,
            codalmacen INTEGER NOT NULL,
            tipoecf TEXT,
            encf TEXT NOT NULL,
            FechaVencimientoSecuencia TEXT NOT NULL,
            IndicadorNotaCredito TEXT NOT NULL,
            IndicadorEnvioDiferido TEXT NOT NULL,
            IndicadorMontoGravado TEXT NOT NULL,
            TipoIngresos TEXT NOT NULL,
            TipoPago TEXT NOT NULL,
            FechaLimitePago TEXT,
            Terminopago TEXT,
            MontoPago TEXT NOT NULL,
            TipoCuentaPago TEXT,
            NumeroCuentaPago TEXT,
            BancoPago TEXT,
            FechaDesde TEXT,
            FechaHasta TEXT,
            TotalPaginas TEXT,
            RNCEmisor TEXT NOT NULL,
            RazonSocialEmisor TEXT NOT NULL,
            NombreComercial TEXT,
            Sucursal TEXT,
            DireccionEmisor TEXT,
            Municipioemisor TEXT,
            Provincia TEXT,
            TelefonoEmisor TEXT,
            CorreoEmisor TEXT,
            WebSite TEXT,
            ActividadEconomica TEXT,
            CodigoVendedor TEXT,
            NumeroFacturaInterna TEXT,
            NumeroPedidoInterno TEXT,
            ZonaVenta TEXT,
            RutaVenta TEXT,
            FechaEmision TEXT NOT NULL,
            RNCComprador TEXT NOT NULL,
            IdentificadorExtranjero TEXT,
            RazonSocialComprador TEXT,
            ContactoComprador TEXT,
            CorreoComprador TEXT,
            DireccionComprador TEXT,
            MunicipioComprador TEXT,
            ProvinciaComprador TEXT,
            PaisComprador TEXT,
            FechaEntrega TEXT,
            ContactoEntrega TEXT,
            DireccionEntrega TEXT,
            TelefonoAdicionalcomprador TEXT,
            FechaOrdenCompra TEXT,
            NumeroOrdenCompra TEXT,
            CodigoInternoComprador TEXT,
            ResponsablePago TEXT,
            MontoGravadoTotal TEXT NOT NULL,
            MontoGravadoI1 TEXT,
            MontoGravadoI2 TEXT,
            MontoGravadoI3 TEXT,
            MontoExento TEXT,
            Itbistasa1 TEXT,
            Itbistasa2 TEXT,
            Itbistasa3 TEXT,
            TotalITBIS TEXT,
            TotalITBIS1 TEXT,
            TotalITBIS2 TEXT,
            TotalITBIS3 TEXT,
            MontoImpuestoAdicional TEXT,
            MontoTotal TEXT,
            MontoNoFacturable TEXT,
            MontoPeriodo TEXT,
            SaldoAnterior TEXT,
            MontoAvancePago TEXT,
            ValorPagar TEXT,
            TotalITBISRetenido TEXT,
            TotalISRRetencion TEXT,
            TotalITBISPercepcion TEXT,
            TotalISRPercepcion TEXT,
            TipoMoneda TEXT,
            TipoCambio TEXT,
            MontoGravadoTotalOtraMoneda TEXT,
            MontoGravado1OtraMoneda TEXT,
            MontoGravado2OtraMoneda TEXT,
            MontoGravado3OtraMoneda TEXT,
            MontoExentoOtraMoneda TEXT,
            TotalITBISOtraMoneda TEXT,
            TotalITBIS1OtraMoneda TEXT,
            TotalITBIS2OtraMoneda TEXT,
            TotalITBIS3OtraMoneda TEXT,
            MontoImpuestoAdicionalOtraMoneda TEXT,
            TotalotraMoneda REAL,
            estado TEXT NOT NULL DEFAULT 'A',
            procesadadgii TEXT NOT NULL DEFAULT 'N',
            PRIMARY KEY (transaccionid, codalmacen)
        )
    """)
    
    # Tabla de detalles (interfazencfdet)
    cursor.execute("""
        CREATE TABLE interfazencfdet (
            transaccionid INTEGER NOT NULL,
            codalmacen INTEGER NOT NULL,
            linea INTEGER NOT NULL,
            NumeroLinea TEXT,
            IndicadorFacturacion TEXT,
            IndicadorAgenteRetencionoPercepcion TEXT,
            MontoITBISRetenido TEXT,
            MontoISRRetenido TEXT,
            tipoimpuesto TEXT,
            tasaITBIS1 TEXT,
            tasaITBIS2 TEXT,
            tasaITBIS3 TEXT,
            tasaISC TEXT,
            tasaISCAD TEXT,
            tasaotroimp TEXT,
            MontoITBIS1 TEXT,
            MontoITBIS2 TEXT,
            MontoITBIS3 TEXT,
            MontoISC TEXT,
            MontoISCAd TEXT,
            MontoOtrosImp TEXT,
            tipoimpotramoneda TEXT,
            tasaITBIS1otramoneda TEXT,
            tasaITBIS2otramoneda TEXT,
            tasaITBIS3otramoneda TEXT,
            tasaISCotramoneda TEXT,
            tasaISCADotramoneda TEXT,
            tasaotroimpotramoneda TEXT,
            MontoITBIS1otramoneda TEXT,
            MontoITBIS2otramoneda TEXT,
            MontoITBIS3otramoneda TEXT,
            MontoISCotramoneda TEXT,
            MontoISCAdotramoneda TEXT,
            MontoOtrosImpotramoneda TEXT,
            TipoCodigo TEXT,
            CodigoItem TEXT,
            NombreItem TEXT,
            IndicadorBienoServicio TEXT,
            DescripcionItem TEXT,
            CantidadItem TEXT,
            UnidadMedida TEXT,
            CantidadReferencia TEXT,
            Subcantidad TEXT,
            CodigoSubCantidad TEXT,
            GradosAcohol TEXT,
            PrecioUnitarioReferencia TEXT,
            FechaElaboracion TEXT,
            FechaVencimientoItem TEXT,
            PrecioUnitarioItem TEXT,
            DescuentoMonto TEXT,
            PrecioOtraMoneda TEXT,
            DescuentoOtraMoneda TEXT,
            MontoItemOtraMoneda TEXT,
            MontoItem TEXT,
            NumeroSubTotal TEXT,
            DescripcionSubtotal TEXT,
            Orden TEXT,
            SubTotalMontoGravadoTotal TEXT,
            SubTotalMontoGravadoI1 TEXT,
            SubTotalMontoGravadoI2 TEXT,
            SubTotalMontoGravadoI3 TEXT,
            SubTotalITBIS1 TEXT,
            SubTotalITBIS2 TEXT,
            SubTotalITBIS3 TEXT,
            SubTotalImpuestoAdicional TEXT,
            SubTotalExento TEXT,
            MontoSubTotal TEXT,
            PRIMARY KEY (transaccionid, codalmacen, linea),
            FOREIGN KEY (transaccionid, codalmacen) REFERENCES interfazencf(transaccionid, codalmacen)
        )
    """)
    
    # Tabla formas de pago (ecfformapago)
    cursor.execute("""
        CREATE TABLE ecfformapago (
            transaccionid INTEGER NOT NULL,
            codalmacen INTEGER NOT NULL,
            formapagoid INTEGER NOT NULL,
            formapago TEXT NOT NULL,
            montopago REAL NOT NULL,
            PRIMARY KEY (transaccionid, codalmacen, formapagoid),
            FOREIGN KEY (transaccionid, codalmacen) REFERENCES interfazencf(transaccionid, codalmacen)
        )
    """)
    
    # Tabla impuestos adicionales (ecftipoimpadic)
    cursor.execute("""
        CREATE TABLE ecftipoimpadic (
            transaccionid INTEGER NOT NULL,
            codalmacen INTEGER NOT NULL,
            linea INTEGER NOT NULL,
            tipoimpid INTEGER NOT NULL,
            codimp TEXT NOT NULL,
            tasaimp REAL NOT NULL,
            PRIMARY KEY (transaccionid, codalmacen, linea, tipoimpid),
            FOREIGN KEY (transaccionid, codalmacen, linea) REFERENCES interfazencfdet(transaccionid, codalmacen, linea)
        )
    """)
    
    # Tabla subdescuentos (ecfsubdescuento)
    cursor.execute("""
        CREATE TABLE ecfsubdescuento (
            transaccionid INTEGER NOT NULL,
            codalmacen INTEGER NOT NULL,
            linea INTEGER NOT NULL,
            subdescid INTEGER NOT NULL,
            tiposubdesc TEXT NOT NULL,
            porcentajedesc REAL,
            montosubdesc REAL NOT NULL,
            PRIMARY KEY (transaccionid, codalmacen, linea, subdescid),
            FOREIGN KEY (transaccionid, codalmacen, linea) REFERENCES interfazencfdet(transaccionid, codalmacen, linea)
        )
    """)
    
    # ====== Insertar datos de prueba ======
    import time
    timestamp = str(int(time.time()))[-7:] # Generamos secuencia unica (7 digitos para sumar 12 con '3200' y el ID final)
    
    # Factura 1 - Consumo (tipo 32)
    cursor.execute(f"""
        INSERT INTO interfazencf (
            transaccionid, codalmacen, tipoecf, encf, FechaVencimientoSecuencia,
            IndicadorNotaCredito, IndicadorEnvioDiferido, IndicadorMontoGravado,
            TipoIngresos, TipoPago, MontoPago, RNCEmisor, RazonSocialEmisor,
            NombreComercial, DireccionEmisor, Municipioemisor, Provincia, FechaEmision, RNCComprador, RazonSocialComprador,
            MontoGravadoTotal, MontoGravadoI1, Itbistasa1, TotalITBIS, TotalITBIS1,
            MontoTotal, ValorPagar, TipoMoneda, estado, procesadadgii
        ) VALUES (
            1, 2, '32', 'E320000000035', '31-12-2027',
            '0', '0', '1', '01', '1', '1200.00', '130013454', 'TekServices Demo SRL',
            'TekDemo', 'Av. 27 de Febrero', '020100', '020000', '14-02-2026', '123456789', 'Consumidor Final',
            '1016.95', '1016.95', '18', '183.05', '183.05',
            '1200.00', '1200.00', 'DOP', 'A', 'N'
        )
    """)
    
    # Detalle de factura 1
    cursor.execute("""
        INSERT INTO interfazencfdet (
            transaccionid, codalmacen, linea, NumeroLinea, IndicadorFacturacion,
            tasaITBIS1, MontoITBIS1, TipoCodigo, CodigoItem, NombreItem,
            IndicadorBienoServicio, CantidadItem, UnidadMedida, PrecioUnitarioItem, MontoItem
        ) VALUES (
            1, 2, 1, '1', '1', '18', '183.05', '01', 'PROD-001', 'Venta Consumidor',
            '1', '1', '43', '1016.95', '1200.00'
        )
    """)
    
    # Forma de pago factura 1
    cursor.execute("""
        INSERT INTO ecfformapago (transaccionid, codalmacen, formapagoid, formapago, montopago)
        VALUES (1, 2, 1, '1', 1200.00)
    """)
    
    # Factura 2 - Consumo (tipo 32)
    cursor.execute(f"""
        INSERT INTO interfazencf (
            transaccionid, codalmacen, tipoecf, encf, FechaVencimientoSecuencia,
            IndicadorNotaCredito, IndicadorEnvioDiferido, IndicadorMontoGravado,
            TipoIngresos, TipoPago, MontoPago, RNCEmisor, RazonSocialEmisor,
            NombreComercial, DireccionEmisor, Municipioemisor, Provincia, FechaEmision, RNCComprador, RazonSocialComprador,
            MontoGravadoTotal, MontoGravadoI1, Itbistasa1, TotalITBIS, TotalITBIS1,
            MontoTotal, ValorPagar, TipoMoneda, estado, procesadadgii
        ) VALUES (
            2, 2, '32', 'E320000000036', '31-12-2027',
            '0', '0', '1', '01', '1', '2500.00', '130013454', 'TekServices Demo SRL',
            'TekDemo', 'Av. 27 de Febrero', '020100', '020000', '14-02-2026', '987654321', 'Consumidor General',
            '2118.64', '2118.64', '18', '381.36', '381.36',
            '2500.00', '2500.00', 'DOP', 'A', 'N'
        )
    """)
    
    # Detalle factura 2
    cursor.execute("""
        INSERT INTO interfazencfdet (
            transaccionid, codalmacen, linea, NumeroLinea, IndicadorFacturacion,
            tasaITBIS1, MontoITBIS1, TipoCodigo, CodigoItem, NombreItem,
            IndicadorBienoServicio, CantidadItem, UnidadMedida, PrecioUnitarioItem, MontoItem
        ) VALUES (
            2, 2, 1, '1', '1', '18', '381.36', '01', 'PROD-002', 'Venta Consumidor 2',
            '1', '1', '43', '2118.64', '2500.00'
        )
    """)
    
    # Forma de pago factura 2
    cursor.execute("""
        INSERT INTO ecfformapago (transaccionid, codalmacen, formapagoid, formapago, montopago)
        VALUES (2, 2, 1, '1', 2500.00)
    """)
    
    # Factura 3 - Consumo (tipo 32)
    cursor.execute(f"""
        INSERT INTO interfazencf (
            transaccionid, codalmacen, tipoecf, encf, FechaVencimientoSecuencia,
            IndicadorNotaCredito, IndicadorEnvioDiferido, IndicadorMontoGravado,
            TipoIngresos, TipoPago, MontoPago, RNCEmisor, RazonSocialEmisor,
            NombreComercial, DireccionEmisor, Municipioemisor, Provincia, FechaEmision, RNCComprador, RazonSocialComprador,
            MontoGravadoTotal, MontoGravadoI1, Itbistasa1, TotalITBIS, TotalITBIS1,
            MontoTotal, ValorPagar, TipoMoneda, estado, procesadadgii
        ) VALUES (
            3, 2, '32', 'E320000000037', '31-12-2027',
            '0', '0', '1', '01', '1', '800.00', '130013454', 'TekServices Demo SRL',
            'TekDemo', 'Av. 27 de Febrero', '020100', '020000', '14-02-2026', '111222333', 'Consumidor Express',
            '677.97', '677.97', '18', '122.03', '122.03',
            '800.00', '800.00', 'DOP', 'A', 'N'
        )
    """)
    
    # Detalle factura 3
    cursor.execute("""
        INSERT INTO interfazencfdet (
            transaccionid, codalmacen, linea, NumeroLinea, IndicadorFacturacion,
            tasaITBIS1, MontoITBIS1, TipoCodigo, CodigoItem, NombreItem,
            IndicadorBienoServicio, CantidadItem, UnidadMedida, PrecioUnitarioItem, MontoItem
        ) VALUES (
            3, 2, 1, '1', '1', '18', '122.03', '01', 'PROD-003', 'Venta Consumidor 3',
            '1', '1', '43', '677.97', '800.00'
        )
    """)
    
    # Forma de pago factura 3
    cursor.execute("""
        INSERT INTO ecfformapago (transaccionid, codalmacen, formapagoid, formapago, montopago)
        VALUES (3, 2, 1, '1', 800.00)
    """)
    
    # Factura 4 - Consumo ALTO (tipo 32) - OVER 250K threshold
    cursor.execute(f"""
        INSERT INTO interfazencf (
            transaccionid, codalmacen, tipoecf, encf, FechaVencimientoSecuencia,
            IndicadorNotaCredito, IndicadorEnvioDiferido, IndicadorMontoGravado,
            TipoIngresos, TipoPago, MontoPago, RNCEmisor, RazonSocialEmisor,
            NombreComercial, DireccionEmisor, Municipioemisor, Provincia, FechaEmision, RNCComprador, RazonSocialComprador,
            MontoGravadoTotal, MontoGravadoI1, Itbistasa1, TotalITBIS, TotalITBIS1,
            MontoTotal, ValorPagar, TipoMoneda, estado, procesadadgii
        ) VALUES (
            4, 2, '32', 'E320000000038', '31-12-2027',
            '0', '0', '1', '01', '1', '300000.00', '130013454', 'TekServices Demo SRL',
            'TekDemo', 'Av. 27 de Febrero', '020100', '020000', '14-02-2026', '444555666', 'Consumidor Premium',
            '254237.29', '254237.29', '18', '45762.71', '45762.71',
            '300000.00', '300000.00', 'DOP', 'A', 'N'
        )
    """)
    
    # Detalle factura 4
    cursor.execute("""
        INSERT INTO interfazencfdet (
            transaccionid, codalmacen, linea, NumeroLinea, IndicadorFacturacion,
            tasaITBIS1, MontoITBIS1, TipoCodigo, CodigoItem, NombreItem,
            IndicadorBienoServicio, CantidadItem, UnidadMedida, PrecioUnitarioItem, MontoItem
        ) VALUES (
            4, 2, 1, '1', '1', '18', '45762.71', '01', 'PROD-004', 'Venta Alto Valor',
            '1', '1', '43', '254237.29', '300000.00'
        )
    """)
    
    # Forma de pago factura 4
    cursor.execute("""
        INSERT INTO ecfformapago (transaccionid, codalmacen, formapagoid, formapago, montopago)
        VALUES (4, 2, 1, '1', 300000.00)
    """)
    
    conn.commit()
    
    # Verificar datos insertados
    cursor.execute("SELECT COUNT(*) FROM interfazencf WHERE estado = 'A'")
    pending_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM interfazencfdet")
    det_count = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"✅ Base de datos SQLite creada: {db_path}")
    print(f"   - Tablas: interfazencf, interfazencfdet, ecfformapago, ecftipoimpadic, ecfsubdescuento")
    print(f"   - {pending_count} facturas pendientes de procesar")
    print(f"   - {det_count} líneas de detalle")
    print(f"\nPara probar el agente:")
    print(f"   python -m src.main validate --config config/config-sqlite.yaml")
    print(f"   python -m src.main once --config config/config-sqlite.yaml --debug")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "./data/test_invoices.db"
    create_test_database(db_path)
