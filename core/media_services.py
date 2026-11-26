import io
import warnings

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import AuditEvent, Category, OutboxEvent, Product, PromotionBanner
from .pricing import promotion_is_in_window

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


def normalize_image(upload, *, preserve_alpha=False):
    if upload is None or upload.size > MAX_UPLOAD_BYTES:
        raise ValueError("Choose a JPEG, PNG or WebP image up to 5 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload, formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Choose an image with at most 16 million pixels.")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Choose a still image.")
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                # Copy pixels to strip metadata and any appended content.
                if preserve_alpha:
                    clean = Image.new("RGBA", image.size)
                    clean.paste(image.convert("RGBA"))
                else:
                    clean = Image.new("RGB", image.size, "white")
                    if "A" in image.getbands():
                        clean.paste(image.convert("RGB"), mask=image.getchannel("A"))
                    else:
                        clean.paste(image.convert("RGB"))
                output = io.BytesIO()
                clean.save(output, format="WEBP", quality=85, method=4)
                return output.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, SyntaxError) as error:
        raise ValueError("The image could not be read. Choose a valid JPEG, PNG or WebP.") from error


def record_menu_change(request, business, obj, action):
    AuditEvent.objects.create(
        business=business, actor=request.user, action=action,
        object_type=type(obj).__name__, object_id=str(obj.pk),
    )
    OutboxEvent.objects.create(
        business=business, event_type="menu.update",
        payload={"catalog_changed": isinstance(obj, (Category, Product))},
        next_attempt_at=timezone.now(),
    )


def banner_status(banner, at=None):
    now = at or timezone.now()
    if not banner.active:
        return "Draft"
    if banner.starts_at and banner.starts_at > now:
        return "Scheduled"
    if banner.ends_at and banner.ends_at <= now:
        return "Expired"
    if (banner.product_id and not banner.product.active) or (banner.category_id and not banner.category.active):
        return "Hidden target"
    if banner.promotion_id and (
        not banner.promotion.active or not promotion_is_in_window(banner.promotion, at=now)
    ):
        return "Scheduled"
    return "Active"


def menu_banners(business, location, token, at=None):
    banners = business.banners.defer("content").select_related("promotion", "product", "category")
    banners = banners.prefetch_related("locations", "promotion__locations")
    result = []
    for banner in banners:
        if banner_status(banner, at=at) != "Active":
            continue
        locations = list(banner.locations.all())
        if locations and location not in locations:
            continue
        if banner.promotion_id:
            promotion_locations = list(banner.promotion.locations.all())
            if promotion_locations and location not in promotion_locations:
                continue
        kind = "product" if banner.product_id else "category" if banner.category_id else "promotion"
        result.append({
            "id": banner.pk, "headline": banner.headline, "description": banner.description,
            "alt_text": banner.alt_text, "cta_text": banner.cta_text,
            "target": {"type": kind, "value": str(getattr(banner, f"{kind}_id"))},
            "image_url": reverse("guest_banner_image", args=[token, banner.pk]),
        })
        if len(result) == 3:
            break
    return result


def queue_banner_boundary_events(at=None):
    now = at or timezone.now()
    with transaction.atomic():
        banners = PromotionBanner.objects.select_for_update(of=("self",))
        banners = banners.select_related("promotion", "business", "product", "category")
        for banner in banners.defer("content"):
            visible = banner_status(banner, at=now) == "Active"
            previous = banner.last_visible
            if previous == visible:
                continue
            banner.last_visible = visible
            banner.save(update_fields=["last_visible"])
            if previous is not None:
                OutboxEvent.objects.create(
                    business=banner.business, event_type="menu.update",
                    payload={"banner_id": banner.pk}, next_attempt_at=now,
                )
