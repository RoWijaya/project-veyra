import asyncio
from uuid import uuid4

from channels.layers import get_channel_layer
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check Channels group delivery through the configured Redis-compatible service."

    def handle(self, *args, **options):
        try:
            asyncio.run(asyncio.wait_for(self.probe(), timeout=10))
        except Exception as error:
            raise CommandError(f"Channel layer group delivery failed ({type(error).__name__}).") from None
        self.stdout.write(self.style.SUCCESS("Channel layer group delivery verified."))

    async def probe(self):
        layer = get_channel_layer()
        if layer is None:
            raise RuntimeError("No channel layer")
        group = f"veyra_probe_{uuid4().hex}"
        channel = await layer.new_channel()
        sent = {"type": "infrastructure.check", "nonce": uuid4().hex}
        try:
            await layer.group_add(group, channel)
            await layer.group_send(group, sent)
            if await layer.receive(channel) != sent:
                raise ValueError("Channel layer returned unexpected data")
        finally:
            await layer.group_discard(group, channel)
