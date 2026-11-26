"""Validated editor fields for storefront presentation."""

from urllib.parse import urlsplit

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from .media_forms import NormalizedImageField
from .ordering_page import (
    COLORS,
    DEFAULTS,
    IMAGE_FIELDS,
    LAYOUTS,
    LIST_MODELS,
    contrast,
    drop_stale_references,
)


def public_link(label):
    return forms.URLField(
        label=label,
        required=False,
        assume_scheme="https",
        max_length=240,
        validators=[URLValidator(schemes=["https", "http"])],
    )


def color(label):
    return forms.RegexField(
        r"\A#[0-9a-fA-F]{6}\Z",
        label=label,
        max_length=7,
        required=False,
        widget=forms.TextInput(attrs={"type": "color"}),
    )


class OrderingBuilderForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    display_name = forms.CharField(
        label="Display name",
        max_length=100,
        required=False,
        help_text="Leave blank to use the business name.",
    )
    tagline = forms.CharField(max_length=100, required=False)
    introduction = forms.CharField(
        label="Description",
        max_length=240,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    logo = NormalizedImageField(required=False, label="Restaurant logo", preserve_alpha=True)
    remove_logo = forms.BooleanField(required=False, label="Remove current logo")
    image = NormalizedImageField(required=False, label="Banner image")
    remove_image = forms.BooleanField(required=False, label="Remove current banner")
    banner_alt = forms.CharField(
        label="Banner image description",
        max_length=200,
        required=False,
        help_text="Leave blank if the image is decorative.",
    )
    banner_title = forms.CharField(max_length=100, required=False)
    banner_description = forms.CharField(max_length=200, required=False)
    focal_x = forms.IntegerField(
        label="Horizontal focal point (%)", min_value=0, max_value=100, required=False
    )
    focal_y = forms.IntegerField(
        label="Vertical focal point (%)", min_value=0, max_value=100, required=False
    )
    caption = forms.ChoiceField(
        label="Caption style",
        required=False,
        choices=[("solid", "Solid caption"), ("overlay", "Dark image overlay")],
    )
    accent = color("Accent color")
    background = color("Page background")
    text = color("Main text")
    secondary = color("Secondary text")
    button_style = forms.ChoiceField(
        choices=[("rounded", "Rounded"), ("pill", "Pill")], required=False
    )
    card_style = forms.ChoiceField(
        choices=[("divided", "Divided"), ("outlined", "Outlined"), ("raised", "Soft shadow")],
        required=False,
    )
    layout = forms.ChoiceField(
        choices=[(key, label) for key, label, _ in LAYOUTS],
        required=False,
        widget=forms.RadioSelect,
    )
    show_images = forms.BooleanField(label="Show product images", required=False)
    show_descriptions = forms.BooleanField(label="Show product descriptions", required=False)
    show_information = forms.BooleanField(
        label="Show this information on the public menu", required=False
    )
    address = forms.CharField(label="Public address", max_length=240, required=False)
    phone = forms.RegexField(
        r"\A[+0-9 ().-]{7,32}\Z",
        label="Phone number",
        required=False,
        max_length=32,
        widget=forms.TextInput(attrs={"type": "tel"}),
    )
    website = public_link("Website")
    instagram = public_link("Instagram")
    facebook = public_link("Facebook")
    opening_note = forms.CharField(
        label="Opening information",
        max_length=240,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, data=None, files=None, *, business, draft, revision):
        if data is not None and data.get("presentation_fields") == "1":
            data = data.copy()
            for key in ("hidden_products", "hidden_categories", "featured_products"):
                data[key] = ",".join(data.getlist(f"select_{key}"))
        self.business = business
        self.draft = draft
        initial = drop_stale_references(business, {**DEFAULTS, **draft.data})
        super().__init__(data, files, initial={**initial, "revision": revision})
        for key in LIST_MODELS:
            self.fields[key] = forms.CharField(
                required=False, max_length=40000, widget=forms.HiddenInput
            )
            self.initial[key] = ",".join(initial[key])
        for name, attr in (("remove_logo", "logo_content"), ("remove_image", "banner_content")):
            if not getattr(draft, attr):
                self.fields.pop(name)

    def clean_phone(self):
        number = self.cleaned_data["phone"]
        if number and (not 7 <= sum(ch.isdigit() for ch in number) <= 15 or "+" in number[1:]):
            raise ValidationError("Enter a phone number with 7 to 15 digits.")
        return number

    def clean(self):
        data = super().clean()
        color_values = {key: (data.get(key) or value).upper() for key, value in COLORS.items()}
        for key in ("accent", "text", "secondary"):
            if key not in self.errors and "background" not in self.errors:
                if contrast(color_values[key], color_values["background"]) < 4.5:
                    self.add_error(
                        key, "Use a color with at least 4.5:1 contrast against the page background."
                    )
        for field in ("website", "instagram", "facebook"):
            url = data.get(field)
            if url and (urlsplit(url).username is not None or urlsplit(url).password is not None):
                self.add_error(field, "Enter a public web address without credentials.")
        for field, removal in (("logo", "remove_logo"), ("image", "remove_image")):
            if data.get(field) and data.get(removal):
                self.add_error(field, "Choose a replacement image or remove the current one.")

        references = {}
        for key, related in LIST_MODELS.items():
            values = list(dict.fromkeys(filter(None, data.get(key, "").split(","))))
            if values:
                if related not in references:
                    references[related] = set(
                        map(str, getattr(self.business, related).values_list("pk", flat=True))
                    )
                if not set(values) <= references[related]:
                    self.add_error(key, "Choose only products or categories from this business.")
                    continue
            data[key] = values
        return data

    def apply(self):
        cleaned = self.cleaned_data
        draft = self.draft
        draft.data = {key: cleaned.get(key, default) for key, default in DEFAULTS.items()}
        for key in COLORS:
            draft.data[key] = (draft.data[key] or COLORS[key]).upper()
        for key in ("focal_x", "focal_y", "layout", "caption", "button_style", "card_style"):
            if draft.data[key] is None or draft.data[key] == "":
                draft.data[key] = DEFAULTS[key]
        for field, upload, remove in zip(
            IMAGE_FIELDS, ("logo", "image"), ("remove_logo", "remove_image")
        ):
            if cleaned.get(remove):
                setattr(draft, field, None)
            elif cleaned[upload]:
                setattr(draft, field, cleaned[upload])
        if not draft.banner_content:
            draft.data["banner_alt"] = ""
        return draft
