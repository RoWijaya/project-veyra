from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from .media_forms import BannerForm, ProductImageForm
from .media_services import banner_status, menu_banners, record_menu_change
from .media_storage import queue_media_upload
from .models import Product, ProductImage, PromotionBanner
from .ordering_page import published_design
from .page_errors import access_restricted
from .services import require_business_feature
from .storefront import resolve_storefront


def image_response(content):
    if not content:
        raise Http404
    response = HttpResponse(bytes(content), content_type="image/webp")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = 'inline; filename="image.webp"'
    return response


def media_membership(request, slug):
    from .views import membership_for

    return membership_for(request, slug)


@login_required
@require_http_methods(["GET", "POST"])
def product_image(request, slug, product_id):
    membership = media_membership(request, slug)
    allowed = "catalog.manage" if request.method == "POST" else "catalog.view"
    if not membership.allows(allowed):
        return HttpResponseForbidden("You do not have access to this product image.")
    product = get_object_or_404(Product, pk=product_id, business=membership.business)
    if request.method == "GET":
        return image_response(get_object_or_404(ProductImage, product=product).content)
    try:
        require_business_feature(membership.business, "catalog")
    except PermissionError:
        return HttpResponseForbidden("Catalog access requires an active subscription.")
    form = ProductImageForm(request.POST, request.FILES)
    removing = request.POST.get("action") == "remove"
    if removing or form.is_valid():
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=product.pk)
            if removing:
                ProductImage.objects.filter(product=product).delete()
            else:
                image, _ = ProductImage.objects.update_or_create(product=product, defaults={
                    "content": form.cleaned_data["image"],
                    "alt_text": form.cleaned_data["alt_text"],
                })
                queue_media_upload(membership.business, "product", image.pk, form.cleaned_data["image"])
            record_menu_change(request, membership.business, product, "product.image_changed")
        if request.POST.get("return_to_catalog"):
            messages.success(request, "Product image removed." if removing else "Product image saved.")
            return redirect("product_image_edit", slug=slug, product_id=product.pk)
        return JsonResponse({"saved": True})
    if request.POST.get("return_to_catalog"):
        return render(request, "core/product_image.html", {
            "business": membership.business, "product": product, "form": form,
            "image": ProductImage.objects.filter(product=product).defer("content").first(),
        }, status=400)
    return JsonResponse({"errors": form.errors}, status=400)


@login_required
@require_safe
def product_image_edit(request, slug, product_id):
    membership = media_membership(request, slug)
    if not membership.allows("catalog.manage"):
        raise PermissionDenied
    product = get_object_or_404(Product, pk=product_id, business=membership.business)
    image = ProductImage.objects.filter(product=product).defer("content").first()
    return render(request, "core/product_image.html", {
        "business": membership.business, "product": product, "image": image,
        "form": ProductImageForm(initial={"alt_text": image.alt_text if image else product.name}),
    })


@require_safe
def guest_product_image(request, token, product_id):
    storefront = resolve_storefront(token)
    image = get_object_or_404(
        ProductImage, product_id=product_id, product__business=storefront.business, product__active=True,
    )
    return image_response(image.content)


def ordering_image_response(business, field):
    return image_response(getattr(published_design(business, image_field=field), field, None))


@require_safe
def guest_ordering_banner(request, token):
    return ordering_image_response(resolve_storefront(token).business, "banner_content")


@require_safe
def guest_ordering_logo(request, token):
    return ordering_image_response(resolve_storefront(token).business, "logo_content")


@login_required
@require_http_methods(["GET", "POST"])
def banners(request, slug, banner_id=None):
    membership = media_membership(request, slug)
    if not membership.allows("catalog.view") or membership.location_restricted:
        raise PermissionDenied
    business = membership.business
    banner = get_object_or_404(business.banners.defer("content"), pk=banner_id) if banner_id else None
    with timezone.override(ZoneInfo(business.timezone_name)):
        form = BannerForm(request.POST or None, request.FILES or None,
                          instance=banner, business=business)
        if request.method == "POST":
            if not membership.allows("catalog.manage"):
                raise PermissionDenied
            try:
                require_business_feature(business, "promotions")
            except PermissionError:
                return access_restricted(request, "Promotions require an active subscription.")
            if form.is_valid():
                with transaction.atomic():
                    saved = form.save(commit=False)
                    if form.cleaned_data["image"] is not None:
                        saved.content = form.cleaned_data["image"]
                    saved.last_visible = banner_status(saved) == "Active"
                    saved.save()
                    form.save_m2m()
                    queue_media_upload(business, "campaign", saved.pk, form.cleaned_data["image"])
                    record_menu_change(request, business, saved, "banner.saved")
                messages.success(request, "Banner saved.")
                return redirect("banners", slug=slug)
        listing = list(business.banners.defer("content").select_related("promotion", "product", "category"))
        for item in listing:
            item.display_status = banner_status(item)
        return render(request, "core/banners.html", {
            "business": business, "banners": listing, "form": form, "editing": banner,
            "can_manage": membership.allows("catalog.manage"),
        }, status=400 if request.method == "POST" else 200)


@login_required
@require_safe
def banner_image(request, slug, banner_id):
    membership = media_membership(request, slug)
    if not membership.allows("catalog.view") or membership.location_restricted:
        return HttpResponseForbidden("You do not have access to this image.")
    banner = get_object_or_404(PromotionBanner, pk=banner_id, business=membership.business)
    return image_response(banner.content)


@require_safe
def guest_banner_image(request, token, banner_id):
    storefront = resolve_storefront(token)
    if not any(banner["id"] == banner_id for banner in
               menu_banners(storefront.business, storefront.location, token)):
        raise Http404
    return image_response(get_object_or_404(
        PromotionBanner, pk=banner_id, business=storefront.business,
    ).content)
