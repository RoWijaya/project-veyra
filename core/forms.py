import json

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.utils.text import slugify

from .media_forms import NormalizedImageField
from .models import (
    Business,
    Category,
    Customer,
    InventoryItem,
    Location,
    PaymentConnection,
    Printer,
    Product,
    Promotion,
    RecipeLine,
    Station,
    StockMovement,
    User,
)
from .permissions import DEFAULT_STATION_CAPABILITIES, STATION_CAPABILITIES

PERMISSION_CHOICES = (
    ("catalog.view", "View catalog"),
    ("catalog.manage", "Manage catalog"),
    ("orders.view", "View orders"),
    ("orders.create", "Create orders"),
    ("orders.manage", "Manage order status"),
    ("inventory.view", "View inventory"),
    ("inventory.manage", "Manage inventory"),
    ("locations.view", "View locations"),
    ("locations.manage", "Manage locations"),
    ("tables.view", "View tables and QR codes"),
    ("tables.manage", "Manage tables and QR codes"),
    ("customers.view", "View customers"),
    ("customers.manage", "Manage customers"),
    ("reports.view", "View reports"),
    ("employees.view", "View team"),
    ("employees.manage", "Manage team"),
)


class VerificationResendForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class RegistrationForm(UserCreationForm):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150)

    class Meta:
        model = User
        fields = ("email", "first_name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.pop("autofocus", None)


class BusinessForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ("name", "business_type")

    def save(self, commit=True):
        business = super().save(commit=False)
        base = slugify(business.name)[:150] or "business"
        business.slug = base
        suffix = 2
        while Business.objects.filter(slug=business.slug).exists():
            business.slug = f"{base}-{suffix}"
            suffix += 1
        if commit:
            business.save()
        return business


class InvitationForm(forms.Form):
    email = forms.EmailField()
    permissions = forms.MultipleChoiceField(choices=PERMISSION_CHOICES, required=False)


class StationForm(forms.ModelForm):
    capabilities = forms.MultipleChoiceField(choices=STATION_CAPABILITIES, required=False)

    class Meta:
        model = Station
        fields = ("name", "kind", "handling_mode", "location", "printer")

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = business.locations.filter(active=True)
        self.fields["printer"].queryset = business.printers.filter(is_active=True)
        if not self.is_bound:
            self.fields["capabilities"].initial = DEFAULT_STATION_CAPABILITIES.get(
                self.initial.get("kind", Station.Kind.REGISTER), []
            )

    def save(self, commit=True):
        station = super().save(commit=False)
        station.capabilities = self.cleaned_data["capabilities"]
        if commit:
            station.save()
        return station


class PrinterForm(forms.ModelForm):
    paper_width = forms.ChoiceField(choices=[("58mm", "58 mm"), ("80mm", "80 mm")])
    class Meta:
        model = Printer
        fields = ("name", "printer_type", "paper_width", "location")

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.business = business
        self.fields["location"].queryset = business.locations.filter(active=True)


class PaymentConnectionForm(forms.Form):
    provider = forms.ChoiceField(choices=PaymentConnection.Provider.choices)
    environment = forms.ChoiceField(choices=PaymentConnection.Environment.choices)
    merchant_id = forms.CharField(max_length=120)
    credential_bundle = forms.CharField(
        max_length=5000,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=(
            'Paste provider-issued credentials as a JSON object, for example {"server_key":"…"}.'
        ),
    )

    def clean_credential_bundle(self):
        try:
            credentials = json.loads(self.cleaned_data["credential_bundle"])
        except json.JSONDecodeError as error:
            raise forms.ValidationError("Enter credentials as a JSON object.") from error
        if (
            not isinstance(credentials, dict)
            or not credentials
            or any(
                not isinstance(key, str) or not key or not isinstance(value, str) or not value
                for key, value in credentials.items()
            )
        ):
            raise forms.ValidationError("Credentials must be non-empty string values in an object.")
        return credentials



class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "description", "sort_order", "active")
        labels = {"active": "Show on guest menu", "sort_order": "Display order"}
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "description": "For your team. This description stays in management.",
            "sort_order": "Lower numbers appear first on the guest menu.",
            "active": "Hiding the category keeps its products orderable under All.",
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.business = business

    def clean_name(self):
        name = self.cleaned_data["name"]
        if self.instance.business.categories.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A category with this name already exists.")
        return name

class ProductForm(forms.ModelForm):
    image = NormalizedImageField(required=False, label="Product image")
    image_alt = forms.CharField(
        required=False, label="Image description", max_length=200,
        help_text="Describe the photo for people who cannot see it. A new image uses the product name if left blank.",
    )
    remove_image = forms.BooleanField(required=False, label="Remove current image on save")

    class Meta:
        model = Product
        fields = ("name", "sku", "description", "price", "category", "station", "active")
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
        labels = {"description": "Guest menu description"}

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.business = business
        self.fields["category"].queryset = business.categories.filter(Q(active=True) | Q(pk=self.instance.category_id))
        self.fields["category"].empty_label = "Uncategorized"
        self.fields["category"].label_from_instance = lambda category: category.name if category.active else f"{category.name} (hidden from menu)"
        self.fields["station"].queryset = business.stations.filter(Q(active=True) | Q(pk=self.instance.station_id))
        image = getattr(self.instance, "image", None)
        if image:
            self.initial.setdefault("image_alt", image.alt_text)
        else:
            self.fields.pop("remove_image")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("remove_image") and cleaned.get("image"):
            self.add_error("image", "Choose either a replacement or removal for this image.")
        return cleaned


class NamedModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class PromotionForm(forms.ModelForm):
    days_of_week = forms.MultipleChoiceField(
        choices=(
            (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
            (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
        ),
        required=False,
    )

    class Meta:
        model = Promotion
        fields = (
            "name", "section", "active", "starts_at", "ends_at", "start_date", "end_date",
            "start_time", "end_time", "days_of_week", "discount_type", "value", "priority",
            "stackable", "products", "categories", "locations",
        )
        field_classes = {
            "products": NamedModelMultipleChoiceField,
            "categories": NamedModelMultipleChoiceField,
            "locations": NamedModelMultipleChoiceField,
        }
        error_messages = {
            "products": {
                "invalid_choice": "Choose available products.",
                "invalid_pk_value": "Choose available products.",
            },
            "categories": {
                "invalid_choice": "Choose available categories.",
                "invalid_pk_value": "Choose available categories.",
            },
        }
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "products": forms.SelectMultiple(attrs={"size": 6}),
            "categories": forms.SelectMultiple(attrs={"size": 4}),
            "locations": forms.SelectMultiple(attrs={"size": 4}),
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.instance.business = business
        selectable_products = Q(active=True)
        if self.instance.pk:
            # Keep existing targets when a product is deactivated after the promotion was saved.
            selectable_products |= Q(pk__in=self.instance.products.values("pk"))
        self.fields["products"].queryset = business.products.filter(selectable_products).order_by("name", "pk")
        self.fields["categories"].queryset = business.categories.order_by("name")
        self.fields["locations"].queryset = business.locations.filter(active=True).order_by("name")
        if not self.is_bound and self.instance.pk:
            self.initial["days_of_week"] = [str(day) for day in self.instance.days_of_week]

    def clean_days_of_week(self):
        return [int(day) for day in self.cleaned_data["days_of_week"]]

    def save(self, commit=True):
        promotion = super().save(commit=False)
        promotion.business = self.business
        promotion.days_of_week = self.cleaned_data["days_of_week"]
        if commit:
            promotion.save()
            self.save_m2m()
        return promotion


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ("name", "address")


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ("name", "email", "phone", "note")


class InventoryItemForm(forms.ModelForm):
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    opening_stock = forms.DecimalField(max_digits=12, decimal_places=3, min_value=0, initial=0)

    class Meta:
        model = InventoryItem
        fields = ("name", "sku", "unit")

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.business = business
        self.fields["location"].queryset = business.locations.filter(active=True)


class StockAdjustmentForm(forms.Form):
    item = forms.ModelChoiceField(queryset=InventoryItem.objects.none())
    location = forms.ModelChoiceField(queryset=Location.objects.none())
    quantity_delta = forms.DecimalField(max_digits=12, decimal_places=3)
    reason = forms.ChoiceField(
        choices=[
            (StockMovement.Reason.RECEIVED, "Stock received"),
            (StockMovement.Reason.ADJUSTMENT, "Manual adjustment"),
            (StockMovement.Reason.WASTE, "Waste"),
        ]
    )
    note = forms.CharField(max_length=240, required=False)

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = business.inventory_items.filter(active=True)
        self.fields["location"].queryset = business.locations.filter(active=True)


class RecipeLineForm(forms.ModelForm):
    class Meta:
        model = RecipeLine
        fields = ("product", "item", "quantity")

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = business.products.order_by("name")
        self.fields["item"].queryset = business.inventory_items.filter(active=True).order_by("name")
