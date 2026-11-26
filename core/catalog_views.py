from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .page_errors import access_restricted
from .forms import CategoryForm, ProductForm
from .media_services import record_menu_change
from .models import Business, ProductImage, PromotionBanner, Voucher
from .services import enforce_business_limit, require_business_feature
from .views import membership_for


def page_results(request, queryset):
    query = request.GET.copy()
    query.pop("page", None)
    return {
        "page_obj": Paginator(queryset, 25).get_page(request.GET.get("page")),
        "pagination_query": query.urlencode() + "&" if query else "",
    }



def apply_product_image(product, values):
    if values.get("remove_image"):
        ProductImage.objects.filter(product=product).delete()
    elif values["image"]:
        ProductImage.objects.update_or_create(product=product, defaults={
            "content": values["image"], "alt_text": values["image_alt"] or product.name,
        })
    elif values["image_alt"]:
        ProductImage.objects.filter(product=product).exclude(alt_text=values["image_alt"]).update(
            alt_text=values["image_alt"], updated_at=timezone.now(),
        )


@login_required
@require_http_methods(["GET", "POST"])
def catalog(request, slug, product_id=None):
    membership = membership_for(request, slug)
    can_manage = membership.allows("catalog.manage")
    if not membership.allows("catalog.view") or ((product_id or request.method == "POST") and not can_manage):
        raise PermissionDenied
    business = membership.business
    products = business.products.select_related("category", "image").defer("image__content").order_by("name", "pk")
    product = get_object_or_404(products, pk=product_id) if product_id else None
    def product_form(product, *args, **kwargs):
        form = ProductForm(*args, business=business, instance=product, **kwargs)
        stations = membership.scope_locations(form.fields["station"].queryset)
        if product and product.station_id and not stations.filter(pk=product.station_id).exists():
            raise PermissionDenied("Only staff with access to the assigned station can edit this product.")
        form.fields["station"].queryset = stations
        return form

    form = product_form(product)
    if request.method == "POST":
        try:
            with transaction.atomic():
                business = Business.objects.select_for_update().get(pk=business.pk)
                membership = membership_for(request, slug)
                if not membership.allows("catalog.manage"):
                    raise PermissionDenied
                require_business_feature(business, "catalog")
                if product_id:
                    product = get_object_or_404(products.select_for_update(of=("self",)), pk=product_id)
                previously_active = bool(product and product.active)
                form = product_form(product, request.POST, request.FILES)
                if form.is_valid():
                    if form.cleaned_data["active"] and not previously_active:
                        enforce_business_limit(business, "products", business.products.filter(active=True).count())
                    saved = form.save()
                    apply_product_image(saved, form.cleaned_data)
                    record_menu_change(request, business, saved, "product.updated" if product_id else "product.created")
                    messages.success(request, "Product saved." if product_id else "Product added.")
                    return redirect("catalog", slug=slug)
        except PermissionError as error:
            return access_restricted(request, str(error))
    search = request.GET.get("q", "").strip()[:160]
    if search:
        products = products.filter(Q(name__icontains=search) | Q(sku__icontains=search) | Q(category__name__icontains=search))
    category_id = request.GET.get("category", "")
    if category_id == "uncategorized":
        products = products.filter(category__isnull=True)
    elif category_id:
        products = products.filter(category_id=category_id) if category_id.isascii() and category_id.isdecimal() and len(category_id) <= 18 else products.none()
    return render(request, "core/catalog.html", {
        **page_results(request, products),
        "business": business, "form": form, "editing": product, "can_manage": can_manage,
        "can_manage_campaigns": can_manage and not membership.location_restricted,
        "search": search, "category_id": category_id, "categories": business.categories.all(),
    }, status=400 if request.method == "POST" else 200)


@login_required
@require_http_methods(["GET", "POST"])
def categories(request, slug, category_id=None):
    membership = membership_for(request, slug)
    can_manage = membership.allows("catalog.manage")
    if not membership.allows("catalog.view") or ((category_id or request.method == "POST") and not can_manage):
        raise PermissionDenied
    business = membership.business
    category = get_object_or_404(business.categories, pk=category_id) if category_id else None
    form = CategoryForm(business=business, instance=category)
    error = None
    if request.method == "POST":
        try:
            with transaction.atomic():
                business = Business.objects.select_for_update().get(pk=business.pk)
                membership = membership_for(request, slug)
                if not membership.allows("catalog.manage"):
                    raise PermissionDenied
                require_business_feature(business, "catalog")
                if category_id:
                    category = get_object_or_404(business.categories.select_for_update(), pk=category_id)
                if category and request.POST.get("action") == "delete":
                    form = CategoryForm(business=business, instance=category)
                    if category.products.exists():
                        error = "Move its products to another category or leave them uncategorized before deleting."
                    elif (category.promotions.exists() or Voucher.objects.filter(categories=category).exists()
                          or PromotionBanner.objects.filter(category=category).exists()):
                        error = "Remove this category from its promotions, vouchers or banners before deleting."
                    else:
                        record_menu_change(request, business, category, "category.deleted")
                        category.delete()
                        messages.success(request, "Category deleted.")
                        return redirect("categories", slug=slug)
                else:
                    form = CategoryForm(request.POST, business=business, instance=category)
                    if form.is_valid():
                        saved = form.save()
                        record_menu_change(request, business, saved, "category.updated" if category_id else "category.created")
                        messages.success(request, "Category saved." if category_id else "Category added.")
                        return redirect("categories", slug=slug)
        except PermissionError as error:
            return access_restricted(request, str(error))
    query = request.GET.get("q", "").strip()[:100]
    listing = business.categories.filter(Q(name__icontains=query) | Q(description__icontains=query)).annotate(product_count=Count("products"))
    return render(request, "core/categories.html", {
        **page_results(request, listing),
        "business": business, "editing": category, "form": form, "can_manage": can_manage, "search": query, "error": error,
    }, status=400 if request.method == "POST" else 200)
