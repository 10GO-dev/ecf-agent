import os
import sys
import subprocess
import shutil
from pathlib import Path

def build():
    """Compila el agente usando PyInstaller."""
    print("Iniciando compilación del Agente ECF...")
    
    # Limpiar builds anteriores
    shutil.rmtree("build", ignore_errors=True)
    shutil.rmtree("dist", ignore_errors=True)
    
    # Verificar PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("Error: PyInstaller no está instalado. Ejecuta: pip install pyinstaller")
        sys.exit(1)
        
    # Ejecutar PyInstaller
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "build.spec"
    ]
    
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n✅ Compilación exitosa!")
        print(f"Ejecutable generado en: {Path('dist/ecf-agent.exe').absolute()}")
    else:
        print("\n❌ Error durante la compilación.")
        sys.exit(result.returncode)

if __name__ == "__main__":
    build()
