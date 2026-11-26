"""Business-scoped editor, private preview, and publication actions."""

from base64 import b64encode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods, require_safe

from .customer_views import guest_catalog, render_storefront
from .media_views import image_response, media_membership
from .models import Business, OrderingPage, ProductImage
from .ordering_forms import OrderingBuilderForm
from .ordering_page import (
    COLOR_PRESETS,
    COLORS,
    DEFAULTS,
    LAYOUTS,
    add_presentation,
    draft_for,
    drop_stale_references,
    image_values,
    ordered,
    publish,
    save_draft,
    unpublish,
)
from .page_errors import access_restricted
from .services import require_business_feature
from .storefront import pickup_token


def builder_membership(request, slug, *, edit=False):
    membership = media_membership(request, slug)
    if membership.allows("ordering_page.edit") or (
        not edit and membership.allows("ordering_page.view")
    ):
        return membership
    raise PermissionDenied("You do not have access to this ordering page.")


def public_menu_url(membership):
    business = membership.business
    table = (
        membership.scope_locations(business.tables)
        .filter(active=True, location__active=True).order_by("name", "pk").first()
    )
    if table:
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        return reverse("table_order", args=[token])
    location = (
        membership.allowed_locations().filter(active=True, takeaway_version__isnull=False)
        .order_by("name", "pk")
        .first()
    )
    return reverse("pickup_menu", args=[pickup_token(location)]) if location else ""


def catalog_rows(business, form, model):
    prefix = "product" if model == "products" else "category"

    def ids(key):
        return str(form[key].value() or "").split(",")

    hidden = set(ids("hidden_" + model))
    featured = set(ids("featured_products")) if prefix == "product" else set()
    queryset = getattr(business, model).order_by("name", "pk").values("pk", "name", "active")
    return [
        {**item, "hidden": str(item["pk"]) in hidden, "featured": str(item["pk"]) in featured}
        for item in ordered(queryset, ids(prefix + "_order"), lambda item: item["pk"])
    ]


def editor_context(membership, page, draft, form):
    business = membership.business
    return {
        "business": business,
        "page": page,
        "draft": draft,
        "form": form,
        "can_edit": membership.allows("ordering_page.edit"),
        "is_owner": business.owner_id == membership.user_id,
        "public_menu_url": public_menu_url(membership),
        "layouts": LAYOUTS,
        "color_presets": COLOR_PRESETS,
        "color_fields": [form[name] for name in COLORS],
        "category_rows": catalog_rows(business, form, "categories"),
        "product_rows": catalog_rows(business, form, "products"),
        "locations": membership.allowed_locations().filter(active=True).order_by("name", "pk"),
        "history": page.snapshots.exclude(number=None)
        .defer("data", "logo_content", "banner_content")
        .order_by("-number")
        if page
        else [],
    }


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def ordering_page_settings(request, slug):
    membership = builder_membership(request, slug)
    business = membership.business
    if request.method == "POST":
        return change_ordering_page(request, membership)
    page = OrderingPage.objects.filter(business=business).first()
    draft = draft_for(page)
    form = OrderingBuilderForm(
        business=business, draft=draft, revision=page.revision if page else 0
    )
    return render(
        request, "core/ordering_page_settings.html", editor_context(membership, page, draft, form)
    )


def change_ordering_page(request, membership):
    business = membership.business
    action = request.POST.get("action") or "save"
    owner_actions = {"publish", "unpublish", "restore"}
    if action in owner_actions:
        if business.owner_id != request.user.pk:
            raise PermissionDenied("Only the business owner can change publication.")
    elif not membership.allows("ordering_page.edit"):
        raise PermissionDenied("You do not have permission to edit ordering page drafts.")

    try:
        with transaction.atomic():
            business = Business.objects.select_for_update().get(pk=business.pk)
            # Resolve membership again inside the write transaction.
            membership = builder_membership(request, business.slug, edit=action == "save")
            if action in owner_actions and business.owner_id != request.user.pk:
                raise PermissionDenied("Only the business owner can change publication.")
            require_business_feature(business, "qr_ordering")
            page = OrderingPage.objects.filter(business=business).first()
            draft = draft_for(page)
            revision = page.revision if page else 0
            form = OrderingBuilderForm(
                request.POST if action == "save" else None,
                request.FILES if action == "save" else None,
                business=business,
                draft=draft,
                revision=revision,
            )
            if request.POST.get("revision") != str(revision):
                context = editor_context(membership, page, draft, form)
                context["conflict"] = True
                return render(request, "core/ordering_page_settings.html", context, status=409)
            if action == "save":
                if not form.is_valid():
                    return render(
                        request,
                        "core/ordering_page_settings.html",
                        editor_context(membership, page, draft, form),
                        status=400,
                    )
                if page is None:
                    page = OrderingPage.objects.create(business=business, is_published=False)
                save_draft(request, page, form.apply())
                messages.success(request, "Draft saved. Your public menu is unchanged.")
            elif action == "publish" and draft.pk:
                draft.data = drop_stale_references(business, draft.data)
                draft.save(update_fields=["data"])
                publish(request, page, draft)
                messages.success(request, "Ordering page published.")
            elif action == "unpublish" and page and page.is_published:
                unpublish(request, page)
                messages.success(request, "Design unpublished. Guests now see the standard menu.")
            elif action == "restore" and page:
                value = request.POST.get("version", "")
                if not value.isascii() or not value.isdecimal() or len(value) > 18:
                    raise Http404
                prior = get_object_or_404(page.snapshots, number=int(value))
                draft.data = drop_stale_references(business, {**DEFAULTS, **prior.data})
                for name, contents in image_values(prior).items():
                    setattr(draft, name, contents)
                save_draft(request, page, draft, action="restored")
                messages.success(request, "Version restored to draft. Publish it when ready.")
            else:
                context = editor_context(membership, page, draft, form)
                context["action_error"] = (
                    "Choose an available action. Save a draft before publishing."
                )
                return render(request, "core/ordering_page_settings.html", context, status=400)
    except PermissionError as error:
        return access_restricted(request, str(error))
    return redirect("ordering_page_settings", slug=business.slug)


def image_data(content):
    return "data:image/webp;base64," + b64encode(content).decode("ascii") if content else ""


@login_required
@never_cache
@xframe_options_sameorigin
@require_http_methods(["GET", "POST"])
def ordering_page_preview(request, slug):
    membership = builder_membership(request, slug)
    business = membership.business
    page = OrderingPage.objects.filter(business=business).first()
    draft = draft_for(page)
    form = None
    if request.method == "POST":
        if not membership.allows("ordering_page.edit"):
            raise PermissionDenied("You do not have permission to preview unsaved edits.")
        form = OrderingBuilderForm(
            request.POST,
            request.FILES,
            business=business,
            draft=draft,
            revision=page.revision if page else 0,
        )
        if form.is_valid():
            draft = form.apply()

    locations = membership.allowed_locations().filter(active=True).order_by("name", "pk")
    selected = request.POST.get("preview_location") or request.GET.get("location")
    if selected and (not selected.isascii() or not selected.isdecimal() or len(selected) > 18):
        raise Http404
    location = get_object_or_404(locations, pk=selected) if selected else locations.first()
    if location is None and membership.location_restricted:
        raise PermissionDenied("A location assignment is required to preview this menu.")
    products, categories = guest_catalog(business, location)
    preview_parameters = request.POST if request.method == "POST" else request.GET
    preview_request = preview_parameters.get("preview_request", "")
    if len(preview_request) > 12 or not (preview_request.isascii() and preview_request.isdecimal()):
        preview_request = ""
    context = {
        "business": business,
        "location": location,
        "products": products,
        "categories": categories,
        "cart_count": 0,
        "is_preview": True,
        "preview_request": preview_request,
        "ordering_customized": True,
        "subscription_allows_orders": True,
        "ordering_logo_url": image_data(draft.logo_content),
        "ordering_banner_url": image_data(draft.banner_content),
        "preview_form": form,
    }
    add_presentation(context, {**DEFAULTS, **draft.data})
    return render_storefront(
        request,
        "core/customer_menu.html",
        context,
        status=400 if form is not None and form.errors else 200,
    )


@login_required
@never_cache
@require_safe
def ordering_preview_product_image(request, slug, product_id):
    business = builder_membership(request, slug).business
    image = get_object_or_404(
        ProductImage, product_id=product_id, product__business=business, product__active=True
    )
    return image_response(image.content)


@login_required
@never_cache
@require_safe
def ordering_editor_image(request, slug, kind):
    business = builder_membership(request, slug).business
    page = get_object_or_404(OrderingPage, business=business)
    draft = draft_for(page)
    return image_response(getattr(draft, kind + "_content"))
