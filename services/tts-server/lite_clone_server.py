"""Entry point — re-exports the FastAPI app from app.py for backward compatibility.

The server can be started with either:
    python lite_clone_server.py
    python app.py
    uvicorn lite_clone_server:app
    uvicorn app:app
"""

from app import app  # noqa: F401 — re-exported so uvicorn "lite_clone_server:app" keeps working

if __name__ == "__main__":
    import uvicorn
    from config import config_manager, get_host, get_ssl_config

    host = get_host()
    port = int(config_manager.get_int("lite_server.port", 8005))
    uvicorn.run(
        "lite_clone_server:app",
        host=host,
        port=port,
        reload=False,
        **get_ssl_config(),
    )
