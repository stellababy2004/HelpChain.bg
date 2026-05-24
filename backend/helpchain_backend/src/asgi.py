from asgiref.wsgi import WsgiToAsgi

from .app import create_app

# Keep the ASGI entrypoint aligned with the canonical Flask factory.
asgi_app = WsgiToAsgi(create_app())
