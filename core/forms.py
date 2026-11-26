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
from .permissions import (
    DEFAULT_STATION_CAPABILITIES, INVITABLE_PERMISSIONS, PERMISSION_CHOICES, STATION_CAPABILITIES,
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

    def validate_unique(self):
        # Registration handles the database constraint with the same public response for duplicates.
        pass


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
    permissions = forms.MultipleChoiceField(
        choices=[choice for choice in PERMISSION_CHOICES if choice[0] in INVITABLE_PERMISSIONS],
        required=False, widget=forms.CheckboxSelectMultiple
    )


class StationForm(forms.ModelForm):
    capabilities = forms.MultipleChoiceField(
        choices=STATION_CAPABILITIES, label="Station capabilities", required=False,
        widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = Station
        fields = ("name", "kind", "handling_mode", "location", "printer")

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = business.locations.filter(active=True)
        self.fields["printer"].queryset = business.printers.filter(is_active=True).order_by("name")
        self.fields["location"].empty_label = "No location assigned"
        self.fields["printer"].empty_label = "No printer assigned"
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
    merchant_id = forms.CharField(label="Merchant ID", max_length=120)
    server_key = forms.CharField(
        label="Midtrans server key", required=False, max_length=5000,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    signature_secret_key = forms.CharField(
        label="Midtrans Signature Secret Key", required=False, max_length=5000,
        help_text="From Midtrans Dashboard, Settings, Access Keys. Required for verification.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    client_id = forms.CharField(label="BCA client ID", required=False, max_length=5000)
    client_secret = forms.CharField(
        label="BCA client secret", required=False, max_length=5000,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    channel_id = forms.CharField(
        label="BCA channel ID", required=False, max_length=120,
        help_text="Leave blank to use the default channel.",
    )
    credential_bundle = forms.CharField(required=False, max_length=5000, widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        provider = cleaned.get("provider")
        if not provider or self.errors.get("credential_bundle") or cleaned.get("credential_bundle"):
            return cleaned

        required = ("server_key",) if provider == PaymentConnection.Provider.MIDTRANS else ("client_id", "client_secret")
        for field in required:
            if not cleaned.get(field) and not self.errors.get(field):
                self.add_error(field, "Enter the credential issued by your payment provider.")
        keys = (*required, "signature_secret_key") if provider == PaymentConnection.Provider.MIDTRANS else (*required, "channel_id")
        cleaned["credential_bundle"] = {key: cleaned[key] for key in keys if cleaned.get(key)}
        return cleaned

    def clean_credential_bundle(self):
        bundle = self.cleaned_data["credential_bundle"]
        if not bundle:
            return None
        try:
            credentials = json.loads(bundle)
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


WEEKDAY_CHOICES = (
    ("0", "Mon"),
    ("1", "Tue"),
    ("2", "Wed"),
    ("3", "Thu"),
    ("4", "Fri"),
    ("5", "Sat"),
    ("6", "Sun"),
)


class PromotionForm(forms.ModelForm):
    permanent_offer = forms.BooleanField(
        required=False,
        initial=True,
        label="Permanent offer",
        help_text="Keep this promotion available indefinitely without a start or expiration date.",
    )
    days_of_week = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Promotion
        fields = (
            "name", "section", "active", "permanent_offer", "start_date", "end_date",
            "start_time", "end_time", "days_of_week", "discount_type", "value", "priority",
            "stackable", "products", "categories", "locations",
        )
        labels = {
            "start_date": "Starts on",
            "end_date": "Ends on",
            "start_time": "Starts at",
            "end_time": "Ends at",
        }
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
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "start_time": forms.TimeInput(format="%H:%M:%S", attrs={"type": "time", "step": 1}),
            "end_time": forms.TimeInput(format="%H:%M:%S", attrs={"type": "time", "step": 1}),
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
            self.initial["permanent_offer"] = getattr(self.instance, "permanent_offer", True)
            self.initial["days_of_week"] = [str(day) for day in (self.instance.days_of_week or [])]
        elif not self.is_bound:
            self.initial["permanent_offer"] = True
            self.initial["days_of_week"] = []

    def clean(self):
        cleaned_data = super().clean()
        permanent = cleaned_data.get("permanent_offer", False)
        if permanent:
            # Date inputs are disabled in this mode. Restore the stored values for later edits.
            cleaned_data["start_date"] = self.instance.start_date
            cleaned_data["end_date"] = self.instance.end_date
        else:
            start_date = cleaned_data.get("start_date")
            end_date = cleaned_data.get("end_date")
            if start_date and end_date and end_date < start_date:
                self.add_error("end_date", "Ends on must be on or after Starts on.")
        return cleaned_data

    def clean_days_of_week(self):
        days = self.cleaned_data.get("days_of_week") or []
        return sorted([int(day) for day in days])

    def save(self, commit=True):
        promotion = super().save(commit=False)
        promotion.business = self.business
        promotion.days_of_week = self.cleaned_data.get("days_of_week") or []
        promotion.permanent_offer = self.cleaned_data.get("permanent_offer", False)
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
