import hashlib
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from .models import Order, Product, Promotion, Voucher
from .modifiers import modifier_catalog, normalize_options, price_modifiers


@dataclass(frozen=True)
class AppliedPromotion:
    name: str
    discount_amount: Decimal


@dataclass(frozen=True)
class PriceQuote:
    base_price: Decimal
    unit_price: Decimal
    promotions: tuple[AppliedPromotion, ...] = ()

    @property
    def discount_amount(self):
        return self.base_price - self.unit_price


def _is_in_window(promotion, local_now, utc_now):
    if not getattr(promotion, "permanent_offer", False):
        if promotion.starts_at and utc_now < promotion.starts_at:
            return False
        if promotion.ends_at and utc_now >= promotion.ends_at:
            return False
        if promotion.start_date and local_now.date() < promotion.start_date:
            return False
        if promotion.end_date and local_now.date() > promotion.end_date:
            return False

    allowed_days = (
        promotion.days_of_week
        if (promotion.days_of_week and len(promotion.days_of_week) < 7)
        else list(range(7))
    )

    if not promotion.start_time:
        if promotion.days_of_week and len(promotion.days_of_week) < 7:
            return local_now.weekday() in allowed_days
        return True

    start, end = promotion.start_time, promotion.end_time
    current = local_now.timetz().replace(tzinfo=None)
    if start < end:
        return local_now.weekday() in allowed_days and start <= current < end
    if current >= start:
        return local_now.weekday() in allowed_days
    if current < end:
        previous_day = (local_now.weekday() - 1) % 7
        return previous_day in allowed_days
    return False


def promotion_is_in_window(promotion, *, at=None):
    now = at or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone=timezone.get_current_timezone())
    utc_now = now.astimezone(ZoneInfo("UTC"))
    local_now = utc_now.astimezone(ZoneInfo(promotion.business.timezone_name))
    return promotion.active and _is_in_window(promotion, local_now, utc_now)


def _discounted_price(current_price, promotion):
    if promotion.discount_type == Promotion.DiscountType.FIXED_PRICE:
        price = min(current_price, promotion.value)
    elif promotion.discount_type == Promotion.DiscountType.PERCENT:
        price = current_price * (Decimal("100") - promotion.value) / Decimal("100")
    else:
        price = current_price - promotion.value
    return max(Decimal("0.00"), price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def price_product(*, product, business=None, location=None, at=None):
    """Return the authoritative price using current business-local promotion rules."""
    business = business or product.business
    if product.business_id != business.pk or not product.active:
        raise Product.DoesNotExist
    now = at or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone=timezone.get_current_timezone())
    utc_now = now.astimezone(ZoneInfo("UTC"))
    local_now = utc_now.astimezone(ZoneInfo(business.timezone_name))
    target_match = Q(products=product)
    if product.category_id:
        target_match |= Q(categories=product.category_id)
    target_match |= Q(products__isnull=True, categories__isnull=True)
    promotions = list(
        Promotion.objects.filter(business=business, active=True)
        .filter(target_match)
        .prefetch_related("products", "categories", "locations")
        .distinct()
    )
    candidates = []
    for promotion in promotions:
        product_ids = {item.pk for item in promotion.products.all()}
        category_ids = {item.pk for item in promotion.categories.all()}
        if (
            (product_ids or category_ids)
            and product.pk not in product_ids
            and product.category_id not in category_ids
        ):
            continue
        location_ids = {item.pk for item in promotion.locations.all()}
        if location_ids and (location is None or location.pk not in location_ids):
            continue
        if not _is_in_window(promotion, local_now, utc_now):
            continue
        candidate_price = _discounted_price(product.price, promotion)
        candidates.append((promotion, candidate_price))
    if not candidates:
        return PriceQuote(base_price=product.price, unit_price=product.price)

    candidates.sort(key=lambda item: (-item[0].priority, item[1], item[0].pk))
    winner, winner_price = candidates[0]
    applied = [AppliedPromotion(winner.name, product.price - winner_price)]
    current_price = winner_price
    if winner.stackable:
        for promotion, _ in candidates[1:]:
            if not promotion.stackable:
                continue
            next_price = _discounted_price(current_price, promotion)
            if next_price < current_price:
                applied.append(AppliedPromotion(promotion.name, current_price - next_price))
                current_price = next_price
    return PriceQuote(
        base_price=product.price,
        unit_price=current_price,
        promotions=tuple(applied),
    )


def promotional_ordering(*, business, products, location=None, at=None):
    products = list(products)
    quotes = {
        product.pk: price_product(product=product, business=business, location=location, at=at)
        for product in products
    }
    for product in products:
        product.price_quote = quotes[product.pk]
        product.display_price = quotes[product.pk].unit_price
    return sorted(
        products,
        key=lambda product: (
            not bool(quotes[product.pk].promotions),
            product.category.sort_order if product.category_id else 2**31,
            product.category.name if product.category_id else "",
            product.name.casefold(),
        ),
    )

@dataclass(frozen=True)
class PricedOrderLine:
    product: Product
    quantity: int
    price: PriceQuote
    modifiers: list
    surcharge: Decimal

    @property
    def unit_price(self):
        return self.price.unit_price + self.surcharge

    @property
    def base_unit_price(self):
        return self.price.base_price + self.surcharge

    @property
    def total(self):
        return self.unit_price * self.quantity

    def approval(self):
        return [str(self.product.pk), self.quantity, str(self.unit_price), self.modifiers]


def price_order_line(product, quantity, selected, groups, price):
    modifiers, surcharge = price_modifiers(groups, normalize_options(selected))
    line = PricedOrderLine(product, quantity, price, modifiers, surcharge)
    if line.base_unit_price > Decimal("9999999999.99"):
        raise ValueError("An item exceeds the supported price.")
    return line


def price_order_items(*, business, items, location):
    if not items or any(not 1 <= quantity <= 99 for _, quantity, _ in items):
        raise ValueError("Choose items with a quantity between 1 and 99.")
    groups = modifier_catalog(
        business=business, product_ids=[item[0] for item in items],
        location=location, include_other_locations=location is None,
    )
    products = business.products.filter(active=True).select_related("category").in_bulk(groups)
    now = timezone.now()
    lines = []
    for product_id, quantity, selected in items:
        product = products.get(product_id)
        if product is None:
            raise Product.DoesNotExist
        product_groups = groups[product_id]
        if location is None and any(group.location_ids for group in product_groups):
            raise ValueError(f"Choose an order location for {product.name}.")
        lines.append(price_order_line(
            product, quantity, selected, product_groups,
            price_product(product=product, business=business, location=location, at=now),
        ))
    return lines


class VoucherError(ValueError):
    pass


def normalize_voucher_code(value):
    code = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,31}", code, re.ASCII):
        raise VoucherError("Enter a valid voucher code.")
    return code


@dataclass(frozen=True)
class AppliedVoucher:
    voucher: Voucher
    eligible_subtotal: Decimal
    subtotal: Decimal
    discount: Decimal

    @property
    def total(self):
        return self.subtotal - self.discount

    def snapshot(self):
        return {
            "name": self.voucher.name,
            "code": self.voucher.code,
            "discount_type": self.voucher.discount_type,
            "value": str(self.voucher.value),
            "discount": str(self.discount),
            "eligible_subtotal": str(self.eligible_subtotal),
            "subtotal": str(self.subtotal),
        }

    def fingerprint(self):
        payload = [self.voucher.pk, self.snapshot()]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def calculate_voucher(*, business, code, lines, location, order_type, customer=None, at=None, lock=False):
    code = normalize_voucher_code(code)
    if order_type not in Order.OrderType.values:
        raise VoucherError("Choose dine-in or takeaway.")
    query = business.vouchers.prefetch_related("products", "categories", "locations")
    if lock:
        query = query.select_for_update()
    voucher = query.filter(code=code).first()
    if voucher is None or not voucher.active:
        raise VoucherError("This voucher is unavailable.")
    now = at or timezone.now()
    if voucher.starts_at and now < voucher.starts_at:
        raise VoucherError("This voucher is not valid yet.")
    if voucher.ends_at and now >= voucher.ends_at:
        raise VoucherError("This voucher has expired.")
    location_ids = {item.pk for item in voucher.locations.all()}
    if location_ids and getattr(location, "pk", None) not in location_ids:
        raise VoucherError("This voucher is unavailable at this location.")
    if voucher.order_type and voucher.order_type != order_type:
        raise VoucherError("This voucher is unavailable for this order type.")
    if voucher.redemption_limit and voucher.redemptions.count() >= voucher.redemption_limit:
        raise VoucherError("This voucher has reached its redemption limit.")
    if voucher.customer_limit:
        if customer is None or customer.business_id != business.pk:
            raise VoucherError("This voucher requires a customer record at the counter.")
        if voucher.redemptions.filter(order__customer=customer).count() >= voucher.customer_limit:
            raise VoucherError("This customer has reached the voucher limit.")
    if not voucher.stack_with_promotions and any(line.price.discount_amount > 0 for line in lines):
        raise VoucherError("This voucher cannot be combined with current promotions.")

    products = {item.pk for item in voucher.products.all()}
    categories = {item.pk for item in voucher.categories.all()}
    eligible = sum(
        (line.price.unit_price * line.quantity for line in lines
         if not (products or categories) or line.product.pk in products or line.product.category_id in categories),
        Decimal("0.00"),
    )
    if eligible <= 0:
        raise VoucherError("This voucher does not apply to these items.")
    if eligible < voucher.minimum_subtotal:
        raise VoucherError("The eligible items do not meet the minimum purchase.")
    discount = voucher.value
    if voucher.discount_type == Voucher.DiscountType.PERCENT:
        discount = (eligible * voucher.value / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    discount = min(discount, eligible, voucher.maximum_discount if voucher.maximum_discount is not None else eligible)
    if discount <= 0:
        raise VoucherError("The eligible items do not qualify for a discount.")
    subtotal = sum((line.total for line in lines), Decimal("0.00"))
    return AppliedVoucher(voucher, eligible, subtotal, discount)


def pos_voucher_approval(*, business, location, order_type, customer, lines, applied):
    return {
        "business": str(business.pk),
        "code": applied.voucher.code,
        "location": getattr(location, "pk", None),
        "type": order_type,
        "customer": getattr(customer, "pk", None),
        "lines": [line.approval() for line in lines],
        "voucher": applied.fingerprint(),
    }
