#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from app.config import PORT, HOST

def main():
    print(f"🚀 Iniciando Servidor de Tablero Agentico (Flask + FastAPI)...")
    print(f"📡 Escuchando en http://{HOST}:{PORT}")
    print(f"🖥️  Interfaz del Operador: http://localhost:{PORT}")
    print(f"📚 Documentación API FastAPI: http://localhost:{PORT}/docs")
    print(f"🔌 MCP Service (stdio): {Path(__file__).resolve().parent / 'mcp_server.py'}")
    
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True
    )

if __name__ == "__main__":
    main()
