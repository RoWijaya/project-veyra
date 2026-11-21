"""Shared location, product and pricing rules for order options."""

from decimal import Decimal

from django.db.models import Prefetch

from .models import ModifierGroup, ModifierOption


def modifier_catalog(*, business, product_ids, location, include_other_locations=False):
    product_ids = set(product_ids)
    result = {product_id: [] for product_id in product_ids}
    groups = (
        ModifierGroup.objects.filter(business=business, products__pk__in=product_ids, active=True)
        .distinct()
        .prefetch_related(
            "products", "locations",
            Prefetch("options", queryset=ModifierOption.objects.filter(active=True), to_attr="active_options"),
        )
    )
    for group in groups:
        group.location_ids = {branch.pk for branch in group.locations.all()}
        if not include_other_locations and group.location_ids and getattr(location, "pk", None) not in group.location_ids:
            continue
        for product in group.products.all():
            if product.business_id == business.pk and product.pk in result:
                result[product.pk].append(group)
    return result


def normalize_options(values):
    if not isinstance(values, (list, tuple)) or len(values) > 100:
        raise ValueError("Choose valid product options.")
    options = []
    for value in values:
        text = str(value)
        if not text.isascii() or not text.isdecimal() or len(text) > 19:
            raise ValueError("Choose valid product options.")
        options.append(int(text))
    if len(options) != len(set(options)):
        raise ValueError("Choose each product option once.")
    return sorted(options)


def submitted_options(data, field_name):
    return [
        value for key in data
        if key == field_name or (
            key.startswith(f"{field_name}_") and key.removeprefix(f"{field_name}_").isdecimal()
        )
        for value in data.getlist(key) if value
    ]


def price_modifiers(groups, selected_ids):
    remaining = set(selected_ids)
    snapshot = []
    total = Decimal("0.00")
    for group in groups:
        selected = [option for option in group.active_options if option.pk in remaining]
        if not group.min_selections <= len(selected) <= group.max_selections:
            if group.min_selections == group.max_selections:
                limit = str(group.max_selections)
            else:
                limit = f"{group.min_selections} to {group.max_selections}"
            raise ValueError(f"{group.name}: choose {limit} option(s).")
        for option in selected:
            remaining.remove(option.pk)
            snapshot.append({"group": group.name, "name": option.name, "surcharge": str(option.surcharge)})
            total += option.surcharge
    if remaining:
        raise ValueError("A selected option is unavailable for this product or location.")
    return snapshot, total
