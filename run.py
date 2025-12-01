import uvicorn
from dotenv import load_dotenv, find_dotenv
from app.config import get_settings

# Load environment variables from .env file with override
load_dotenv(find_dotenv(), override=True)

if __name__ == "__main__":
    settings = get_settings()
    # Configurar workers basado en los núcleos de CPU disponibles
    import multiprocessing
    workers = multiprocessing.cpu_count()
    
    # Configurar uvicorn para manejar solicitudes concurrentes
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=workers,  # Múltiples workers para concurrencia
        loop="uvloop",  # Event loop más rápido
        http="httptools",  # Parser HTTP más rápido
        limit_concurrency=1000  # Límite de conexiones concurrentes
    )