import hashlib
import io
import re
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import django
import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, logout
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, DecimalField, F, Prefetch, Q, Sum
from django.db.models.functions import TruncDate
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.encoding import force_str
from django.utils.http import (
    url_has_allowed_host_and_scheme,
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from .forms import (
    BusinessForm,
    CustomerForm,
    InventoryItemForm,
    InvitationForm,
    LocationForm,
    PaymentConnectionForm,
    PrinterForm,
    PromotionForm,
    RecipeLineForm,
    RegistrationForm,
    StationForm,
    StockAdjustmentForm,
    VerificationResendForm,
)
from .identity import primary_login
from .models import (
    AuditEvent,
    Business,
    Customer,
    DiningTable,
    InventoryBalance,
    InventoryItem,
    Invitation,
    Location,
    Membership,
    Order,
    OrderLine,
    OrderTender,
    OutboxEvent,
    PairedDevice,
    PaymentConnection,
    PaymentTransaction,
    PrintJob,
    Product,
    Promotion,
    RecipeLine,
    Station,
    User,
)
from .modifiers import modifier_catalog, submitted_options
from .permissions import (
    ORDER_TRACKING_MAX_AGE,
    order_for_tracking_cookie,
    order_tracking_cookie_name,
    order_tracking_cookie_signer,
)
from .pricing import calculate_voucher, pos_voucher_approval, price_order_items, promotional_ordering
from .voucher_limits import VoucherRateLimit, check_voucher_attempt
from .storefront import pickup_token, validate_table_token
from .services import (
    accept_invitation,
    adjust_stock,
    business_entitlements,
    create_business,
    create_inventory_item,
    create_invitation,
    create_location,
    create_order,
    create_printer,
    create_station,
    delete_payment_connection,
    enforce_business_limit,
    initiate_qris_payment,
    issue_station_pairing_code,
    record_order_payment,
    reprint_order_ticket,
    require_business_feature,
    revoke_invitation,
    revoke_paired_device,
    save_payment_connection,
    transition_order,
    verify_payment_connection,
)


def home(request):
    video_path = "core/landing/veyra-story.mp4"
    video_sources = (
        [{"url": reverse("landing_film"), "type": "video/mp4"}] if finders.find(video_path) else []
    )
    poster_path = "core/landing/veyra-story-poster.webp"
    poster_url = static(poster_path) if finders.find(poster_path) else ""
    return render(
        request,
        "core/home.html",
        {"landing_video_sources": video_sources, "landing_poster": poster_url},
    )


@require_safe
def landing_film(request):
    source = finders.find("core/landing/veyra-story.mp4")
    if not source:
        raise Http404

    path = Path(source)
    size = path.stat().st_size
    start, end = 0, size - 1
    status = 200
    range_header = request.headers.get("Range", "")
    unit, separator, value = range_header.partition("=")
    if separator and unit.strip().lower() == "bytes":
        match = re.fullmatch(r"(\d*)-(\d*)", value.strip())
        if not match or not any(match.groups()) or "," in value:
            return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})

        first, last = match.groups()
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        else:
            suffix_length = int(last)
            if suffix_length == 0:
                return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})
            start, end = max(size - suffix_length, 0), size - 1
        if start >= size or end < start:
            return HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        status = 206

    length = end - start + 1

    def stream_file():
        with path.open("rb") as film:
            film.seek(start)
            remaining = length
            while remaining:
                chunk = film.read(min(64 * 1024, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk

    response = (
        HttpResponse(content_type="video/mp4", status=status)
        if request.method == "HEAD"
        else StreamingHttpResponse(stream_file(), content_type="video/mp4", status=status)
    )
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(length)
    response["X-Content-Type-Options"] = "nosniff"
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def send_account_email(subject, body, recipient):
    if django.VERSION >= (6, 1):
        from django.core.mail import EmailMessage

        message = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient])
        return message.send(using="default")
    from django.core.mail import send_mail

    return send_mail(subject, body, None, [recipient])


def send_verification_email(request, user, next_url=""):
    token = signing.dumps(user.pk, salt="veyra-email-verification")
    uid = urlsafe_base64_encode(str(user.pk).encode())
    verify_path = reverse("verify_email", kwargs={"uidb64": uid, "token": token})
    if next_url:
        verify_path = f"{verify_path}?{urlencode({'next': next_url})}"
    link = request.build_absolute_uri(verify_path)
    send_account_email("Verify your Veyra account", f"Confirm your email: {link}", user.email)


@require_http_methods(["GET", "POST"])
def resend_verification(request):
    next_url = request.POST.get("next") or request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ""
    form = VerificationResendForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        ip = request.META.get("REMOTE_ADDR", "unknown")
        ip_key = "verification-ip:" + hashlib.sha256(ip.encode()).hexdigest()
        cache.add(ip_key, 0, 600)
        attempts = cache.incr(ip_key)
        email_key = "verification-email:" + hashlib.sha256(email.encode()).hexdigest()
        if attempts <= 8 and cache.add(email_key, True, 600):
            user = User.objects.filter(email=email, is_active=False, email_verified_at__isnull=True).first()
            if user:
                send_verification_email(request, user, next_url)
                AuditEvent.objects.create(actor=user, action="account.verification_resent")
        return render(request, "core/registered.html", {"next_url": next_url})
    return render(request, "core/auth.html", {
        "form": form, "title": "Resend verification email", "next_url": next_url,
    })


def register(request):
    next_url = request.POST.get("next") or request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ""
    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        submitted_email = request.POST.get("email", "").strip().lower()
        if submitted_email and User.objects.filter(email=submitted_email).exists():
            return render(request, "core/registered.html", {"next_url": next_url})
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.is_active = False
        user.save()
        send_verification_email(request, user, next_url)
        AuditEvent.objects.create(actor=user, action="account.registered")
        return render(request, "core/registered.html", {"next_url": next_url})
    return render(
        request,
        "core/auth.html",
        {"form": form, "title": "Create your account", "next_url": next_url},
    )


@transaction.atomic
def verify_email(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user_id = signing.loads(token, salt="veyra-email-verification", max_age=60 * 60 * 24)
        if str(user_id) != uid:
            raise signing.BadSignature
        user = get_object_or_404(User.objects.select_for_update(), pk=uid, email_verified_at__isnull=True, is_active=False)
    except (ValueError, TypeError, signing.BadSignature, Http404):
        return render(request, "core/verify.html", {"valid": False}, status=400)
    if not user.is_active:
        user.is_active = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=["is_active", "email_verified_at"])
        AuditEvent.objects.create(actor=user, action="account.email_verified")
    next_url = request.GET.get("next", "")
    if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return primary_login(request, user, next_url)
    return render(request, "core/verify.html", {"valid": True})


def sign_in(request):
    error = None
    next_url = request.POST.get("next") or request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ""
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        ip = request.META.get("REMOTE_ADDR", "unknown")
        attempt_key = "login:" + hashlib.sha256(f"{ip}:{email}".encode()).hexdigest()
        cache.add(attempt_key, 0, 600)
        attempts = cache.incr(attempt_key)
        if attempts > 8:
            error = "Too many attempts. Wait 10 minutes and try again."
        else:
            user = authenticate(request, email=email, password=request.POST.get("password", ""))
            if user:
                cache.delete(attempt_key)
                return primary_login(request, user, next_url or "/app/")
            error = "Email or password is incorrect. Verify your email before signing in."
    return render(
        request,
        "core/auth.html",
        {"title": "Welcome back", "error": error, "next_url": next_url},
    )


@require_POST
def sign_out(request):
    logout(request)
    return redirect("home")


@login_required
def workspace_home(request):
    businesses = (
        Business.objects.filter(
            Q(owner=request.user) | Q(memberships__user=request.user, memberships__active=True)
        )
        .distinct()
        .annotate(
            product_count=Count("products", distinct=True),
            order_count=Count("orders", distinct=True),
        )
    )
    return render(request, "core/workspaces.html", {"businesses": businesses})


@login_required
def create_business_view(request):
    form = BusinessForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        new_business = form.save(commit=False)
        business = create_business(
            owner=request.user,
            name=new_business.name,
            slug=new_business.slug,
            business_type=new_business.business_type,
        )
        return redirect("business_dashboard", slug=business.slug)
    return render(request, "core/auth.html", {"title": "Set up your business", "form": form})


def membership_for(request, slug):
    business = get_object_or_404(Business, slug=slug)
    if business.owner_id == request.user.pk:
        membership, _ = Membership.objects.get_or_create(
            business=business, user=request.user, defaults={"active": True}
        )
        if not membership.active:
            membership.active = True
            membership.save(update_fields=["active"])
        return membership
    return get_object_or_404(
        Membership.objects.select_related("business"),
        business=business,
        user=request.user,
        active=True,
    )


@login_required
@require_safe
def business_settings(request, slug):
    membership = membership_for(request, slug)
    return render(request, "core/business_settings.html", {"business": membership.business})


@login_required
def business_dashboard(request, slug):
    membership = membership_for(request, slug)
    business = membership.business
    entitlement_state = business_entitlements(business)
    can_see_catalog = membership.allows("catalog.view")
    can_manage_catalog = membership.allows("catalog.manage")
    can_see_orders = membership.allows("orders.view")
    can_see_locations = membership.allows("locations.view")
    can_see_inventory = membership.allows("inventory.view")
    can_see_customers = membership.allows("customers.view")
    can_manage_team = membership.allows("employees.manage")
    can_see_team = can_manage_team or membership.allows("employees.view")
    return render(
        request,
        "core/dashboard.html",
        {
            "business": business,
            "entitlement_state": entitlement_state,
            "membership": membership,
            "is_owner": business.owner_id == request.user.pk,
            "can_see_catalog": can_see_catalog,
            "can_manage_catalog": can_manage_catalog,
            "can_see_orders": can_see_orders,
            "can_see_locations": can_see_locations,
            "can_see_tables": membership.allows("tables.view") or membership.allows("tables.manage"),
            "can_see_inventory": can_see_inventory,
            "can_see_customers": can_see_customers,
            "can_manage_team": can_manage_team,
            "can_see_team": can_see_team,
            "can_see_reports": membership.allows("reports.view"),
            "can_create_orders": membership.allows("orders.create"),
            "product_count": business.products.filter(active=True).count()
            if can_see_catalog
            else None,
            "order_count": business.orders.count() if can_see_orders else None,
            "locations": business.locations.count() if can_see_locations else None,
            "recent_orders": business.orders.order_by("-created_at")[:5] if can_see_orders else [],
        },
    )


@login_required
@require_safe
def reports(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("reports.view"):
        return HttpResponseForbidden("You do not have access to reports.")
    business = membership.business
    try:
        require_business_feature(business, "analytics")
    except PermissionError:
        return HttpResponse("Analytics is unavailable on this subscription.", status=403)
    today = timezone.localdate()
    raw_end_date = request.GET.get("end", "")
    raw_start_date = request.GET.get("start", "")
    def invalid_dates(message):
        return render(request, "core/reports.html", {
            "business": business,
            "filter_error": message,
            "start_value": raw_start_date,
            "end_value": raw_end_date,
            "locations": business.locations.filter(active=True).order_by("name"),
            "categories": business.categories.order_by("name"),
            "location_value": request.GET.get("location", ""),
            "category_value": request.GET.get("category", ""),
        }, status=400)

    try:
        end_date = parse_date(raw_end_date) if raw_end_date else today
        start_date = parse_date(raw_start_date) if raw_start_date else None
        if end_date is None or (raw_start_date and start_date is None):
            raise ValueError
        start_date = start_date or end_date - timedelta(days=29)
        next_date = end_date + timedelta(days=1)
    except (ValueError, OverflowError):
        return invalid_dates("Enter valid calendar dates in YYYY-MM-DD format.")
    if start_date > end_date or (end_date - start_date).days > 366:
        return invalid_dates("Choose a date range of at most 367 days, with From on or before Through.")
    location_id = request.GET.get("location", "")
    locations = business.locations.filter(active=True).order_by("name")
    location = None
    if location_id:
        location = get_object_or_404(locations, pk=location_id)
    categories = business.categories.order_by("name")
    category_id = request.GET.get("category", "")
    category = get_object_or_404(categories, pk=category_id) if category_id else None
    start_at = timezone.make_aware(datetime.combine(start_date, time.min))
    end_at = timezone.make_aware(datetime.combine(next_date, time.min))
    orders = business.orders.filter(created_at__gte=start_at, created_at__lt=end_at)
    lines = OrderLine.objects.filter(order__in=orders)
    if location:
        orders = orders.filter(location=location)
        lines = lines.filter(order__location=location)
    if category:
        lines = lines.filter(product__category=category)
    live_orders = orders.exclude(status=Order.Status.CANCELLED)
    paid = live_orders.filter(payment_status=Order.PaymentStatus.PAID)
    pending = live_orders.filter(
        payment_status__in=(Order.PaymentStatus.UNPAID, Order.PaymentStatus.PENDING)
    )
    paid_total = paid.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    pending_total = pending.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    cancelled = orders.filter(status=Order.Status.CANCELLED)
    payment_breakdown = list(
        paid.values("payment_method").annotate(total=Sum("total"), orders=Count("pk"))
        .order_by("payment_method")
    )
    sales_by_day = list(
        paid.annotate(day=TruncDate("created_at", tzinfo=ZoneInfo(business.timezone_name)))
        .values("day")
        .annotate(revenue=Sum("total"), orders=Count("pk"))
        .order_by("day")
    )
    category_performance = list(
        lines.filter(order__payment_status=Order.PaymentStatus.PAID)
        .exclude(order__status=Order.Status.CANCELLED)
        .values("product__category__name")
        .annotate(
            units=Sum("quantity"),
            revenue=Sum(F("unit_price") * F("quantity"), output_field=DecimalField()),
        )
        .order_by("-revenue")
    )
    location_performance = list(
        paid.values("location__name")
        .annotate(revenue=Sum("total"), orders=Count("pk"))
        .order_by("-revenue")
    )
    best_sellers = list(
        lines.filter(order__payment_status=Order.PaymentStatus.PAID)
        .exclude(order__status=Order.Status.CANCELLED)
        .values("product_id", "product_name")
        .annotate(
            units=Sum("quantity"),
            revenue=Sum(F("unit_price") * F("quantity"), output_field=DecimalField()),
        )
        .order_by("-units", "product_name")[:10]
    )
    voucher_orders = orders.filter(voucher_redemption__isnull=False)
    paid_condition = Q(payment_status=Order.PaymentStatus.PAID) & ~Q(status=Order.Status.CANCELLED)
    voucher_performance = (
        voucher_orders.values("voucher_redemption__voucher_id", "voucher_redemption__voucher__name", "location__name")
        .annotate(
            redemptions=Count("pk"), discounts=Sum("voucher_discount"),
            paid_orders=Count("pk", filter=paid_condition), paid_sales=Sum("total", filter=paid_condition),
            average_sale=Avg("total", filter=paid_condition),
        ).order_by("-redemptions", "voucher_redemption__voucher_id", "location__name")
    )
    return render(
        request,
        "core/reports.html",
        {
        "business": business,
        "locations": locations,
        "categories": categories,
        "selected_location": location,
        "selected_category": category,
        "start_date": start_date,
        "end_date": end_date,
        "paid_total": paid_total,
        "pending_total": pending_total,
        "paid_orders": paid.count(),
        "average_paid_order": paid.aggregate(value=Avg("total"))["value"] or Decimal("0.00"),
        "cancelled_count": cancelled.count(),
        "cancelled_total": cancelled.aggregate(value=Sum("total"))["value"] or Decimal("0.00"),
        "payment_breakdown": payment_breakdown,
        "best_sellers": best_sellers,
        "sales_by_day": sales_by_day,
        "peak_daily_sales": max((row["revenue"] for row in sales_by_day), default=0),
        "category_performance": category_performance,
        "location_performance": location_performance,
        "voucher_performance": voucher_performance,
        "average_without_voucher": paid.filter(voucher_redemption__isnull=True).aggregate(value=Avg("total"))["value"],
        "dine_in_count": live_orders.filter(order_type=Order.OrderType.DINE_IN).count(),
        "takeaway_count": live_orders.filter(order_type=Order.OrderType.TAKEAWAY).count(),
        },
    )


@login_required
def promotions(request, slug, promotion_id=None):
    membership = membership_for(request, slug)
    if not membership.allows("catalog.view"):
        return HttpResponseForbidden("You do not have access to this catalog.")
    business = membership.business
    promotion = (
        get_object_or_404(Promotion, business=business, pk=promotion_id)
        if promotion_id is not None
        else None
    )
    form = PromotionForm(request.POST or None, business=business, instance=promotion)
    if request.method == "POST":
        if not membership.allows("catalog.manage"):
            return HttpResponseForbidden("You cannot manage promotions for this business.")
        try:
            require_business_feature(business, "promotions")
        except PermissionError as exc:
            return HttpResponse(str(exc), status=403)
        if form.is_valid():
            promotion = form.save()
            AuditEvent.objects.create(
                business=business,
                actor=request.user,
                action="promotion.updated" if promotion_id else "promotion.created",
                object_type="Promotion",
                object_id=str(promotion.pk),
                metadata={"name": promotion.name, "active": promotion.active},
            )
            OutboxEvent.objects.create(
                business=business,
                event_type="menu.update",
                payload={"promotion_id": promotion.pk},
                next_attempt_at=timezone.now(),
            )
            messages.success(request, "Promotion saved.")
            return redirect("promotions", slug=slug)
    return render(
        request,
        "core/promotions.html",
        {
            "business": business,
            "promotions": business.promotions.order_by("-active", "-priority", "name"),
            "form": form,
            "editing": promotion is not None,
            "can_manage": membership.allows("catalog.manage"),
        },
    )


@login_required
def locations(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("locations.view"):
        return HttpResponseForbidden("You do not have access to locations.")
    can_manage = membership.allows("locations.manage")
    form = LocationForm(request.POST or None)
    form.instance.business = membership.business
    if request.method == "POST":
        if not can_manage:
            return HttpResponseForbidden("You cannot manage locations.")
        action = request.POST.get("action")
        if action in {"enable_pickup", "disable_pickup"}:
            location_id = request.POST.get("location", "")
            if not location_id.isascii() or not location_id.isdecimal() or len(location_id) > 19:
                raise Http404
            with transaction.atomic():
                location = get_object_or_404(
                    membership.business.locations.select_for_update(), pk=location_id, active=True,
                )
                enabled = action == "enable_pickup"
                if enabled != bool(location.takeaway_version):
                    location.takeaway_version = uuid.uuid4() if enabled else None
                    location.save(update_fields=["takeaway_version"])
                    AuditEvent.objects.create(
                        business=membership.business, actor=request.user,
                        action=f"location.{action}", object_type="Location", object_id=str(location.pk),
                    )
                    OutboxEvent.objects.create(
                        business=membership.business, event_type="menu.update",
                        payload={"reason": "location.pickup_changed"}, next_attempt_at=timezone.now(),
                    )
            messages.success(request, "Takeaway enabled." if enabled else "Takeaway disabled.")
            return redirect("locations", slug=slug)
        if form.is_valid():
            try:
                create_location(
                    business=membership.business,
                    actor=request.user,
                    name=form.cleaned_data["name"],
                    address=form.cleaned_data["address"],
                )
            except PermissionError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Location added.")
                return redirect("locations", slug=slug)
    locations = membership.business.locations.order_by("name")
    for location in locations:
        if location.active and location.takeaway_version:
            location.pickup_path = reverse("pickup_menu", kwargs={"token": pickup_token(location)})
            location.pickup_url = request.build_absolute_uri(location.pickup_path)
    return render(
        request,
        "core/locations.html",
        {
            "business": membership.business,
            "locations": locations,
            "form": form,
            "can_manage": can_manage,
        },
    )


@login_required
def tables(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("tables.view") and not membership.allows("tables.manage"):
        return HttpResponseForbidden("You do not have access to tables.")
    business = membership.business
    can_manage = membership.allows("tables.manage")
    if request.method == "POST":
        if not can_manage:
            return HttpResponseForbidden("You cannot manage tables.")
        table_id = request.POST.get("table", "")
        action = request.POST.get("action")
        if action in {"revoke", "rotate", "update"}:
            table = get_object_or_404(DiningTable, pk=table_id, business=business)
            if action == "update":
                location = get_object_or_404(
                    Location,
                    pk=request.POST.get("location"),
                    business=business,
                    active=True,
                )
                name = request.POST.get("name", "").strip()
                area = request.POST.get("area", "").strip()
                if not name or len(name) > 80 or len(area) > 80:
                    messages.error(request, "Enter a table name and an area under 81 characters.")
                else:
                    table.name, table.area, table.location = name, area, location
                    try:
                        table.save(update_fields=["name", "area", "location"])
                        AuditEvent.objects.create(
                            business=business,
                            actor=request.user,
                            action="table.updated",
                            object_type="DiningTable",
                            object_id=str(table.pk),
                        )
                        messages.success(request, "Table updated.")
                    except IntegrityError:
                        messages.error(
                            request, "A table with that name already exists at this location."
                        )
            elif action == "revoke":
                table.active = False
                table.save(update_fields=["active"])
            else:
                table.qr_version = uuid.uuid4()
                table.active = True
                table.save(update_fields=["qr_version", "active"])
            if action != "update":
                AuditEvent.objects.create(
                    business=business,
                    actor=request.user,
                    action=f"table.qr_{action}d",
                    object_type="DiningTable",
                    object_id=str(table.pk),
                )
                messages.success(request, "Table QR updated.")
            return redirect("tables", slug=slug)
        location = get_object_or_404(
            Location, pk=request.POST.get("location"), business=business, active=True
        )
        name = request.POST.get("name", "").strip()
        area = request.POST.get("area", "").strip()
        if not name or len(name) > 80 or len(area) > 80:
            messages.error(request, "Enter a table name and an area under 81 characters.")
        else:
            try:
                table = DiningTable.objects.create(
                    business=business, location=location, name=name, area=area
                )
                AuditEvent.objects.create(
                    business=business,
                    actor=request.user,
                    action="table.created",
                    object_type="DiningTable",
                    object_id=str(table.pk),
                )
                return redirect("tables", slug=slug)
            except IntegrityError:
                messages.error(request, "A table with that name already exists at this location.")
    table_list = business.tables.select_related("location").order_by("location__name", "name")
    table_tokens = {
        table.pk: signing.dumps(
            [str(table.pk), str(table.qr_version)], salt="veyra-table-qr", compress=True
        )
        for table in table_list
        if table.active
    }
    for table in table_list:
        table.qr_token = table_tokens.get(table.pk)
    return render(
        request,
        "core/tables.html",
        {
            "business": business,
            "tables": table_list,
            "table_tokens": table_tokens,
            "locations": business.locations.filter(active=True).order_by("name"),
            "can_manage": can_manage,
        },
    )


@require_safe
def table_qr(request, token):
    from qrcode.image.svg import SvgPathImage

    validate_table_token(token)
    url = request.build_absolute_uri(reverse("table_order", kwargs={"token": token}))
    image = qrcode.make(url, image_factory=SvgPathImage, box_size=8, border=4)
    output = io.BytesIO()
    image.save(output)
    response = HttpResponse(output.getvalue(), content_type="image/svg+xml")
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET", "POST"])
def table_order(request, token):
    if request.method == "GET":
        from .customer_views import menu

        return menu(request, token)
    table = validate_table_token(token)
    subscription_allows_orders = business_entitlements(table.business)["active"] and (
        "*" in business_entitlements(table.business)["features"]
        or "qr_ordering" in business_entitlements(table.business)["features"]
    )
    products = table.business.products.select_related("category").filter(active=True)
    products = promotional_ordering(
        business=table.business, products=products, location=table.location
    )
    providers = table.business.payment_connections.filter(verified_at__isnull=False).order_by("provider")
    error = None
    if not subscription_allows_orders:
        error = "Ordering is temporarily unavailable for this business."
    if request.method == "POST" and subscription_allows_orders:
        items = []
        try:
            method = request.POST.get("payment_method", "counter")
            provider = ""
            if method.startswith("qris:"):
                provider = method.partition(":")[2]
                if not providers.filter(provider=provider).exists():
                    raise ValueError("That payment provider is not available")
                method = Order.PaymentMethod.QRIS
            elif method == Order.PaymentMethod.COUNTER:
                method = Order.PaymentMethod.COUNTER
            else:
                raise ValueError("Choose a payment method")
            for product in products:
                quantity = int(request.POST.get(f"quantity_{product.pk}", "0"))
                if quantity:
                    items.append((product.pk, quantity))
            order = create_order(
                business=table.business,
                actor=None,
                items=items,
                idempotency_key=request.POST.get("idempotency_key"),
                order_type=Order.OrderType.DINE_IN,
                table=table,
                note=request.POST.get("note", ""),
                guest_tracking_token=request.POST.get("tracking_token", ""),
                payment_method=method,
                payment_provider=provider,
            )
            if order.guest_tracking_digest != hashlib.sha256(
                request.POST.get("tracking_token", "").encode()
            ).hexdigest():
                raise ValueError("Order retry token did not match")
            if method == Order.PaymentMethod.QRIS and not order.payments.filter(
                status=PaymentTransaction.Status.PENDING, expires_at__gt=timezone.now()
            ).exists():
                try:
                    initiate_qris_payment(
                        business=table.business,
                        order_id=order.pk,
                        provider=provider,
                        guest_tracking_token=request.POST["tracking_token"],
                    )
                except (ValueError, PaymentConnection.DoesNotExist, OSError):
                    messages.error(
                        request,
                        "Your order was received, but QRIS could not start. Try again from order tracking or pay at the counter.",
                    )
            response = redirect("guest_order_tracking", order_id=order.pk)
            cookie_name = order_tracking_cookie_name(order.pk)
            response.set_signed_cookie(
                cookie_name,
                request.POST["tracking_token"],
                salt="veyra-order-tracking",
                max_age=ORDER_TRACKING_MAX_AGE,
                httponly=True,
                secure=request.is_secure(),
                samesite="Lax",
            )
            return response
        except (ValueError, Product.DoesNotExist, DiningTable.DoesNotExist):
            error = "We could not place that order. Check quantities and try again."
    return render(
        request,
        "core/table_order.html",
        {
            "table": table,
            "table_token": token,
            "products": products,
            "providers": providers,
            "idempotency_key": uuid.uuid4(),
            "tracking_token": uuid.uuid4().hex + uuid.uuid4().hex,
            "error": error,
        },
    )


@require_safe
def table_menu_data(request, token):
    from .media_services import menu_banners
    table = validate_table_token(token)
    products = promotional_ordering(
        business=table.business,
        products=table.business.products.select_related("category", "image")
        .defer("image__content").filter(active=True),
        location=table.location,
    )
    return JsonResponse({"products": [
        {
            "id": product.pk,
            "name": product.name,
            "image_url": reverse("guest_product_image", args=[token, product.pk])
            if hasattr(product, "image") else None,
            "image_alt": product.image.alt_text if hasattr(product, "image") else "",
            "base_price": str(product.price),
            "price": str(product.display_price),
            "promotions": [promotion.name for promotion in product.price_quote.promotions],
        }
        for product in products
    ], "banners": menu_banners(table.business, table.location, token)})


@require_http_methods(["GET", "POST"])
def guest_order_tracking(request, order_id):
    cookie_name = order_tracking_cookie_name(order_id)
    order = order_for_tracking_cookie(order_id, request.COOKIES.get(cookie_name))
    if order is None:
        raise Http404
    if request.method == "POST":
        if order.payment_method != Order.PaymentMethod.QRIS:
            return HttpResponseForbidden("This order does not have QRIS enabled.")
        try:
            initiate_qris_payment(
                business=order.business,
                order_id=order.pk,
                provider=order.payment_provider,
                guest_tracking_token=order_tracking_cookie_signer(order.pk).unsign(
                    request.COOKIES[cookie_name], max_age=ORDER_TRACKING_MAX_AGE
                ),
            )
        except (ValueError, PaymentConnection.DoesNotExist, signing.BadSignature, OSError):
            messages.error(request, "We could not start payment. Please try again or pay at the counter.")
        return redirect("guest_order_tracking", order_id=order.pk)
    payment = order.payments.order_by("-created_at").first()
    return render(request, "core/guest_order_tracking.html", {"order": order, "payment": payment})


@require_safe
def guest_order_payment_qr(request, order_id):
    order = order_for_tracking_cookie(
        order_id, request.COOKIES.get(order_tracking_cookie_name(order_id))
    )
    if order is None:
        raise Http404
    tx = order.payments.filter(
        status=PaymentTransaction.Status.PENDING, expires_at__gt=timezone.now()
    ).order_by("-created_at").first()
    if not tx or not tx.qr_payload:
        raise Http404
    from qrcode.image.svg import SvgPathImage

    image = qrcode.make(tx.qr_payload, image_factory=SvgPathImage, box_size=8, border=4)
    output = io.BytesIO()
    image.save(output)
    response = HttpResponse(output.getvalue(), content_type="image/svg+xml")
    response["Cache-Control"] = "no-store"
    return response


@login_required
def customers(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("customers.view"):
        return HttpResponseForbidden("You do not have access to customers.")
    can_manage = membership.allows("customers.manage")
    form = CustomerForm(request.POST or None)
    if request.method == "POST":
        if not can_manage:
            return HttpResponseForbidden("You cannot manage customers.")
        if form.is_valid():
            customer = form.save(commit=False)
            customer.business = membership.business
            customer.save()
            AuditEvent.objects.create(
                business=membership.business,
                actor=request.user,
                action="customer.created",
                object_type="Customer",
                object_id=str(customer.pk),
            )
            messages.success(request, "Customer added.")
            return redirect("customers", slug=slug)
    query = request.GET.get("q", "").strip()[:100]
    customer_list = membership.business.customers.order_by("name")
    if query:
        customer_list = customer_list.filter(name__icontains=query)
    page = Paginator(customer_list, 25).get_page(request.GET.get("page"))
    pagination_query = request.GET.copy()
    pagination_query.pop("page", None)
    return render(
        request,
        "core/customers.html",
        {
            "business": membership.business,
            "customers": page.object_list,
            "page_obj": page,
            "total_count": page.paginator.count,
            "pagination_query": pagination_query.urlencode() + "&" if pagination_query else "",
            "form": form,
            "query": query,
            "can_manage": can_manage,
        },
    )


@login_required
def inventory(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("inventory.view"):
        return HttpResponseForbidden("You do not have access to inventory.")
    business = membership.business
    can_manage = membership.allows("inventory.manage")
    allowed_locations = membership.allowed_inventory_locations()
    add_form = InventoryItemForm(
        request.POST if request.POST.get("action") == "add" else None, business=business
    )
    adjustment_form = StockAdjustmentForm(
        request.POST if request.POST.get("action") == "adjust" else None,
        business=business,
    )
    for form in (add_form, adjustment_form):
        form.fields['location'].queryset = allowed_locations.filter(active=True).order_by('name')
    error = None
    idempotency_key = request.POST.get("idempotency_key") or str(uuid.uuid4())
    if request.method == "POST":
        if not can_manage:
            return HttpResponseForbidden("You cannot manage inventory.")
        try:
            if request.POST.get("action") == "add" and add_form.is_valid():
                data = add_form.cleaned_data
                create_inventory_item(
                    business=business,
                    actor=request.user,
                    name=data["name"],
                    unit=data["unit"],
                    sku=data["sku"],
                    location_id=data["location"].pk,
                    opening_stock=data["opening_stock"],
                )
                messages.success(request, "Inventory item added.")
                return redirect("inventory", slug=slug)
            if request.POST.get("action") == "adjust" and adjustment_form.is_valid():
                data = adjustment_form.cleaned_data
                adjust_stock(
                    business=business,
                    actor=request.user,
                    item_id=data["item"].pk,
                    location_id=data["location"].pk,
                    quantity_delta=data["quantity_delta"],
                    reason=data["reason"],
                    idempotency_key=idempotency_key,
                    note=data["note"],
                )
                messages.success(request, "Stock updated.")
                return redirect("inventory", slug=slug)
        except PermissionError:
            return HttpResponseForbidden("You cannot manage inventory.")
        except (
            ValueError,
            InventoryBalance.DoesNotExist,
            InventoryItem.DoesNotExist,
            Location.DoesNotExist,
        ):
            error = "We could not update stock. Check the item, location, and quantity."
    items = (
        business.inventory_items.filter(active=True)
        .prefetch_related(Prefetch("balances", queryset=InventoryBalance.objects.filter(location__in=allowed_locations).select_related("location")))
        .order_by("name")
    )
    return render(
        request,
        "core/inventory.html",
        {
            "business": business,
            "items": items,
            "movements": business.stock_movements.filter(location__in=allowed_locations).select_related(
                "item", "location", "actor", "receiving_line__receipt__purchase"
            ).order_by("-created_at")[:40],
            "locations": allowed_locations.filter(active=True),
            "add_form": add_form,
            "adjustment_form": adjustment_form,
            "can_manage": can_manage,
            "error": error,
            "idempotency_key": idempotency_key,
        },
    )


@login_required
def recipes(request, slug):
    membership = membership_for(request, slug)
    if not membership.allows("inventory.manage"):
        return HttpResponseForbidden("You do not have access to recipe management.")
    business = membership.business
    form = RecipeLineForm(request.POST or None, business=business)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            line, _ = RecipeLine.objects.select_for_update().get_or_create(
                product=data["product"],
                item=data["item"],
                defaults={"quantity": data["quantity"]},
            )
            line.quantity = data["quantity"]
            line.full_clean()
            line.save()
            AuditEvent.objects.create(
                business=business,
                actor=request.user,
                action="inventory.recipe_updated",
                object_type="RecipeLine",
                object_id=str(line.pk),
                metadata={"product_id": str(line.product_id), "item_id": str(line.item_id)},
            )
        messages.success(request, "Recipe ingredient saved.")
        return redirect("recipes", slug=slug)
    recipe_products = business.products.prefetch_related("recipe_lines__item").order_by("name")
    return render(
        request,
        "core/recipes.html",
        {"business": business, "form": form, "products": recipe_products},
    )


def _pos_configurations(data, product):
    product_id = str(product.pk)
    key = f"quantity_{product_id}"
    extra_keys = [
        input_name for input_name in data
        if input_name.startswith(f"{key}__") and re.fullmatch(r"[1-9][0-9]{0,2}", input_name.split("__")[1])
        and input_name.count("__") == 1
    ]
    configurations = []
    for input_name in (key, *extra_keys):
        suffix = input_name.removeprefix(key)
        selected = submitted_options(data, f"modifiers_{product_id}{suffix}")
        configurations.append({
            "input_name": input_name, "option_name": f"modifiers_{product_id}{suffix}",
            "quantity": data.get(input_name, "0"), "selected": selected,
            "index": suffix.removeprefix("__") or "0",
        })
    return configurations


@login_required
def point_of_sale(request, slug):
    membership = membership_for(request, slug)
    business = membership.business
    can_create = membership.allows("orders.create") and membership.allows("catalog.view")
    locations = business.locations.filter(active=True).order_by("name")
    location_id = (request.POST if request.method == "POST" else request.GET).get("location", "")
    valid_location_id = location_id.isascii() and location_id.isdecimal() and len(location_id) <= 19
    selected_location = locations.filter(pk=location_id).first() if valid_location_id else None
    products = business.products.select_related("category", "image").defer("image__content").filter(active=True)
    products = promotional_ordering(
        business=business, products=products, location=selected_location
    )
    groups = modifier_catalog(
        business=business, product_ids=[product.pk for product in products],
        location=selected_location, include_other_locations=True,
    )
    for product in products:
        product.order_modifier_groups = groups[product.pk]
        product.order_configurations = _pos_configurations(request.POST, product)
    error = None
    response_status = 200
    action = request.POST.get("action", "")
    voucher_code = request.POST.get("voucher_code", "").strip()[:32]
    voucher_token = ""
    voucher = None
    preview_json = action == "apply_voucher" and request.headers.get("Accept") == "application/json"
    if request.method == "POST":
        if not can_create:
            return HttpResponseForbidden("You cannot create orders for this business.")
        items = []
        try:
            if location_id and selected_location is None:
                raise ValueError("Choose one of this business’s locations.")
            for product in products:
                for configuration in product.order_configurations:
                    quantity = int(configuration["quantity"])
                    if quantity:
                        items.append((product.pk, quantity, configuration["selected"]))
            if action == "apply_voucher":
                check_voucher_attempt(request, business)
                require_business_feature(business, "pos")
                require_business_feature(business, "promotions")
                customer = None
                customer_id = request.POST.get("customer", "")
                if customer_id:
                    if not membership.allows("customers.view"):
                        raise PermissionError
                    if not customer_id.isascii() or not customer_id.isdecimal() or len(customer_id) > 18:
                        raise ValueError("Choose a customer from this business.")
                    customer = business.customers.get(pk=customer_id)
                lines = price_order_items(business=business, items=items, location=selected_location)
                order_type = request.POST.get("order_type")
                voucher = calculate_voucher(
                    business=business, code=request.POST.get("voucher_code", ""), lines=lines,
                    location=selected_location, order_type=order_type, customer=customer,
                )
                voucher_code = voucher.voucher.code
                voucher_token = signing.dumps(pos_voucher_approval(
                    business=business, location=selected_location, order_type=order_type,
                    customer=customer, lines=lines, applied=voucher,
                ), salt="veyra-pos-voucher")
                if preview_json:
                    return JsonResponse({
                        "code": voucher_code, "token": voucher_token,
                        "discount": str(voucher.discount), "total": str(voucher.total),
                    })
            elif action == "remove_voucher":
                voucher_code = ""
            else:
                order = create_order(
                    business=business,
                    actor=request.user,
                    items=items,
                    idempotency_key=request.POST.get("idempotency_key"),
                    order_type=request.POST.get("order_type"),
                    table_label=request.POST.get("table_label", ""),
                    note=request.POST.get("note", ""),
                    customer_id=request.POST.get("customer") or None,
                    location_id=selected_location.pk if selected_location else None,
                    voucher_code=request.POST.get("voucher_code", ""),
                    voucher_quote_token=request.POST.get("voucher_quote_token", ""),
                )
                messages.success(request, f"Order {str(order.pk)[:8]} created.")
                return redirect("point_of_sale", slug=slug)
        except VoucherRateLimit as exc:
            error = str(exc)
            response_status = 429
        except ValueError as exc:
            error = str(exc)
            response_status = 400
        except (Customer.DoesNotExist, Product.DoesNotExist, Location.DoesNotExist,
                Membership.DoesNotExist, PermissionError):
            error = "We could not price those items. Check the location, customer and quantities."
            if action == "apply_voucher":
                response_status = 400
    if preview_json:
        return JsonResponse({"error": error}, status=response_status)
    orders = (
        list(business.orders.order_by("-created_at")[:10])
        if membership.allows("orders.view")
        else []
    )
    if membership.allows("orders.manage"):
        for order in orders:
            order.payment_key = uuid.uuid4()
    return render(
        request,
        "core/pos.html",
        {
            "business": business,
            "products": products,
            "locations": locations,
            "selected_location": selected_location,
            "orders": orders,
            "can_see_orders": membership.allows("orders.view"),
            "can_manage_orders": membership.allows("orders.manage"),
            "can_select_customer": membership.allows("customers.view"),
            "customers": business.customers.order_by("name")[:100]
            if membership.allows("customers.view")
            else [],
            "can_create": can_create,
            "idempotency_key": request.POST.get("idempotency_key") or uuid.uuid4(),
            "order_type": request.POST.get("order_type", Order.OrderType.TAKEAWAY),
            "table_label": request.POST.get("table_label", ""),
            "order_note": request.POST.get("note", ""),
            "selected_customer": request.POST.get("customer", ""),
            "voucher": voucher,
            "voucher_code": voucher_code,
            "voucher_token": voucher_token,
            "error": error,
        },
        status=response_status,
    )


@login_required
@require_POST
def record_payment(request, slug, order_id):
    membership = membership_for(request, slug)
    if not membership.allows("orders.manage") or not membership.allows("orders.view"):
        return HttpResponseForbidden("You cannot record payments for these orders.")
    try:
        tender = record_order_payment(
            business=membership.business,
            order_id=order_id,
            method=request.POST.get("method", ""),
            actor=request.user,
            idempotency_key=request.POST.get("idempotency_key", ""),
            cash_received=request.POST.get("cash_received", ""),
            reference=request.POST.get("reference", ""),
        )
    except Order.DoesNotExist as error:
        raise Http404 from error
    except PermissionError:
        return HttpResponseForbidden("You cannot record payments for these orders.")
    except (ValueError, OrderTender.DoesNotExist) as error:
        messages.error(request, str(error))
        return redirect("point_of_sale", slug=slug)
    if tender.method == OrderTender.Method.CASH:
        messages.success(
            request,
            f"Cash payment recorded. Change due: Rp {tender.change_due:,.0f}.",
        )
    else:
        messages.success(request, "Card payment recorded after terminal confirmation.")
    return redirect("point_of_sale", slug=slug)


@login_required
def team(request, slug):
    membership = membership_for(request, slug)
    can_view = membership.allows("employees.view") or membership.allows("employees.manage")
    if not can_view:
        return HttpResponseForbidden("You do not have access to this team.")
    can_manage = membership.allows("employees.manage")
    form = InvitationForm(request.POST or None)
    if request.method == "POST":
        if not can_manage:
            return HttpResponseForbidden("You cannot manage employees.")
        action = request.POST.get("action")
        if action == "revoke_invitation":
            try:
                revoke_invitation(
                    business=membership.business,
                    invitation_id=request.POST.get("invitation_id"),
                    actor=request.user,
                )
                messages.success(request, "Invitation revoked.")
            except (Invitation.DoesNotExist, ValueError) as error:
                messages.error(request, str(error))
            except PermissionError:
                return HttpResponseForbidden("You cannot manage employees.")
            return redirect("team", slug=slug)
        if form.is_valid():
            try:
                invitation, token = create_invitation(
                    business=membership.business,
                    actor=request.user,
                    email=form.cleaned_data["email"],
                    permissions=form.cleaned_data["permissions"],
                )
                link = request.build_absolute_uri(
                    reverse(
                        "accept_invitation",
                        kwargs={"slug": slug, "token": token},
                    )
                )
                try:
                    sent = send_account_email(
                        f"Join {membership.business.name} on Veyra",
                        f"{request.user.get_full_name() or request.user.email} invited you to "
                        f"{membership.business.name}. Sign in with {invitation.email} and accept "
                        f"your invitation within 7 days: {link}",
                        invitation.email,
                    )
                except Exception:
                    Invitation.objects.filter(pk=invitation.pk).update(expires_at=timezone.now())
                    messages.error(request, "We could not send the invitation email.")
                    return redirect("team", slug=slug)
                if not sent:
                    Invitation.objects.filter(pk=invitation.pk).update(expires_at=timezone.now())
                    raise ValueError("We could not send the invitation email.")
                messages.success(request, f"Invitation sent to {invitation.email}.")
                return redirect("team", slug=slug)
            except PermissionError:
                return HttpResponseForbidden("You cannot grant those team permissions.")
            except ValueError as error:
                messages.error(request, str(error))
    memberships = (
        membership.business.memberships.filter(active=True)
        .select_related("user")
        .order_by("created_at")[:100]
    )
    return render(
        request,
        "core/team.html",
        {
            "business": membership.business,
            "memberships": memberships,
            "invitations": membership.business.invitations.filter(
                accepted_at__isnull=True, expires_at__gt=timezone.now()
            ).order_by("-created_at")[:50],
            "form": form,
            "can_manage": can_manage,
        },
    )


@login_required
def stations(request, slug):
    business = get_object_or_404(Business, slug=slug, owner=request.user)
    create_form = StationForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") == "create"
        else None,
        business=business,
    )
    printer_form = PrinterForm(
        request.POST
        if request.method == "POST" and request.POST.get("action") == "create_printer"
        else None,
        business=business,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create" and create_form.is_valid():
            data = create_form.cleaned_data
            try:
                create_station(
                    business=business,
                    actor=request.user,
                    name=data["name"],
                    kind=data["kind"],
                    handling_mode=data.get("handling_mode", Station.HandlingMode.BOTH),
                    printer_id=data["printer"].pk if data.get("printer") else None,
                    location_id=data["location"].pk if data["location"] else None,
                    capabilities=data["capabilities"],
                )
                messages.success(
                    request, "Station created. Issue a pairing code when its device is ready."
                )
                return redirect("stations", slug=slug)
            except (PermissionError, ValueError, ValidationError) as error:
                messages.error(request, str(error))
        elif action == "create_printer" and printer_form.is_valid():
            data = printer_form.cleaned_data
            try:
                create_printer(
                    business=business,
                    actor=request.user,
                    name=data["name"],
                    printer_type=data["printer_type"],
                    paper_width=data["paper_width"],
                    location_id=data["location"].pk if data.get("location") else None,
                )
                messages.success(request, "Printer configured.")
                return redirect("stations", slug=slug)
            except (PermissionError, ValueError, ValidationError) as error:
                messages.error(request, str(error))
        elif action == "pair":
            station = get_object_or_404(
                Station, business=business, pk=request.POST.get("station_id")
            )
            try:
                _, code = issue_station_pairing_code(station=station, actor=request.user)
            except PermissionError as error:
                messages.error(request, str(error))
                return redirect("stations", slug=slug)
            messages.success(
                request,
                f"One-time code for {station.name}: {code}. It expires in 5 minutes.",
            )
            return redirect("stations", slug=slug)
        elif action == "revoke":
            try:
                revoke_paired_device(
                    business=business,
                    device_id=request.POST.get("device_id"),
                    actor=request.user,
                )
            except (PairedDevice.DoesNotExist, ValueError):
                raise Http404
            messages.success(request, "Device session revoked.")
            return redirect("stations", slug=slug)
    return render(
        request,
        "core/stations.html",
        {
            "business": business,
            "stations": business.stations.select_related("location", "printer")
            .prefetch_related("devices")
            .order_by("name"),
            "printers": business.printers.select_related("location").order_by("name"),
            "form": create_form,
            "printer_form": printer_form,
        },
    )


@login_required
def station_screen(request, slug, station_id):
    membership = membership_for(request, slug)
    if not membership.allows("orders.view"):
        return HttpResponseForbidden("You do not have access to operational orders.")
    station = get_object_or_404(Station, pk=station_id, business=membership.business, active=True)

    if request.method == "POST":
        if not membership.allows("orders.manage"):
            return HttpResponseForbidden("You cannot change operational orders.")
        action = request.POST.get("action")
        order_id = request.POST.get("order_id")
        if action == "advance":
            next_status = request.POST.get("status")
            try:
                transition_order(
                    business=membership.business,
                    order_id=order_id,
                    status=next_status,
                    actor=request.user,
                )
                messages.success(request, f"Order status updated to {next_status}.")
            except (ValueError, PermissionError, Order.DoesNotExist):
                messages.error(request, "We could not update the order. Check its current status and access.")
        elif action == "reprint":
            try:
                reprint_order_ticket(
                    business=membership.business,
                    order_id=order_id,
                    station_id=station.pk,
                    actor=request.user,
                )
                messages.success(request, "Preparation ticket reprint queued.")
            except (ValueError, PermissionError, Order.DoesNotExist, Station.DoesNotExist):
                messages.error(request, "We could not queue that ticket. Check the order and station.")
        return redirect("station_screen", slug=slug, station_id=station.pk)

    orders_qs = Order.objects.filter(
        business=membership.business,
        status__in=[Order.Status.NEW, Order.Status.PREPARING, Order.Status.READY],
    )
    if station.location_id:
        orders_qs = orders_qs.filter(location_id=station.location_id)

    orders = list(orders_qs.prefetch_related("lines").order_by("created_at"))
    print_jobs = PrintJob.objects.filter(
        business=membership.business, station=station
    ).order_by("-created_at")[:10]

    return render(
        request,
        "core/station_screen.html",
        {
            "business": membership.business,
            "station": station,
            "orders": orders,
            "print_jobs": print_jobs,
            "can_manage_orders": membership.allows("orders.manage"),
            "now": timezone.now(),
        },
    )


@login_required
def payment_settings(request, slug):
    business = get_object_or_404(Business, slug=slug, owner=request.user)
    form = PaymentConnectionForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "save" else None
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save" and form.is_valid():
            try:
                save_payment_connection(
                    business=business,
                    actor=request.user,
                    provider=form.cleaned_data["provider"],
                    environment=form.cleaned_data["environment"],
                    merchant_id=form.cleaned_data["merchant_id"],
                    credentials=form.cleaned_data["credential_bundle"],
                )
                messages.success(
                    request,
                    "Credentials encrypted and saved. Provider verification is not configured yet.",
                )
                return redirect("payment_settings", slug=slug)
            except (PermissionError, ValueError, RuntimeError) as error:
                messages.error(request, str(error))
        elif action == "verify":
            try:
                valid, msg = verify_payment_connection(
                    business=business,
                    connection_id=request.POST.get("connection_id"),
                    actor=request.user,
                )
                if valid:
                    messages.success(request, f"Connection verified: {msg}")
                else:
                    messages.error(request, f"Verification failed: {msg}")
            except Exception as error:
                messages.error(request, str(error))
            return redirect("payment_settings", slug=slug)
        elif action == "disconnect":
            try:
                delete_payment_connection(
                    business=business,
                    connection_id=request.POST.get("connection_id"),
                    actor=request.user,
                )
            except PaymentConnection.DoesNotExist:
                raise Http404
            messages.success(request, "Merchant credentials removed.")
            return redirect("payment_settings", slug=slug)
    return render(
        request,
        "core/payments.html",
        {
            "business": business,
            "connections": business.payment_connections.order_by("provider"),
            "transactions": business.payment_transactions.select_related("order").order_by("-created_at")[:20],
            "form": form,
        },
    )


@never_cache
def accept_invitation_view(request, slug, token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    invitation = Invitation.objects.select_related("business").filter(
        business__slug=slug, token_digest=digest,
    ).first()
    if not invitation:
        return render(request, "core/invitation_accept.html", {"valid": False}, status=404)
    valid = invitation.accepted_at is None and invitation.expires_at > timezone.now()
    authenticated = request.user.is_authenticated
    email_matches = authenticated and invitation.email.lower() == request.user.email.lower()
    if request.method == "POST":
        if not authenticated:
            login_url = reverse("login") + "?" + urlencode({"next": request.get_full_path()})
            return redirect(login_url)
        if not valid:
            return render(request, "core/invitation_accept.html", {"valid": False}, status=400)
        if not email_matches:
            return render(request, "core/invitation_accept.html", {
                "valid": True, "authenticated": True, "email_matches": False,
            }, status=403)
        try:
            accepted_membership = accept_invitation(
                token=token,
                business_slug=slug,
                actor=request.user,
            )
        except PermissionError as error:
            return HttpResponseForbidden(str(error))
        except (Invitation.DoesNotExist, ValueError):
            return render(request, "core/invitation_accept.html", {"valid": False}, status=400)
        return redirect("business_dashboard", slug=accepted_membership.business.slug)
    return render(
        request,
        "core/invitation_accept.html",
        {
            "valid": valid,
            "email_matches": email_matches,
            "authenticated": authenticated,
            "invitation": invitation,
            "login_url": reverse("login") + "?" + urlencode({"next": request.get_full_path()}),
            "register_url": reverse("register")
            + "?"
            + urlencode({"next": request.get_full_path()}),
        },
    )


@login_required
@require_POST
def update_order_status(request, slug, order_id):
    membership = membership_for(request, slug)
    try:
        transition_order(
            business=membership.business,
            order_id=order_id,
            status=request.POST.get("status", ""),
            actor=request.user,
        )
    except Order.DoesNotExist as error:
        raise Http404 from error
    except PermissionError:
        return HttpResponseForbidden("You cannot manage orders for this business.")
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("point_of_sale", slug=slug)
    messages.success(request, "Order status updated.")
    return redirect("point_of_sale", slug=slug)
