import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application

# Initialize Django before importing application models or routing.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from core.identity import MFAWebsocketMiddleware
from core.routing import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(MFAWebsocketMiddleware(URLRouter(websocket_urlpatterns)))
        ),
    }
)

# Uvicorn does not provide Django runserver's development static-file handler.
from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

if settings.DEBUG:
    application = ASGIStaticFilesHandler(application)
