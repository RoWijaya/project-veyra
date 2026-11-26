import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def all_weekdays():
    return list(range(7))


def default_plan_entitlements():
    return {
        "features": [
            "catalog", "pos", "qr_ordering", "inventory", "promotions",
            "analytics", "payments", "team", "stations",
        ],
        "limits": {},
    }


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)


class VerificationDelivery(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Awaiting delivery"
        SENDING = "sending", "Delivery in progress"
        SENT = "sent", "Accepted by mail service"
        FAILED = "failed", "Delivery failed"
        CLOSED = "closed", "Account verified"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="verification_delivery")
    origin = models.CharField(max_length=400)
    next_url = models.TextField(blank=True)
    generation = models.UUIDField(default=uuid.uuid4, editable=False)
    claim_token = models.UUIDField(null=True, editable=False)
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    result = models.CharField(max_length=32, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    requested_at = models.DateTimeField()
    next_attempt_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["state", "next_attempt_at"])]


class GoogleIdentity(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="google_identity")
    subject = models.CharField(max_length=255, unique=True)
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)


class Authenticator(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="authenticator")
    secret_ciphertext = models.TextField()
    confirmed = models.BooleanField(default=False)
    last_counter = models.BigIntegerField(default=-1)
    recovery_digests = models.JSONField(default=list)
    version = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)


class Business(models.Model):
    class Type(models.TextChoices):
        CAFE = "cafe", "Café"
        RESTAURANT = "restaurant", "Restaurant"
        BAKERY = "bakery", "Bakery"
        BAR = "bar", "Bar"
        RETAIL = "retail", "Retail"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_businesses"
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    business_type = models.CharField(max_length=40, choices=Type.choices, default=Type.OTHER)
    timezone_name = models.CharField(max_length=64, default="Asia/Jakarta")
    created_at = models.DateTimeField(auto_now_add=True)


class OrderingPage(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="ordering_page")
    display_name = models.CharField(max_length=100, blank=True)
    introduction = models.CharField(max_length=240, blank=True)
    banner_content = models.BinaryField(null=True)
    logo_content = models.BinaryField(null=True)
    banner_alt = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    appearance = models.JSONField(default=dict, blank=True)
    is_published = models.BooleanField(default=True)
    revision = models.PositiveBigIntegerField(default=0)
    published_number = models.PositiveBigIntegerField(null=True, blank=True)


class OrderingPageSnapshot(models.Model):
    """One editable draft (no number) and immutable numbered publications."""

    page = models.ForeignKey(OrderingPage, on_delete=models.CASCADE, related_name="snapshots")
    number = models.PositiveBigIntegerField(null=True, blank=True)
    data = models.JSONField(default=dict)
    logo_content = models.BinaryField(null=True)
    banner_content = models.BinaryField(null=True)
    saved_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("page",), condition=models.Q(number__isnull=True),
                                    name="one_ordering_page_draft"),
            models.UniqueConstraint(fields=("page", "number"), name="uniq_ordering_page_publication"),
        ]


class Membership(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    permissions = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    inventory_restricted = models.BooleanField(default=False)
    inventory_locations = models.ManyToManyField("Location", blank=True, related_name="inventory_members")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "user"], name="uniq_business_member"),
        ]

    def allows(self, permission):
        return self.business.owner_id == self.user_id or permission in self.permissions

    @property
    def location_restricted(self):
        return self.inventory_restricted and self.business.owner_id != self.user_id

    def allowed_locations(self):
        locations = self.business.locations.all()
        if self.location_restricted:
            return locations.filter(inventory_members=self)
        return locations

    def scope_locations(self, queryset, field="location"):
        if self.location_restricted:
            return queryset.filter(**{f"{field}__in": self.allowed_locations()})
        return queryset.all()

    def allowed_inventory_locations(self):
        return self.allowed_locations()


class SubscriptionPlan(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=360, blank=True)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="IDR")
    trial_days = models.PositiveSmallIntegerField(default=0)
    entitlements = models.JSONField(default=default_plan_entitlements)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class BusinessSubscription(models.Model):
    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        SUSPENDED = "suspended", "Restricted"
        CANCELED = "canceled", "Canceled"
        EXPIRED = "expired", "Expired"
        GRANDFATHERED = "grandfathered", "Grandfathered access"

    business = models.OneToOneField(
        Business, on_delete=models.PROTECT, related_name="subscription"
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status.choices)
    trial_started_at = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    pending_plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="scheduled_changes", null=True, blank=True,
    )
    terms = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class BillingInvoice(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Awaiting payment"
        PAID = "paid", "Paid"
        VOID = "void", "Closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(BusinessSubscription, on_delete=models.PROTECT, related_name="invoices")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    terms = models.JSONField()
    total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    issued_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ("-issued_at", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["subscription", "period_start"], name="billing_one_period"),
            models.UniqueConstraint(fields=["subscription"], condition=models.Q(status="open"), name="billing_one_open_invoice"),
            models.CheckConstraint(condition=models.Q(total__gt=0, period_end__gt=models.F("period_start")),
                                   name="billing_valid_invoice"),
        ]

    @property
    def reference(self):
        return f"VY-{self.pk.hex[:12].upper()}"


class BillingAttempt(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Payment in progress"
        FAILED = "failed", "Payment unsuccessful"
        PAID = "paid", "Paid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(BillingInvoice, on_delete=models.PROTECT, related_name="attempts")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    checkout_url = models.URLField(max_length=500, blank=True)
    checkout_requested_at = models.DateTimeField(null=True, blank=True)
    provider_transaction = models.CharField(max_length=90, blank=True)
    failure_code = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retry_after = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(fields=["invoice"], condition=models.Q(status="pending"), name="billing_one_pending_attempt"),
            models.UniqueConstraint(fields=["provider_transaction"], condition=~models.Q(provider_transaction=""),
                                    name="billing_unique_provider_transaction"),
        ]
        indexes = [models.Index(fields=["status", "retry_after"])]

    @property
    def provider_reference(self):
        return f"VB-{self.pk.hex}"


class AppendOnlySubscriptionEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Subscription events are append-only")

    def delete(self):
        raise ValueError("Subscription events are append-only")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Subscription events are append-only")


class SubscriptionEvent(models.Model):
    objects = AppendOnlySubscriptionEventQuerySet.as_manager()
    subscription = models.ForeignKey(
        BusinessSubscription, on_delete=models.PROTECT, related_name="events"
    )
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    event_type = models.CharField(max_length=80)
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, blank=True)
    from_plan = models.CharField(max_length=80, blank=True)
    to_plan = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Subscription events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Subscription events are append-only")


class Invitation(models.Model):
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="invitations")
    invited_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sent_invitations")
    email = models.EmailField()
    permissions = models.JSONField(default=list, blank=True)
    token_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["business", "email", "accepted_at"])]


class Location(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    takeaway_version = models.UUIDField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_business_location")
        ]

    def __str__(self):
        return self.name


class DiningTable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="tables")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="tables")
    name = models.CharField(max_length=80)
    area = models.CharField(max_length=80, blank=True)
    qr_version = models.UUIDField(default=uuid.uuid4, editable=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "location", "name"], name="uniq_table_location_name"
            )
        ]

    def clean(self):
        if self.location_id and self.location.business_id != self.business_id:
            raise ValidationError({"location": "Location must belong to this business."})


class GuestCart(models.Model):
    voucher_code = models.CharField(max_length=32, blank=True)
    table = models.ForeignKey(
        DiningTable, null=True, blank=True, on_delete=models.CASCADE, related_name="guest_carts"
    )
    pickup_location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.CASCADE, related_name="pickup_carts"
    )
    qr_version = models.UUIDField()
    session_hash = models.CharField(max_length=40)
    items = models.JSONField(default=dict)
    note = models.CharField(max_length=500, blank=True)
    payment_method = models.CharField(max_length=40, default="counter")
    prefers_takeaway = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    checkout_key = models.UUIDField(default=uuid.uuid4)
    tracking_token = models.UUIDField(default=uuid.uuid4)
    order = models.ForeignKey("Order", null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["table", "qr_version", "session_hash"], name="unique_guest_cart_session"
            ),
            models.UniqueConstraint(
                fields=["pickup_location", "qr_version", "session_hash"], name="unique_pickup_cart_session"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(table__isnull=False, pickup_location__isnull=True)
                    | models.Q(table__isnull=True, pickup_location__isnull=False)
                ),
                name="guest_cart_has_one_scope",
            ),
        ]


class Department(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=80)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_business_department")
        ]


class Workspace(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="workspaces")
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.PROTECT, related_name="workspaces"
    )
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_business_workspace")
        ]

    def clean(self):
        if self.location_id and self.location.business_id != self.business_id:
            raise ValidationError({"location": "Location must belong to this business."})
        if self.department_id and self.department.business_id != self.business_id:
            raise ValidationError({"department": "Department must belong to this business."})


class WorkspaceAssignment(models.Model):
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="workspace_assignments"
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="assignments")
    permissions = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "workspace"], name="uniq_workspace_assignment"
            )
        ]

    def clean(self):
        if (
            self.membership_id
            and self.workspace_id
            and self.membership.business_id != self.workspace.business_id
        ):
            raise ValidationError("Membership and workspace must belong to the same business.")


class Printer(models.Model):
    class PrinterType(models.TextChoices):
        NETWORK_ESCPOS = "network_escpos", "Network ESC/POS"
        USB_ESCPOS = "usb_escpos", "USB ESC/POS"
        VIRTUAL = "virtual", "Virtual / Browser"

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        UNKNOWN = "unknown", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="printers")
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.SET_NULL, related_name="printers"
    )
    name = models.CharField(max_length=100)
    printer_type = models.CharField(
        max_length=32, choices=PrinterType.choices, default=PrinterType.NETWORK_ESCPOS
    )
    connection_params = models.JSONField(default=dict, blank=True)
    paper_width = models.CharField(max_length=10, default="80mm")
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_business_printer")
        ]

    def clean(self):
        if self.location_id and self.location.business_id != self.business_id:
            raise ValidationError({"location": "Location must belong to this business."})


class Station(models.Model):
    class Kind(models.TextChoices):
        REGISTER = "register", "Register"
        KITCHEN = "kitchen", "Kitchen"
        BAR = "bar", "Bar"
        CUSTOM = "custom", "Custom"

    class HandlingMode(models.TextChoices):
        BOTH = "both", "Digital KDS & Printed Tickets"
        KDS = "kds", "Digital KDS Only"
        PRINTED = "printed", "Printed Tickets Only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="stations")
    location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.PROTECT, related_name="stations"
    )
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CUSTOM)
    handling_mode = models.CharField(
        max_length=20, choices=HandlingMode.choices, default=HandlingMode.BOTH
    )
    printer = models.ForeignKey(
        Printer, null=True, blank=True, on_delete=models.SET_NULL, related_name="stations"
    )
    capabilities = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_business_station")
        ]

    def clean(self):
        if self.location_id and self.location.business_id != self.business_id:
            raise ValidationError({"location": "Location must belong to this business."})
        if self.printer_id and self.printer.business_id != self.business_id:
            raise ValidationError({"printer": "Printer must belong to this business."})


class StationPairingCode(models.Model):
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="pairing_codes")
    issued_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    code_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PairedDevice(models.Model):
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="devices")
    label = models.CharField(max_length=120)
    token_digest = models.CharField(max_length=64, unique=True)
    paired_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["station", "revoked_at"])]


class PaymentConnection(models.Model):
    class Provider(models.TextChoices):
        MIDTRANS = "midtrans", "Midtrans"
        BCA_QRIS = "bca_qris", "BCA QRIS (SNAP)"

    class Environment(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox"
        PRODUCTION = "production", "Production"

    class Verification(models.TextChoices):
        PENDING = "pending", "Awaiting verification"
        INCOMPLETE = "incomplete", "Configuration incomplete"
        FAILED = "failed", "Verification failed"
        AUTHENTICATED = "authenticated", "Provider authenticated"
        EVIDENCE = "evidence", "QRIS evidence checked · approval required"
        VERIFIED = "verified", "QRIS enabled"
        EXTERNAL = "external", "Provider approval required"
        DISCONNECTED = "disconnected", "New payments disabled"

    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="payment_connections"
    )
    provider = models.CharField(max_length=24, choices=Provider.choices)
    environment = models.CharField(max_length=16, choices=Environment.choices)
    merchant_id = models.CharField(max_length=120)
    credentials_ciphertext = models.TextField()
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_state = models.CharField(max_length=16, choices=Verification.choices, default=Verification.PENDING)
    last_verification_at = models.DateTimeField(null=True, blank=True)
    last_verification_result = models.CharField(max_length=240, blank=True)
    approval_reference = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "provider"], name="uniq_business_payment_provider"
            )
        ]


class Category(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=240, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_business_category")
        ]


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products"
    )
    station = models.ForeignKey(
        Station, null=True, blank=True, on_delete=models.SET_NULL, related_name="routed_products"
    )
    name = models.CharField(max_length=160)
    sku = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=240, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="product_price_nonnegative"
            )
        ]
        indexes = [models.Index(fields=["business", "active", "name"])]

    def clean(self):
        if self.category_id and self.category.business_id != self.business_id:
            raise ValidationError({"category": "Category must belong to this business."})
        if self.station_id and self.station.business_id != self.business_id:
            raise ValidationError({"station": "Station must belong to this business."})


class ModifierGroup(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="modifier_groups")
    name = models.CharField(max_length=80)
    products = models.ManyToManyField(Product, blank=True, related_name="modifier_groups")
    locations = models.ManyToManyField(Location, blank=True, related_name="modifier_groups")
    min_selections = models.PositiveSmallIntegerField(default=0)
    max_selections = models.PositiveSmallIntegerField(default=1)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name", "pk")
        constraints = [
            models.UniqueConstraint(fields=("business", "name"), name="unique_modifier_group_name"),
            models.CheckConstraint(
                condition=models.Q(max_selections__range=(1, 30), min_selections__lte=models.F("max_selections")),
                name="valid_modifier_selection_limits",
            ),
        ]


class ModifierOption(models.Model):
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name="options")
    name = models.CharField(max_length=80)
    surcharge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name", "pk")
        constraints = [
            models.UniqueConstraint(fields=("group", "name"), name="unique_modifier_option_name"),
            models.CheckConstraint(condition=models.Q(surcharge__gte=0), name="nonnegative_modifier_surcharge"),
        ]


class ProductImage(models.Model):
    product = models.OneToOneField(
        Product, primary_key=True, on_delete=models.CASCADE, related_name="image"
    )
    content = models.BinaryField()
    alt_text = models.CharField(max_length=200)
    updated_at = models.DateTimeField(auto_now=True)


class Promotion(models.Model):
    class DiscountType(models.TextChoices):
        FIXED_PRICE = "fixed_price", "Promotional price"
        PERCENT = "percent", "Percentage off"
        FIXED_AMOUNT = "fixed_amount", "Amount off"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="promotions")
    name = models.CharField(max_length=120)
    section = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)
    permanent_offer = models.BooleanField(
        default=True,
        help_text="Keep this promotion available indefinitely without a start or expiration date.",
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    days_of_week = models.JSONField(default=all_weekdays)
    discount_type = models.CharField(max_length=16, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    priority = models.PositiveSmallIntegerField(default=0)
    stackable = models.BooleanField(default=False)
    products = models.ManyToManyField(Product, blank=True, related_name="promotions")
    categories = models.ManyToManyField(Category, blank=True, related_name="promotions")
    locations = models.ManyToManyField(Location, blank=True, related_name="promotions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["business", "active", "priority"])]

    def clean(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        errors = {}
        try:
            ZoneInfo(self.business.timezone_name)
        except (ZoneInfoNotFoundError, AttributeError):
            errors["business"] = "The business timezone must be a valid IANA timezone."
        if not self.permanent_offer:
            if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
                errors["ends_at"] = "The end timestamp must be after the start timestamp."
            if self.start_date and self.end_date and self.end_date < self.start_date:
                errors["end_date"] = "The end date must be on or after the start date."
        if bool(self.start_time) != bool(self.end_time):
            errors["end_time"] = "Set both recurring times, or leave both blank."
        if self.start_time and self.end_time and self.start_time == self.end_time:
            errors["end_time"] = "A recurring promotion cannot have a zero-length time window."
        if not isinstance(self.days_of_week, list) or any(
            type(day) is not int or day < 0 or day > 6 for day in self.days_of_week
        ):
            errors["days_of_week"] = "Choose weekdays from 0 (Monday) through 6 (Sunday)."
        if self.value is not None:
            if self.value <= 0:
                errors["value"] = "The promotion value must be greater than zero."
            elif self.discount_type == self.DiscountType.PERCENT and self.value > 100:
                errors["value"] = "Percentage discounts cannot exceed 100%."
        if self.pk and self.business_id:
            if self.products.exclude(business_id=self.business_id).exists():
                errors["products"] = "Products must belong to this business."
            if self.categories.exclude(business_id=self.business_id).exists():
                errors["categories"] = "Categories must belong to this business."
            if self.locations.exclude(business_id=self.business_id).exists():
                errors["locations"] = "Locations must belong to this business."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if (self.start_date or self.end_date or self.starts_at or self.ends_at) and not self.pk:
            if self.permanent_offer and "permanent_offer" not in kwargs.get("update_fields", []):
                self.permanent_offer = False
        super().save(*args, **kwargs)


class Voucher(models.Model):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed amount"

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="vouchers")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=32)
    description = models.TextField(blank=True, max_length=500)
    discount_type = models.CharField(max_length=7, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    minimum_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    maximum_discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    redemption_limit = models.PositiveIntegerField(null=True, blank=True)
    customer_limit = models.PositiveIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    stack_with_promotions = models.BooleanField(default=False)
    order_type = models.CharField(max_length=12, choices=(("dine_in", "Dine in"), ("takeaway", "Takeaway")), blank=True)
    locations = models.ManyToManyField(Location, blank=True)
    products = models.ManyToManyField(Product, blank=True)
    categories = models.ManyToManyField(Category, blank=True)

    class Meta:
        ordering = ("name", "pk")
        constraints = [
            models.UniqueConstraint(fields=("business", "code"), name="unique_business_voucher_code"),
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z0-9][A-Z0-9_-]{0,31}$"),
                name="voucher_normalized_code",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(value__gt=0, minimum_subtotal__gte=0)
                    & (models.Q(discount_type="fixed") | models.Q(discount_type="percent", value__lte=100))
                    & (models.Q(maximum_discount__isnull=True) | models.Q(maximum_discount__gt=0))
                ),
                name="voucher_positive_amounts",
            ),
            models.CheckConstraint(
                condition=(models.Q(starts_at__isnull=True) | models.Q(ends_at__isnull=True)
                           | models.Q(starts_at__lt=models.F("ends_at"))),
                name="voucher_valid_window",
            ),
            models.CheckConstraint(
                condition=((models.Q(redemption_limit__isnull=True) | models.Q(redemption_limit__gt=0))
                           & (models.Q(customer_limit__isnull=True) | models.Q(customer_limit__gt=0))),
                name="voucher_positive_limits",
            ),
        ]


class VoucherRedemption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voucher = models.ForeignKey(Voucher, on_delete=models.PROTECT, related_name="redemptions")
    order = models.OneToOneField("Order", on_delete=models.PROTECT, related_name="voucher_redemption")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")


class PromotionBanner(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="banners")
    headline = models.CharField(max_length=100)
    description = models.CharField(max_length=240, blank=True)
    content = models.BinaryField()
    alt_text = models.CharField(max_length=200)
    cta_text = models.CharField(max_length=40)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT)
    promotion = models.ForeignKey(Promotion, null=True, blank=True, on_delete=models.PROTECT)
    locations = models.ManyToManyField(Location, blank=True)
    active = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=0)
    last_visible = models.BooleanField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(starts_at__isnull=True) | models.Q(ends_at__isnull=True)
                    | models.Q(ends_at__gt=models.F("starts_at"))
                ),
                name="banner_valid_interval",
            ),
        ]

    def clean(self):
        for field in ("product", "category", "promotion"):
            target = getattr(self, field)
            if target and target.business_id != self.business_id:
                raise ValidationError({field: "Choose a target from this business."})
        if sum(bool(getattr(self, f"{field}_id")) for field in
               ("product", "category", "promotion")) != 1:
            raise ValidationError("Choose exactly one product, category or promotion.")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "End time must follow start time."})


class PromotionScheduleState(models.Model):
    promotion = models.OneToOneField(
        Promotion, on_delete=models.CASCADE, related_name="schedule_state"
    )
    is_in_window = models.BooleanField()
    checked_at = models.DateTimeField(auto_now=True)


class OrderLinePromotion(models.Model):
    line = models.ForeignKey("OrderLine", on_delete=models.CASCADE, related_name="promotions")
    promotion_name = models.CharField(max_length=120)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)


class Customer(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["business", "name"])]


class Supplier(models.Model):
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="suppliers")
    name = models.CharField(max_length=160)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    notes = models.TextField(max_length=1000, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "pk")
        constraints = [
            models.UniqueConstraint(models.functions.Lower("name"), "business", name="supplier_unique_name"),
        ]

    def __str__(self):
        return self.name


class InventoryItem(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="inventory_items")
    name = models.CharField(max_length=160)
    sku = models.CharField(max_length=80, blank=True)
    unit = models.CharField(max_length=24)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="uniq_inventory_name")
        ]

    def __str__(self):
        return self.name


class InventoryBalance(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="balances")
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="inventory_balances"
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    last_purchase_unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "location"], name="uniq_inventory_location_balance"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0), name="inventory_quantity_nonnegative"
            ),
        ]

    @property
    def current_cost_estimate(self):
        from .purchasing import line_amount

        if self.last_purchase_unit_cost is None:
            return None
        return line_amount(self.quantity, self.last_purchase_unit_cost)

    def clean(self):
        if self.item_id and self.location_id and self.item.business_id != self.location.business_id:
            raise ValidationError("Inventory item and location must belong to the same business.")



class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDERED = "ordered", "Ordered"
        PARTIAL = "partial", "Partially received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="purchase_orders")
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    reference = models.CharField(max_length=20)
    supplier_name = models.CharField(max_length=160)
    location_name = models.CharField(max_length=120)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    total = models.DecimalField(max_digits=24, decimal_places=2, default=0)
    revision = models.PositiveIntegerField(default=0)
    expected_on = models.DateField(null=True, blank=True)
    ordered_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(fields=("business", "reference"), name="purchase_unique_reference"),
        ]
        indexes = [models.Index(fields=("business", "location", "status", "-created_at"))]


class PurchaseOrderLine(models.Model):
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="purchase_lines")
    item_name = models.CharField(max_length=160)
    unit = models.CharField(max_length=24)
    ordered_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    received_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=22, decimal_places=2)

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(fields=("purchase", "item"), name="purchase_unique_line_item"),
            models.CheckConstraint(
                condition=models.Q(ordered_quantity__gt=0, unit_cost__gt=0, received_quantity__gte=0,
                                   received_quantity__lte=models.F("ordered_quantity")),
                name="purchase_line_valid_amounts",
            ),
        ]

    @property
    def outstanding_quantity(self):
        return self.ordered_quantity - self.received_quantity


class PurchaseReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    idempotency_key = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    delivery_reference = models.CharField(max_length=80, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(fields=("purchase", "idempotency_key"), name="purchase_receipt_idempotency"),
        ]

    @property
    def reference(self):
        return f"RC-{self.pk.hex[:12].upper()}"


class PurchaseReceiptLine(models.Model):
    receipt = models.ForeignKey(PurchaseReceipt, on_delete=models.PROTECT, related_name="lines")
    purchase_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    accepted_quantity = models.DecimalField(max_digits=12, decimal_places=3)
    rejected_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    damaged_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=22, decimal_places=2)

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(fields=("receipt", "purchase_line"), name="purchase_receipt_unique_line"),
            models.CheckConstraint(
                condition=models.Q(accepted_quantity__gte=0, rejected_quantity__gte=0, damaged_quantity__gte=0,
                                   unit_cost__gt=0) & (
                    models.Q(accepted_quantity__gt=0) | models.Q(rejected_quantity__gt=0) |
                    models.Q(damaged_quantity__gt=0)
                ), name="purchase_receipt_valid_amounts",
            ),
        ]


class StockCount(models.Model):
    balance = models.ForeignKey(InventoryBalance, on_delete=models.PROTECT, related_name="physical_counts")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    expected = models.DecimalField(max_digits=12, decimal_places=3)
    counted = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=240)
    movement = models.OneToOneField("StockMovement", null=True, blank=True, on_delete=models.PROTECT)
    idempotency_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(fields=("balance", "idempotency_key"), name="stock_count_idempotency"),
            models.CheckConstraint(
                condition=models.Q(expected__gte=0, counted__gte=0) & (
                    models.Q(expected=models.F("counted"), movement__isnull=True)
                    | (~models.Q(expected=models.F("counted")) & models.Q(movement__isnull=False))
                ),
                name="stock_count_matches_movement",
            ),
        ]

    @property
    def variance(self):
        return self.counted - self.expected


class SupplierReturn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purchase_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="supplier_returns")
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=22, decimal_places=2)
    reason = models.CharField(max_length=240)
    movement = models.OneToOneField("StockMovement", on_delete=models.PROTECT)
    idempotency_key = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("purchase_line", "idempotency_key"), name="supplier_return_idempotency",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0, unit_cost__gt=0, subtotal__gte=0),
                name="supplier_return_valid_amounts",
            ),
        ]

    @property
    def reference(self):
        return f"RT-{self.pk.hex[:12].upper()}"


class AppendOnlyStockMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Stock movements are append-only")

    def delete(self):
        raise ValueError("Stock movements are append-only")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Stock movements are append-only")


class StockMovement(models.Model):
    objects = AppendOnlyStockMovementQuerySet.as_manager()

    class Reason(models.TextChoices):
        OPENING = "opening", "Opening stock"
        RECEIVED = "received", "Stock received"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        WASTE = "waste", "Waste"
        TRANSFER = "transfer", "Transfer"
        SALE = "sale", "Recipe consumption"
        RETURN = "return", "Order cancellation return"
        COUNT = "count", "Physical stock count"
        SUPPLIER_RETURN = "supplier_return", "Supplier return"

    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="stock_movements")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    order = models.ForeignKey(
        "Order", null=True, blank=True, on_delete=models.PROTECT, related_name="stock_movements"
    )
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    receiving_line = models.OneToOneField(
        "PurchaseReceiptLine", null=True, blank=True, on_delete=models.PROTECT, related_name="stock_movement",
    )
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=16, choices=Reason.choices)
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    request_digest = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "idempotency_key"], name="uniq_stock_idempotency"
            ),
            models.CheckConstraint(
                condition=~models.Q(quantity_delta=0), name="stock_movement_nonzero"
            ),
            models.UniqueConstraint(
                fields=["order", "item", "reason"],
                condition=models.Q(order__isnull=False, reason__in=["sale", "return"]),
                name="uniq_order_recipe_movement",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Stock movements are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Stock movements are append-only")

    def clean(self):
        if self.item_id and self.item.business_id != self.business_id:
            raise ValidationError({"item": "Item must belong to the movement's business."})
        if self.location_id and self.location.business_id != self.business_id:
            raise ValidationError({"location": "Location must belong to the movement's business."})
        if self.order_id and self.order.business_id != self.business_id:
            raise ValidationError({"order": "Order must belong to the movement's business."})


class OutboxEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="outbox_events")
    event_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    next_attempt_at = models.DateTimeField()
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [models.Index(fields=["published_at", "next_attempt_at"])]


class RecipeLine(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="recipe_lines")
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="recipe_lines")
    quantity = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "item"], name="uniq_recipe_ingredient"),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="recipe_quantity_positive"
            ),
        ]

    def clean(self):
        if self.product_id and self.item_id and self.product.business_id != self.item.business_id:
            raise ValidationError("Recipe item and product must belong to the same business.")


class Order(models.Model):
    voucher_snapshot = models.JSONField(default=dict, blank=True)
    voucher_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    class Source(models.TextChoices):
        POS = "pos", "POS"
        TABLE_QR = "table_qr", "Table QR"
        GUEST_PICKUP = "guest_pickup", "Guest takeaway"
        STATION = "station", "Station"

    GUEST_SOURCES = (Source.TABLE_QR, Source.GUEST_PICKUP)

    class OrderType(models.TextChoices):
        DINE_IN = "dine_in", "Dine in"
        TAKEAWAY = "takeaway", "Takeaway"

    class Status(models.TextChoices):
        NEW = "new", "New"
        PREPARING = "preparing", "Preparing"
        READY = "ready", "Ready"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        COUNTER = "counter", "Pay at counter"
        QRIS = "qris", "QRIS"
        CASH = "cash", "Cash"
        CARD = "card", "Card"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="orders")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT)
    table = models.ForeignKey(
        DiningTable, null=True, blank=True, on_delete=models.PROTECT, related_name="orders"
    )
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_by_device = models.ForeignKey(
        PairedDevice, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL)
    customer_name_snapshot = models.CharField(max_length=160, blank=True)
    customer_email_snapshot = models.EmailField(blank=True)
    customer_phone_snapshot = models.CharField(max_length=40, blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    idempotency_digest = models.CharField(max_length=64, blank=True)
    order_type = models.CharField(
        max_length=16, choices=OrderType.choices, default=OrderType.TAKEAWAY
    )
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.POS)
    payment_method = models.CharField(
        max_length=16, choices=PaymentMethod.choices, default=PaymentMethod.COUNTER
    )
    payment_provider = models.CharField(max_length=24, blank=True)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )
    guest_tracking_digest = models.CharField(max_length=64, null=True, blank=True, unique=True)
    table_label = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def payment_due(self):
        return self.total > 0 and self.payment_status != self.PaymentStatus.PAID

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "idempotency_key"], name="uniq_order_idempotency"
            )
        ]
        indexes = [models.Index(fields=["business", "status", "created_at"])]


class OrderTender(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="tender")
    method = models.CharField(max_length=8, choices=Method.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    cash_received = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reference = models.CharField(max_length=120, blank=True)
    recorded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="order_tender_amount_positive"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        method="cash",
                        cash_received__gte=models.F("amount"),
                        change_due=models.F("cash_received") - models.F("amount"),
                    )
                    | models.Q(method="card", cash_received__isnull=True, change_due=0)
                ),
                name="order_tender_payment_consistent",
            ),
        ]


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=160)
    sku_snapshot = models.CharField(max_length=80, null=True, blank=True)
    category_snapshot_id = models.PositiveBigIntegerField(null=True, blank=True)
    category_snapshot_name = models.CharField(max_length=100, null=True, blank=True)
    modifiers = models.JSONField(default=list, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    base_unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="order_line_quantity_positive"
            )
        ]

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    def clean(self):
        if self.order_id and self.product_id and self.order.business_id != self.product.business_id:
            raise ValidationError({"product": "Product must belong to the order's business."})


class AppendOnlyAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Audit events are append-only")

    def delete(self):
        raise ValueError("Audit events are append-only")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Audit events are append-only")


class AuditEvent(models.Model):
    objects = AppendOnlyAuditQuerySet.as_manager()
    business = models.ForeignKey(
        Business, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Audit events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit events are append-only")


class PrintJob(models.Model):
    class JobType(models.TextChoices):
        PREP_TICKET = "prep_ticket", "Preparation Ticket"
        RECEIPT = "receipt", "Customer Receipt"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PRINTING = "printing", "Printing"
        PRINTED = "printed", "Printed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="print_jobs")
    station = models.ForeignKey(
        Station, null=True, blank=True, on_delete=models.SET_NULL, related_name="print_jobs"
    )
    printer = models.ForeignKey(
        Printer, null=True, blank=True, on_delete=models.SET_NULL, related_name="print_jobs"
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="print_jobs")
    job_type = models.CharField(max_length=24, choices=JobType.choices, default=JobType.PREP_TICKET)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    ticket_payload = models.JSONField(default=dict)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    last_error = models.CharField(max_length=300, blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["business", "status", "created_at"]),
            models.Index(fields=["station", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "idempotency_key"], name="uniq_print_job_idempotency"
            )
        ]


class PaymentTransaction(models.Model):
    class Status(models.TextChoices):
        INITIATING = "initiating", "Confirmation pending"
        REVIEW = "review", "Reconciliation required"
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    COLLECTING_STATUSES = (Status.INITIATING, Status.PENDING)
    TENDER_BLOCKING_STATUSES = (*COLLECTING_STATUSES, Status.REVIEW)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="payment_transactions"
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    connection = models.ForeignKey(
        PaymentConnection, on_delete=models.PROTECT, related_name="transactions"
    )
    provider = models.CharField(max_length=24, choices=PaymentConnection.Provider.choices)
    environment = models.CharField(max_length=16, choices=PaymentConnection.Environment.choices)
    provider_transaction_id = models.CharField(max_length=120, blank=True, db_index=True)
    merchant_reference = models.CharField(max_length=120, blank=True, db_index=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="IDR")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    qr_payload = models.TextField(blank=True)
    qr_image_url = models.URLField(max_length=500, blank=True)
    expires_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def qr_available(self):
        return self.status == self.Status.PENDING and self.expires_at > timezone.now() and bool(
            self.qr_payload or self.qr_image_url
        )

    class Meta:
        indexes = [
            models.Index(fields=["business", "status", "created_at"]),
            models.Index(fields=["order", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "idempotency_key"], name="uniq_payment_tx_idempotency"
            )
        ]


class OrderRefund(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting confirmation"
        SUCCEEDED = "succeeded", "Refunded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="refunds")
    payment = models.ForeignKey(PaymentTransaction, null=True, blank=True, on_delete=models.PROTECT)
    tender = models.ForeignKey(OrderTender, null=True, blank=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    provider_reference = models.CharField(max_length=120, blank=True)
    last_submitted_at = models.DateTimeField(null=True, blank=True)
    next_check_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(fields=("order", "idempotency_key"), name="order_refund_idempotency"),
            models.UniqueConstraint(
                fields=("payment", "provider_reference"), condition=~models.Q(provider_reference=""),
                name="order_refund_provider_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0) & (
                    models.Q(tender__isnull=True, payment__isnull=False)
                    | models.Q(tender__isnull=False, payment__isnull=True)
                ),
                name="order_refund_positive_amount_and_capture",
            ),
        ]

    @property
    def reference(self):
        return f"RF-{self.pk.hex}"


class AppendOnlyWebhookEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Webhook events are append-only")

    def delete(self):
        raise ValueError("Webhook events are append-only")

    def bulk_update(self, objs, fields, **kwargs):
        raise ValueError("Webhook events are append-only")


class PaymentWebhookEvent(models.Model):
    objects = AppendOnlyWebhookEventQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business, null=True, blank=True, on_delete=models.PROTECT, related_name="webhook_events"
    )
    provider = models.CharField(max_length=24)
    event_type = models.CharField(max_length=80, blank=True)
    request_digest = models.CharField(max_length=64, unique=True)
    headers = models.JSONField(default=dict)
    payload = models.JSONField(default=dict)
    is_valid_signature = models.BooleanField(default=False)
    processed = models.BooleanField(default=False)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and PaymentWebhookEvent.objects.filter(pk=self.pk).exists():
            raise ValueError("Webhook events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Webhook events are append-only")
