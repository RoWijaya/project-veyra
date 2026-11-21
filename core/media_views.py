from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_safe

from .media_forms import BannerForm, OrderingPageForm, ProductImageForm
from .media_services import banner_status, menu_banners, ordering_page_for, record_menu_change
from .models import Business, OrderingPage, Product, ProductImage, PromotionBanner
from .services import require_business_feature
from .storefront import pickup_token, resolve_storefront


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
                ProductImage.objects.update_or_create(product=product, defaults={
                    "content": form.cleaned_data["image"],
                    "alt_text": form.cleaned_data["alt_text"],
                })
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
        return HttpResponseForbidden("You cannot manage product images.")
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


def ordering_page_preview_url(business):
    table = business.tables.filter(active=True, location__active=True).order_by("name", "pk").first()
    if table:
        token = signing.dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        return reverse("table_order", args=[token])
    location = business.locations.filter(active=True, takeaway_version__isnull=False).order_by("name", "pk").first()
    if location:
        return reverse("pickup_menu", args=[pickup_token(location)])
    return ""


def ordering_page_owner(request, slug):
    business = media_membership(request, slug).business
    if business.owner_id != request.user.pk:
        raise PermissionDenied("Only the business owner can customize the ordering page.")
    return business


@login_required
@require_http_methods(["GET", "POST"])
def ordering_page_settings(request, slug):
    business = ordering_page_owner(request, slug)
    page = ordering_page_for(business)
    form = OrderingPageForm(business=business, instance=page)
    if request.method == "POST":
        try:
            with transaction.atomic():
                business = Business.objects.select_for_update().get(pk=business.pk)
                require_business_feature(business, "qr_ordering")
                page = ordering_page_for(business)
                form = OrderingPageForm(request.POST, request.FILES, business=business, instance=page)
                if form.is_valid():
                    saved = form.save()
                    record_menu_change(request, business, saved, "ordering_page.saved")
                    messages.success(request, "Ordering page saved.")
                    return redirect("ordering_page_settings", slug=slug)
        except PermissionError as error:
            return HttpResponseForbidden(str(error))
    return render(request, "core/ordering_page_settings.html", {
        "business": business, "ordering_page": page, "form": form,
        "preview_url": ordering_page_preview_url(business),
    }, status=400 if request.method == "POST" else 200)


def ordering_banner_response(business):
    page = get_object_or_404(OrderingPage, business=business, banner_content__isnull=False)
    return image_response(page.banner_content)


@login_required
@require_safe
def ordering_banner_image(request, slug):
    return ordering_banner_response(ordering_page_owner(request, slug))


@require_safe
def guest_ordering_banner(request, token):
    return ordering_banner_response(resolve_storefront(token).business)


@login_required
@require_http_methods(["GET", "POST"])
def banners(request, slug, banner_id=None):
    membership = media_membership(request, slug)
    if not membership.allows("catalog.view"):
        return HttpResponseForbidden("You do not have access to promotions.")
    business = membership.business
    banner = get_object_or_404(business.banners.defer("content"), pk=banner_id) if banner_id else None
    with timezone.override(ZoneInfo(business.timezone_name)):
        form = BannerForm(request.POST or None, request.FILES or None,
                          instance=banner, business=business)
        if request.method == "POST":
            if not membership.allows("catalog.manage"):
                return HttpResponseForbidden("You cannot manage banners.")
            try:
                require_business_feature(business, "promotions")
            except PermissionError:
                return HttpResponseForbidden("Promotions require an active subscription.")
            if form.is_valid():
                with transaction.atomic():
                    saved = form.save(commit=False)
                    if form.cleaned_data["image"] is not None:
                        saved.content = form.cleaned_data["image"]
                    saved.last_visible = banner_status(saved) == "Active"
                    saved.save()
                    form.save_m2m()
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
    if not membership.allows("catalog.view"):
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
