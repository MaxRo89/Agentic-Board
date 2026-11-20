from a2wsgi import WSGIMiddleware
from app.fastapi_app import api
from app.flask_app import flask_app

# The Flask application serves the Operator UI HTML templates and static assets
# FastAPI serves high-performance API endpoints (/api/...), SSE stream (/api/events), and Swagger docs (/docs)
# Mounting WSGI Flask application into ASGI FastAPI
api.mount("/", WSGIMiddleware(flask_app))

# Unified ASGI application
app = api
