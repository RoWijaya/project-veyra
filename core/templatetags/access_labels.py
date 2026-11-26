from django import template

from core.permissions import PERMISSION_CHOICES, STATION_CAPABILITIES

register = template.Library()

MANAGEMENT_LABELS = dict(PERMISSION_CHOICES)
STATION_LABELS = dict(STATION_CAPABILITIES)


@register.filter
def management_permissions(codes):
    return ", ".join(MANAGEMENT_LABELS[code] for code in codes if code in MANAGEMENT_LABELS)


@register.filter
def station_capabilities(codes):
    return ", ".join(STATION_LABELS[code] for code in codes if code in STATION_LABELS)
