import os
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check the local ASGI listener and its PostgreSQL and Redis readiness endpoint."

    def handle(self, *args, **options):
        host = next((host for host in settings.ALLOWED_HOSTS if host != "*" and not host.startswith(".")), None)
        if not host:
            raise CommandError("Staging requires an explicit allowed hostname.")
        request = Request(
            f"http://127.0.0.1:{int(os.environ.get('PORT', '8000'))}/readyz/",
            headers={"Host": host, "X-Forwarded-Proto": "https"},
        )
        try:
            with urlopen(request, timeout=6) as response:
                ready = response.status == 200
        except URLError:
            ready = False
        if not ready:
            raise CommandError("ASGI dependency readiness failed.")
        self.stdout.write("ASGI dependency readiness passed.")
