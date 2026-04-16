"""Service layer — business logic that operates on ORM sessions.

Services never import FastAPI. They accept typed args and return typed ORM objects.
Callers (routes) are responsible for committing or rolling back the session.
"""
