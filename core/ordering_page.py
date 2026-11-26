"""Presentation settings and publication snapshots for the existing guest menu."""

from django.db.models import F, Q

from .media_services import record_menu_change
from .media_storage import queue_media_upload
from .models import AuditEvent, OrderingPage, OrderingPageSnapshot

LAYOUTS = (
    ("classic", "Classic", "A familiar menu with photos beside each item."),
    ("editorial", "Editorial", "Large images and dedicated product cards."),
    ("compact", "Compact", "A dense, text-first menu for larger catalogs."),
)
COLORS = {
    "accent": "#392A22",
    "background": "#F4EFE6",
    "text": "#27221E",
    "secondary": "#625A51",
}
COLOR_PRESETS = {
    "Warm ivory": COLORS,
    "Olive and cream": {
        "accent": "#374534",
        "background": "#F5F4E9",
        "text": "#252A22",
        "secondary": "#4F584B",
    },
    "Deep plum": {
        "accent": "#592E46",
        "background": "#FFF4F7",
        "text": "#2A2025",
        "secondary": "#63515C",
    },
}
LIST_MODELS = {
    "category_order": "categories",
    "hidden_categories": "categories",
    "product_order": "products",
    "hidden_products": "products",
    "featured_products": "products",
}
DEFAULTS = {
    "display_name": "",
    "introduction": "",
    "tagline": "",
    "banner_alt": "",
    "banner_title": "",
    "banner_description": "",
    "focal_x": 50,
    "focal_y": 50,
    "caption": "solid",
    **COLORS,
    "button_style": "rounded",
    "card_style": "divided",
    "layout": "classic",
    "show_descriptions": True,
    "show_images": True,
    "show_information": False,
    "address": "",
    "phone": "",
    "website": "",
    "instagram": "",
    "facebook": "",
    "opening_note": "",
    **dict.fromkeys(LIST_MODELS, ()),
}
BRAND_FIELDS = ("display_name", "introduction", "banner_alt")
IMAGE_FIELDS = ("logo_content", "banner_content")


def settings_for(page=None):
    if isinstance(page, OrderingPageSnapshot):
        return {**DEFAULTS, **page.data}
    return {
        **DEFAULTS,
        **(page.appearance if page else {}),
        **({field: getattr(page, field) for field in BRAND_FIELDS} if page else {}),
    }


def published_design(business, *, image_field=None):
    pages = OrderingPage.objects.filter(business=business, is_published=True)
    snapshots = OrderingPageSnapshot.objects.filter(
        page__in=pages, number=F("page__published_number")
    )
    legacy = pages.filter(published_number__isnull=True)
    if image_field:
        return snapshots.only(image_field).first() or legacy.only(image_field).first()
    image_flags = {
        "has_banner": Q(banner_content__isnull=False),
        "has_logo": Q(logo_content__isnull=False),
    }
    return (
        snapshots.defer(*IMAGE_FIELDS).annotate(**image_flags).first()
        or legacy.defer(*IMAGE_FIELDS).annotate(**image_flags).first()
    )


def draft_for(page):
    source = page
    if page and page.pk:
        draft = page.snapshots.filter(number__isnull=True).first()
        if draft:
            return draft
        if page.published_number is not None:
            source = page.snapshots.filter(number=page.published_number).first()
    return OrderingPageSnapshot(page=page, data=settings_for(source), **image_values(source))


def image_values(source):
    return {field: getattr(source, field, None) for field in IMAGE_FIELDS}


def luminance(hex_color):
    rgb = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in rgb
    ]
    return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast(left, right):
    bright, dark = sorted((luminance(left), luminance(right)), reverse=True)
    return (bright + 0.05) / (dark + 0.05)


def menu_colors(data):
    accent = data["accent"]
    return {
        **{key: data[key] for key in COLORS},
        "on_accent": max(("#000000", "#FFFFFF"), key=lambda color: contrast(accent, color)),
    }


def ordered(items, preferred, key):
    order = {str(value): index for index, value in enumerate(preferred)}
    return sorted(items, key=lambda item: order.get(str(key(item)), len(order)))


def visible_products(products, data):
    hidden_products = set(data["hidden_products"])
    hidden_categories = set(data["hidden_categories"])
    return [
        item
        for item in products
        if str(item.pk) not in hidden_products and str(item.category_id) not in hidden_categories
    ]


def visible_banners(banners, data):
    hidden = {"product": set(data["hidden_products"]), "category": set(data["hidden_categories"])}
    return [
        banner for banner in banners
        if banner["target"]["value"] not in hidden.get(banner["target"]["type"], ())
    ]


def add_presentation(context, data):
    products = ordered(
        visible_products(context["products"], data), data["product_order"], lambda item: item.pk
    )
    featured = set(data["featured_products"])
    for product in products:
        product.menu_featured = str(product.pk) in featured
    products.sort(key=lambda item: not item.menu_featured)
    category_ids = {item.category_id for item in products}
    categories = [category for category in context["categories"] if category.pk in category_ids]
    context.update(
        menu_products=products,
        categories=ordered(categories, data["category_order"], lambda item: item.pk),
        ordering_design=data,
        ordering_colors=menu_colors(data),
        store_name=data["display_name"] or context["business"].name,
    )


def snapshot_legacy(page):
    if page.is_published and page.published_number is None:
        OrderingPageSnapshot.objects.create(
            page=page, number=0, data=settings_for(page), **image_values(page)
        )


def publish(request, page, draft):
    snapshot_legacy(page)
    page.revision += 1
    draft.number = (page.published_number or 0) + 1
    draft.save(update_fields=["number", "saved_at"])
    page.published_number = draft.number
    # Keep the legacy media projection in sync for the archive worker.
    for field in BRAND_FIELDS:
        setattr(page, field, draft.data[field])
    page.appearance = {key: value for key, value in draft.data.items() if key not in BRAND_FIELDS}
    for field in IMAGE_FIELDS:
        setattr(page, field, getattr(draft, field))
    page.is_published = True
    page.save()
    for kind, field in (("ordering_logo", "logo_content"), ("ordering_banner", "banner_content")):
        queue_media_upload(page.business, kind, page.pk, getattr(page, field))
    record_menu_change(request, page.business, page, "ordering_page.published")


def unpublish(request, page):
    snapshot_legacy(page)
    page.is_published = False
    page.revision += 1
    page.save(update_fields=["is_published", "revision", "updated_at"])
    record_menu_change(request, page.business, page, "ordering_page.unpublished")


def save_draft(request, page, draft, *, action="saved"):
    draft.page = page
    draft.save()
    page.revision += 1
    page.save(update_fields=["revision"])
    AuditEvent.objects.create(
        business=page.business,
        actor=request.user,
        action=f"ordering_page.draft_{action}",
        object_type="OrderingPage",
        object_id=str(page.pk),
    )


def drop_stale_references(business, data):
    available = {}
    for key, manager in LIST_MODELS.items():
        if data[key] and manager not in available:
            available[manager] = set(
                map(str, getattr(business, manager).values_list("pk", flat=True))
            )
    return {
        **data,
        **{
            key: [item for item in data[key] if item in available.get(manager, ())]
            for key, manager in LIST_MODELS.items()
        },
    }
