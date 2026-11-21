"""Signed public ordering scopes for a table or a pickup location."""

from dataclasses import dataclass
from uuid import UUID

from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404

from .models import DiningTable, Location


@dataclass(frozen=True)
class Storefront:
    location: Location
    version: UUID
    table: DiningTable | None = None

    @property
    def business(self):
        return self.location.business


def pickup_token(location):
    return "pickup-" + signing.dumps(
        [location.pk, str(location.takeaway_version)], salt="veyra-pickup-link"
    )


def validate_table_token(token):
    try:
        table_id, version = signing.loads(token, salt="veyra-table-qr")
        return DiningTable.objects.select_related("business", "location__business").get(
            pk=table_id, qr_version=version, active=True, location__active=True,
        )
    except (signing.BadSignature, ValidationError, ValueError, DiningTable.DoesNotExist, TypeError):
        raise Http404 from None


def resolve_storefront(token):
    if not token.startswith("pickup-"):
        table = validate_table_token(token)
        if table.location.business_id != table.business_id:
            raise Http404
        return Storefront(location=table.location, version=table.qr_version, table=table)
    try:
        location_id, version = signing.loads(token.removeprefix("pickup-"), salt="veyra-pickup-link")
        location = Location.objects.select_related("business").get(
            pk=location_id, takeaway_version=version, active=True,
        )
        return Storefront(location=location, version=location.takeaway_version)
    except (signing.BadSignature, ValidationError, ValueError, Location.DoesNotExist, TypeError):
        raise Http404 from None
