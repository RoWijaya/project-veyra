from operator import attrgetter

from django import forms

from .media_services import normalize_image
from .models import OrderingPage, PromotionBanner


class NormalizedImageField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        upload = super().clean(data, initial)
        if not upload:
            return None
        try:
            return normalize_image(upload)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error


class ProductImageForm(forms.Form):
    image = NormalizedImageField(label="Product image")
    alt_text = forms.CharField(label="Image description", max_length=200)


class OrderingPageForm(forms.ModelForm):
    image = NormalizedImageField(required=False, label="Banner image")
    remove_image = forms.BooleanField(required=False, label="Remove current banner on save")

    class Meta:
        model = OrderingPage
        fields = ("display_name", "introduction", "image", "banner_alt", "remove_image")
        labels = {"display_name": "Name on the guest menu", "banner_alt": "Banner image description"}
        widgets = {"introduction": forms.Textarea(attrs={"rows": 3})}
        help_texts = {
            "display_name": "Leave blank to use the business name.",
            "introduction": "Optional text shown above the menu.",
            "banner_alt": "Describe the banner for people who cannot see it. Leave blank for a decorative image.",
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.business = business
        if not getattr(self.instance, "has_banner", False):
            self.fields.pop("remove_image")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("remove_image") and cleaned.get("image"):
            self.add_error("image", "Choose either a replacement or removal for the banner.")
        return cleaned

    def save(self, commit=True):
        if self.cleaned_data.get("remove_image"):
            self.instance.banner_content = None
            self.instance.banner_alt = ""
        elif self.cleaned_data["image"]:
            self.instance.banner_content = self.cleaned_data["image"]
        elif not getattr(self.instance, "has_banner", False):
            self.instance.banner_alt = ""
        return super().save(commit=commit)


class BannerForm(forms.ModelForm):
    image = NormalizedImageField(required=False, label="Banner image")

    class Meta:
        model = PromotionBanner
        fields = [
            "headline", "description", "image", "alt_text", "cta_text",
            "product", "category", "promotion", "locations",
            "starts_at", "ends_at", "priority", "active",
        ]
        labels = {"alt_text": "Image description", "cta_text": "Button text"}
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                           format="%Y-%m-%dT%H:%M"),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                         format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.business = business
        for field, manager in (
            ("product", "products"), ("category", "categories"),
            ("promotion", "promotions"), ("locations", "locations"),
        ):
            self.fields[field].queryset = getattr(business, manager).order_by("name")
            self.fields[field].label_from_instance = attrgetter("name")

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image is None and not self.instance.pk:
            raise forms.ValidationError("Choose a banner image.")
        return image
