import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application

# Initialize Django before importing application models or routing.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator, OriginValidator
from django.conf import settings
from django.http.request import split_domain_port, validate_host

from core.identity import MFAWebsocketMiddleware
from core.routing import websocket_urlpatterns

socket_app = AuthMiddlewareStack(MFAWebsocketMiddleware(URLRouter(websocket_urlpatterns)))
development_sockets = AllowedHostsOriginValidator(socket_app)


async def websocket_application(scope, receive, send):
    if not settings.SECURE_SSL_REDIRECT:
        await development_sockets(scope, receive, send)
        return
    hosts = [value.decode("latin1") for key, value in scope["headers"] if key == b"host"]
    origins = [value for key, value in scope["headers"] if key == b"origin"]
    domain = split_domain_port(hosts[0])[0] if len(hosts) == 1 else ""
    if (scope.get("scheme") != "wss" or not domain or not validate_host(domain, settings.ALLOWED_HOSTS)
            or len(origins) != 1):
        await send({"type": "websocket.close"})
        return
    allowed = [f"https://{host}" for host in settings.ALLOWED_HOSTS] + settings.CSRF_TRUSTED_ORIGINS
    await OriginValidator(socket_app, allowed)(scope, receive, send)


application = ProtocolTypeRouter({"http": django_asgi_app, "websocket": websocket_application})

# Uvicorn does not provide Django runserver's development static-file handler.
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

if settings.DEBUG:
    application = ASGIStaticFilesHandler(application)
