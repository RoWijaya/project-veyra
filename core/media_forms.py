from operator import attrgetter

from django import forms

from .media_services import normalize_image
from .models import PromotionBanner


class NormalizedImageField(forms.FileField):
    def __init__(self, *args, preserve_alpha=False, **kwargs):
        self.preserve_alpha = preserve_alpha
        kwargs.setdefault("widget", forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        upload = super().clean(data, initial)
        if not upload:
            return None
        try:
            return normalize_image(upload, preserve_alpha=self.preserve_alpha)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error


class ProductImageForm(forms.Form):
    image = NormalizedImageField(label="Product image")
    alt_text = forms.CharField(label="Image description", max_length=200)


class BannerForm(forms.ModelForm):
    image = NormalizedImageField(
        required=False, label="Banner image", help_text="JPEG, PNG or WebP, up to 5 MB."
    )

    class Meta:
        model = PromotionBanner
        fields = [
            "headline", "description", "image", "alt_text", "cta_text",
            "product", "category", "promotion", "locations",
            "starts_at", "ends_at", "priority", "active",
        ]
        labels = {"alt_text": "Image description", "cta_text": "Button text"}
        widgets = {
            "locations": forms.SelectMultiple(attrs={"size": 4}),
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
