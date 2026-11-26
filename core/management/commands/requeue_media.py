from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.media_storage import ASSETS, queue_media_upload
from core.models import Business


class Command(BaseCommand):
    help = "Queue private Supabase copies from a business's current PostgreSQL media."

    def add_arguments(self, parser):
        parser.add_argument("business_slug")

    def handle(self, *args, **options):
        if not settings.SUPABASE_MEDIA_URL:
            raise CommandError("Configure private Supabase media storage first.")
        try:
            business = Business.objects.get(slug=options["business_slug"])
        except Business.DoesNotExist:
            raise CommandError("Business not found.") from None
        queued = 0
        for kind, (model, tenant_field, field) in ASSETS.items():
            assets = model.objects.filter(**{tenant_field: business.pk, f"{field}__isnull": False})
            for pk, content in assets.values_list("pk", field).iterator(chunk_size=20):
                if content:
                    queue_media_upload(business, kind, pk, content)
                    queued += 1
        self.stdout.write(f"Queued {queued} private media copies for {business.slug}.")
