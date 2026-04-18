"""Workflow engine startup (no-op — engine runs in-process with FastAPI).

The in-process engine (app.engine.engine) is registered and started during
FastAPI lifespan in app/main.py. No separate worker process is needed.
"""
