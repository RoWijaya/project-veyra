"""Private content-addressed copies of uploaded assets for hosted persistence."""
import hashlib
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone

from .models import OrderingPage, OutboxEvent, ProductImage, PromotionBanner


ASSETS = {
    "product": (ProductImage, "product__business_id", "content"),
    "campaign": (PromotionBanner, "business_id", "content"),
    "ordering_banner": (OrderingPage, "business_id", "banner_content"),
    "ordering_logo": (OrderingPage, "business_id", "logo_content"),
}


def queue_media_upload(business, kind, pk, content):
    if content and settings.SUPABASE_MEDIA_URL:
        OutboxEvent.objects.create(
            business=business, event_type="media.archive", next_attempt_at=timezone.now(),
            payload={"kind": kind, "pk": str(pk), "sha256": hashlib.sha256(content).hexdigest()},
        )


def archive_media(event):
    model, tenant_field, content_field = ASSETS[event.payload["kind"]]
    content = model.objects.filter(pk=event.payload["pk"], **{tenant_field: event.business_id}).values_list(
        content_field, flat=True,
    ).first()
    if not content or hashlib.sha256(content).hexdigest() != event.payload["sha256"]:
        # A replacement or deletion made this job obsolete.
        return
    bucket = quote(settings.SUPABASE_MEDIA_BUCKET, safe="")
    key = f"{event.business_id.hex}/{event.payload['kind']}/{event.payload['sha256']}.webp"
    response = requests.post(
        f"{settings.SUPABASE_MEDIA_URL}/storage/v1/object/{bucket}/{key}",
        headers={
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "image/webp",
            "x-upsert": "true",
        },
        data=bytes(content), timeout=(3.05, 15), allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError("Private media upload was not accepted.")
