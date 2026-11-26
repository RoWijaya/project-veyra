from django import template
from django.urls import reverse

from core.models import Membership

register = template.Library()


@register.simple_tag(takes_context=True)
def business_navigation(context):
    request = context.get("request")
    business = context.get("business")
    if not request or not business or not request.user.is_authenticated:
        return None
    membership = Membership.objects.filter(
        business=business, user=request.user, active=True
    ).first()
    owner = business.owner_id == request.user.pk
    if not owner and membership is None:
        return None

    def allows(*permissions):
        return owner or any(membership.allows(permission) for permission in permissions)

    business_wide = owner or not membership.location_restricted
    current = request.resolver_match.url_name
    settings_destinations = [
        ("Ordering page", "ordering_page_settings", "Guest menu design, drafts and publication",
         allows("ordering_page.view", "ordering_page.edit")),
        ("Locations", "locations", "Addresses and service locations", allows("locations.view")),
        ("Team access", "team", "Employee invitations and permissions", allows("employees.view", "employees.manage")),
        ("Payment accounts", "payment_settings", "Merchant connections and verification", owner),
        ("Subscription & billing", "subscription_billing", "Plans, renewal and invoice history", owner),
    ]
    settings_links = [
        {"label": label, "url": reverse(route, kwargs={"slug": business.slug}), "description": description}
        for label, route, description, visible in settings_destinations
        if visible
    ]
    settings_links.append({
        "label": "Account security",
        "url": reverse("account_security"),
        "description": "Sign-in methods and authenticator",
    })
    groups = []
    destinations = [
        ("Daily work", [
            ("Overview", "business_dashboard", True, ()),
            ("Orders & POS", "point_of_sale", allows("orders.view", "orders.create"), ("order_history", "order_receipt")),
            ("Tables & QR", "tables", allows("tables.view", "tables.manage"), ()),
            ("Stations & devices", "stations", owner, ("station_screen",)),
        ]),
        ("Business", [
            ("Product catalog", "catalog", allows("catalog.view"), ("categories", "category_edit", "product_edit", "modifier_groups", "modifier_group_edit", "product_image_edit")),
            ("Promotions", "promotions", business_wide and allows("catalog.view"), ("promotion_edit", "vouchers", "voucher_detail", "banners", "banner_edit")),
            ("Inventory", "inventory", allows("inventory.view"), ("suppliers", "supplier_create", "supplier_detail", "purchases", "purchase_create", "purchase_detail", "purchase_edit", "purchase_receiving")),
            ("Recipes", "recipes", allows("inventory.manage"), ()),
            ("Customers", "customers", allows("customers.view"), ()),
            ("Sales reports", "reports", allows("reports.view"), ()),
        ]),
    ]
    for title, entries in destinations:
        links = [
            {"label": label, "url": reverse(route, kwargs={"slug": business.slug}),
             "current": current == route or current in children}
            for label, route, visible, children in entries if visible
        ]
        if links:
            groups.append({"title": title, "links": links})
    context_label = next(
        (link["label"] for group in groups for link in group["links"] if link["current"]), ""
    )
    return {
        "context_label": context_label,
        "business": business,
        "groups": groups,
        "settings_links": settings_links,
        "settings_current": current == "business_settings",
        "settings_open": current == "billing_invoice" or current in {route for _, route, _, _ in settings_destinations},
    }
