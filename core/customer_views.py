"""Anonymous customer carts scoped to the browser and a signed menu link."""

import hashlib
import uuid
from decimal import Decimal
from secrets import token_urlsafe

from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import salted_hmac
from django.views.decorators.http import require_http_methods, require_safe

from .media_services import menu_banners
from .models import (
    DiningTable,
    GuestCart,
    Location,
    Order,
    OrderingPageSnapshot,
    PaymentConnection,
    PaymentTransaction,
    Product,
)
from .modifiers import modifier_catalog, normalize_options, price_modifiers, submitted_options
from .ordering_page import (
    add_presentation,
    menu_colors,
    published_design,
    settings_for,
    visible_banners,
)
from .permissions import ORDER_TRACKING_MAX_AGE, order_tracking_cookie_name
from .pricing import VoucherError, calculate_voucher, price_order_line, promotional_ordering
from .services import business_entitlements, create_order, initiate_qris_payment
from .storefront import resolve_storefront
from .voucher_limits import VoucherRateLimit, check_voucher_attempt


def _cart(request, storefront):
    if not request.session.session_key:
        request.session.create()
    session_hash = salted_hmac("veyra-customer-cart", request.session.session_key).hexdigest()
    cart, _ = GuestCart.objects.get_or_create(
        session_hash=session_hash, qr_version=storefront.version,
        table=storefront.table,
        pickup_location=None if storefront.table else storefront.location,
    )
    return cart


def _line_selection(key, value):
    if isinstance(value, int):
        return key, value, []
    return value["product"], value["quantity"], value["modifiers"]


def _line_key(product_id, selected):
    if not selected:
        return product_id
    return f"{product_id}:{','.join(map(str, selected))}"


def guest_catalog(business, location):
    products = promotional_ordering(
        business=business,
        products=business.products.filter(active=True).select_related("category", "image").defer("image__content"),
        location=location,
    )
    categories = {product.category_id: product.category for product in products if product.category and product.category.active}
    groups = modifier_catalog(business=business, product_ids=[p.pk for p in products], location=location)
    for product in products:
        product.order_modifier_groups = groups[product.pk]
    return products, sorted(categories.values(), key=lambda category: (category.sort_order, category.name.casefold()))


def _context(storefront, token, cart, draft_type=None):
    table, business, location = storefront.table, storefront.business, storefront.location
    products, categories = guest_catalog(business, location)
    products_by_id = {str(product.pk): product for product in products}
    items = cart.items if not cart.order_id else {}
    lines, priced_lines = [], []
    unavailable_items = set()
    total = subtotal = Decimal("0.00")
    for key, value in items.items():
        product_id, quantity, selected = _line_selection(key, value)
        product = products_by_id.get(product_id)
        if product is None:
            unavailable_items.add(key)
            continue
        line = {"key": key, "product": product, "quantity": quantity, "selected": list(map(str, selected))}
        try:
            priced = price_order_line(
                product, quantity, selected, product.order_modifier_groups, product.price_quote,
            )
        except ValueError as error:
            line["option_error"] = str(error)
        else:
            line.update(modifiers=priced.modifiers, unit_price=priced.unit_price, total=priced.total)
            total += priced.total
            subtotal += priced.base_unit_price * quantity
            priced_lines.append(priced)
        lines.append(line)
    entitlements = business_entitlements(business)
    features = set(entitlements["features"])
    allowed = entitlements["active"] and bool({"*", "qr_ordering"} & features)
    vouchers_allowed = allowed and bool({"*", "promotions"} & features)
    dine_in_available = table is not None
    takeaway_available = location.takeaway_version is not None
    order_type = Order.OrderType.TAKEAWAY if not table or (cart.prefers_takeaway and takeaway_available) else Order.OrderType.DINE_IN
    order_type = draft_type or order_type
    promotion_discount = subtotal - total
    applied = voucher_error = None
    voucher_code = cart.voucher_code if not cart.order_id else ""
    if voucher_code:
        try:
            if not vouchers_allowed:
                raise VoucherError("Vouchers are temporarily unavailable for this business.")
            applied = calculate_voucher(
                business=business, code=voucher_code, lines=priced_lines, location=location, order_type=order_type,
            )
        except VoucherError as error:
            voucher_error = str(error)
        else:
            total = applied.total
    quote = {"revision": cart.revision, "total": str(total), "takeaway_available": takeaway_available,
             "lines": [line.approval() for line in priced_lines]}
    if applied:
        quote.update(voucher=applied.fingerprint(), order_type=order_type)
    design = published_design(business)
    is_snapshot = isinstance(design, OrderingPageSnapshot)
    asset_version = None
    if design:
        asset_version = design.saved_at if is_snapshot else design.updated_at
    providers = business.payment_connections.filter(verified_at__isnull=False)
    if total <= 0:
        providers = providers.none()
    elif total != total.to_integral_value():
        providers = providers.exclude(provider=PaymentConnection.Provider.MIDTRANS)
    selected_payment = cart.payment_method
    if selected_payment != "counter" and not providers.filter(provider=selected_payment.partition(":")[2]).exists():
        selected_payment = "counter"
    context = {
        "business": business, "location": location, "table": table, "table_token": token,
        "cart": cart, "products": products,
        "menu_url": reverse("table_order" if table else "pickup_menu", args=[token]),
        "cart_url": reverse("your_order" if table else "pickup_cart", args=[token]),
        "order_type": order_type, "dine_in_available": dine_in_available,
        "takeaway_available": takeaway_available,
        "categories": categories,
        "lines": lines, "cart_count": sum(_line_selection(key, value)[1] for key, value in items.items()),
        "total": total, "invalid_options": any(line.get("option_error") for line in lines),
        "products_by_id": products_by_id,
        "subtotal": subtotal, "discount": promotion_discount,
        "priced_lines": priced_lines,
        "voucher": applied, "voucher_code": voucher_code, "voucher_error": voucher_error,
        "vouchers_allowed": vouchers_allowed,
        "voucher_input": voucher_code, "selected_payment": selected_payment, "checkout_note": cart.note,
        "cart_quote": signing.dumps(quote, salt="veyra-cart-quote"),
        "quote_data": quote, "unavailable_items": unavailable_items,
        "subscription_allows_orders": allowed,
        "providers": providers,
        "banners": menu_banners(business, location, token),
        "ordering_page": design,
        "ordering_customized": bool(design and (is_snapshot or design.appearance)),
        "ordering_asset_version": asset_version,
        # Preserve context used by older QR checkout clients.
        "idempotency_key": uuid.uuid4(), "tracking_token": uuid.uuid4().hex + uuid.uuid4().hex,
    }
    data = settings_for(design)
    add_presentation(context, data)
    context["banners"] = visible_banners(context["banners"], data)
    return context


def render_storefront(request, template, context, status=200):
    request.ordering_preview = context.get("is_preview", False)
    fragment = not request.ordering_preview and (
        request.headers.get("X-Storefront-Refresh") == "1"
        or request.headers.get("X-Cart-Update") == "quantity"
    )
    if request.ordering_preview:
        request.storefront_style_nonce = context["storefront_style_nonce"] = token_urlsafe(18)
    else:
        context["storefront_fragment"] = fragment
        _, version = storefront_stylesheet(context["ordering_design"], context["ordering_colors"])
        context["ordering_theme_url"] = reverse(
            "guest_ordering_theme", args=[context["table_token"], version]
        )
    response = render(request, template, context, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def storefront_stylesheet(data, colors):
    declarations = render_to_string("core/storefront_theme.css", {
        "ordering_design": data, "ordering_colors": colors,
    })
    css = ".storefront {\n" + declarations + "}\n"
    return css, hashlib.sha256(css.encode()).hexdigest()[:24]


@require_safe
def guest_ordering_theme(request, token, version):
    storefront = resolve_storefront(token)
    data = settings_for(published_design(storefront.business))
    css, current = storefront_stylesheet(data, menu_colors(data))
    response = HttpResponse(css, content_type="text/css; charset=utf-8")
    # Publication can change between the HTML request and its stylesheet request.
    response["Cache-Control"] = "private, max-age=300" if version == current else "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _edit_item(request, action, items, products):
    if action == "add":
        product_id = str(uuid.UUID(request.POST.get("product", "")))
        selected = normalize_options(submitted_options(request.POST, "modifiers"))
        key = _line_key(product_id, selected)
        added = int(request.POST.get("quantity", "1"))
        if not 1 <= added <= 99:
            raise ValueError("Choose a quantity between 1 and 99.")
        quantity = _line_selection(key, items[key])[1] + added if key in items else added
    else:
        key = request.POST.get("line") or request.POST.get("product", "")
        if key not in items:
            raise ValueError("This item is no longer in your order.")
        if action == "remove":
            items.pop(key)
            return
        product_id, quantity, selected = _line_selection(key, items[key])
        if action == "customize":
            selected = normalize_options(submitted_options(request.POST, "modifiers"))
        elif action == "increment":
            quantity += 1
        elif action == "decrement":
            quantity -= 1
        else:
            quantity = int(request.POST.get("quantity", "1"))
            if quantity == 0:
                items.pop(key)
                return

    product = products.get(product_id)
    if product is None:
        raise ValueError("This product is no longer available.")
    price_modifiers(product.order_modifier_groups, selected)
    target = _line_key(product_id, selected)
    if target != key and target in items:
        quantity += _line_selection(target, items[target])[1]
    if not 1 <= quantity <= 99:
        raise ValueError("Choose a quantity between 1 and 99.")
    if target != key:
        items.pop(key)
    items[target] = {"product": product_id, "quantity": quantity, "modifiers": selected} if selected else quantity


def _storefront(token, pickup):
    storefront = resolve_storefront(token)
    if pickup != (storefront.table is None):
        raise Http404
    return storefront


def _order_type(request, context):
    order_type = request.POST.get("order_type", context["order_type"])
    if order_type == Order.OrderType.TAKEAWAY and context["takeaway_available"]:
        return order_type
    if order_type == Order.OrderType.DINE_IN and context["dine_in_available"]:
        return order_type
    raise ValueError("Choose an available order type.")


@require_safe
def menu(request, token, pickup=False):
    return render_guest_menu(request, token, pickup=pickup)


def render_guest_menu(request, token, *, pickup=False, error=None):
    storefront = _storefront(token, pickup)
    context = _context(storefront, token, _cart(request, storefront))
    context["error"] = error
    return render_storefront(request, "core/customer_menu.html", context)


def _tracking_response(request, cart):
    order = cart.order
    if order.payment_method == "qris" and not order.payments.filter(
        status__in=[PaymentTransaction.Status.PENDING, PaymentTransaction.Status.PAID],
    ).exists():
        try:
            initiate_qris_payment(
                business=order.business, order_id=order.pk, provider=order.payment_provider,
                guest_tracking_token=str(cart.tracking_token),
            )
        except (ValueError, PaymentConnection.DoesNotExist, OSError):
            messages.error(request, "Your order was received. QRIS could not start. Retry from order tracking.")
    response = redirect("guest_order_tracking", order_id=order.pk)
    response.set_signed_cookie(
        order_tracking_cookie_name(order.pk), str(cart.tracking_token),
        salt="veyra-order-tracking", max_age=ORDER_TRACKING_MAX_AGE,
        httponly=True, secure=request.is_secure(), samesite="Lax",
    )
    return response


@require_http_methods(["GET", "POST"])
def your_order(request, token, pickup=False):
    storefront = _storefront(token, pickup)
    business = storefront.business
    cart = _cart(request, storefront)
    error = None
    status = 200
    draft_type = None
    partial_update = request.headers.get("X-Cart-Update") == "quantity"
    if request.method == "POST":
        try:
            with transaction.atomic():
                cart = GuestCart.objects.select_for_update().get(pk=cart.pk)
                if int(request.POST.get("revision", "-1")) != cart.revision:
                    raise ValueError("Your order changed in another tab. Review it and try again.")
                action = request.POST.get("action")
                if action == "checkout" and cart.order_id:
                    return _tracking_response(request, cart)
                context = _context(storefront, token, cart)
                if action == "checkout":
                    draft_type = _order_type(request, context)
                    if cart.voucher_code and draft_type != context["order_type"]:
                        context = _context(storefront, token, cart, draft_type)
                    if context["voucher_error"]:
                        raise VoucherError(context["voucher_error"])
                    if not context["subscription_allows_orders"]:
                        raise ValueError("Ordering is temporarily unavailable for this business.")
                    if context["unavailable_items"]:
                        raise ValueError("An item is unavailable. Remove unavailable items before checkout.")
                    if context["invalid_options"]:
                        raise ValueError("Review the options for your items before checkout.")
                    try:
                        quote = signing.loads(request.POST.get("quote", ""), salt="veyra-cart-quote", max_age=3600)
                    except signing.BadSignature:
                        raise ValueError("Review the current prices before placing your order.") from None
                    if quote != context["quote_data"]:
                        raise ValueError("Menu prices changed. Review the updated total and place your order again.")
                    method = request.POST.get("payment_method", cart.payment_method)
                    provider = ""
                    if method.startswith("qris:"):
                        provider = method.partition(":")[2]
                        if not context["providers"].filter(provider=provider).exists():
                            raise ValueError("Choose an available payment method.")
                        method = "qris"
                    elif method != "counter":
                        raise ValueError("Choose an available payment method.")
                    note = request.POST.get("note", cart.note)
                    cart.order = create_order(
                        business=business, actor=None, table=storefront.table, order_type=draft_type,
                        guest_location=storefront.location if pickup else None,
                        items=[_line_selection(key, value) for key, value in cart.items.items()],
                        idempotency_key=cart.checkout_key,
                        guest_tracking_token=str(cart.tracking_token), note=note,
                        payment_method=method, payment_provider=provider,
                        expected_total=context["total"], expected_lines=context["quote_data"]["lines"],
                        voucher_code=cart.voucher_code, expected_voucher=context["quote_data"].get("voucher"),
                    )
                    cart.note = note
                    cart.save(update_fields=["order", "note", "updated_at"])
                elif action in {"add", "update", "remove", "save", "remove_unavailable", "increment", "decrement",
                                "customize", "apply_voucher", "remove_voucher"}:
                    if cart.order_id:
                        cart.items = {}
                        cart.note = ""
                        cart.payment_method = "counter"
                        cart.prefers_takeaway = False
                        cart.voucher_code = ""
                        cart.order = None
                        cart.checkout_key = uuid.uuid4()
                        cart.tracking_token = uuid.uuid4()
                    items = dict(cart.items)
                    if action in {"add", "update", "remove", "increment", "decrement", "customize"}:
                        products = context["products_by_id"]
                        if action == "add":
                            products = {str(product.pk): product for product in context["menu_products"]}
                        _edit_item(request, action, items, products)
                    if action == "remove_unavailable":
                        items = {key: value for key, value in items.items() if key not in context["unavailable_items"]}
                    if action in {"save", "apply_voucher", "remove_voucher"}:
                        draft_type = _order_type(request, context)
                        cart.prefers_takeaway = draft_type == Order.OrderType.TAKEAWAY
                        cart.note = request.POST.get("note", cart.note)
                        if len(cart.note) > 500:
                            raise ValueError("Keep instructions to 500 characters.")
                        cart.payment_method = request.POST.get("payment_method", cart.payment_method)
                        methods = {"counter"} | {f"qris:{p.provider}" for p in context["providers"]}
                        if cart.payment_method not in methods:
                            raise ValueError("Choose an available payment method.")
                    if action == "apply_voucher":
                        check_voucher_attempt(request, business)
                        if not context["vouchers_allowed"]:
                            raise VoucherError("Vouchers are temporarily unavailable for this business.")
                        applied = calculate_voucher(
                            business=business, code=request.POST.get("voucher_code", ""),
                            lines=context["priced_lines"], location=storefront.location, order_type=draft_type,
                        )
                        cart.voucher_code = applied.voucher.code
                    elif action == "remove_voucher":
                        cart.voucher_code = ""
                    cart.items = items
                    cart.revision += 1
                    cart.save()
                else:
                    raise ValueError("Choose an order action.")
            if action == "checkout":
                return _tracking_response(request, cart)
            if request.headers.get("Accept") == "application/json":
                count = sum(_line_selection(key, value)[1] for key, value in cart.items.items())
                return JsonResponse({"count": count, "revision": cart.revision})
            if not partial_update:
                messages.success(request, {
                    "add": "Added to your order.", "apply_voucher": "Voucher applied.",
                    "remove_voucher": "Voucher removed.",
                }.get(action, "Your order was updated."))
                return redirect(context["menu_url"] if action == "add" else context["cart_url"])
        except (ValueError, PermissionError, Product.DoesNotExist, DiningTable.DoesNotExist, Location.DoesNotExist) as exc:
            storefront = _storefront(token, pickup)
            cart.refresh_from_db()
            error = str(exc) if isinstance(exc, ValueError) else "The menu changed. Review your order and try again."
            status = 429 if isinstance(exc, VoucherRateLimit) else 400
            if request.headers.get("Accept") == "application/json":
                return JsonResponse({"error": error, "revision": cart.revision}, status=status)
    context = _context(storefront, token, cart, draft_type)
    if error and request.method == "POST":
        context["voucher_input"] = request.POST.get("voucher_code", "")[:32]
        context["checkout_note"] = request.POST.get("note", cart.note)[:500]
        context["selected_payment"] = request.POST.get("payment_method", cart.payment_method)
    context["error"] = error
    template = "core/customer_menu.html" if error and request.POST.get("action") == "add" else "core/your_order.html"
    return render_storefront(request, template, context, status=status)
