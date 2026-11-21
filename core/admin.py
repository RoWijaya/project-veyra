from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from .billing import restrict_subscription
from .models import BillingAttempt, BillingInvoice, BusinessSubscription, SubscriptionEvent, SubscriptionPlan


class PlanAdminForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = "__all__"
        help_texts = {"trial_days": "Used for new businesses on the starter-trial plan. Zero disables new trials."}

    def clean_entitlements(self):
        data = self.cleaned_data["entitlements"]
        if not isinstance(data, dict):
            raise ValidationError("Enter a JSON object with features and limits.")
        features = data.get("features", [])
        limits = data.get("limits", {})
        if not isinstance(features, list) or any(not isinstance(item, str) or not item for item in features):
            raise ValidationError("Features must be a list of feature names.")
        if not isinstance(limits, dict) or any(type(limit) is not int or limit < 0 for limit in limits.values()):
            raise ValidationError("Limits must be nonnegative whole numbers. Omit a limit for unlimited usage.")
        return data

    def clean(self):
        data = super().clean()
        price = data.get("monthly_price")
        if price is not None and (price < 0 or (price > 0 and (
            data.get("currency") != "IDR" or price != price.to_integral_value()
        ))):
            self.add_error("monthly_price", "Paid plans must use a positive whole IDR amount.")
        return data


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    form = PlanAdminForm
    list_display = ("name", "code", "monthly_price", "currency", "trial_days", "active")
    list_filter = ("active", "currency")
    search_fields = ("name", "code")
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        base = super().get_readonly_fields(request, obj)
        return (*base, "code") if obj else base

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessSubscription)
class BusinessSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("business_name", "plan_name", "status", "trial_ends_at", "current_period_end")
    list_filter = ("status", "plan")
    list_select_related = ("business", "plan")
    search_fields = ("business__name", "business__slug")
    readonly_fields = tuple(field.name for field in BusinessSubscription._meta.fields)
    actions = ("suspend_access", "restore_access")

    @admin.display(description="Business", ordering="business__name")
    def business_name(self, obj):
        return obj.business.name

    @admin.display(description="Plan", ordering="plan__name")
    def plan_name(self, obj):
        return obj.terms.get("name", obj.plan.name)

    @admin.action(permissions=["change"], description="Restrict selected subscriptions")
    def suspend_access(self, request, queryset):
        count = sum(restrict_subscription(obj.business_id, request.user, restrict=True) for obj in queryset)
        self.message_user(request, f"Restricted {count} subscriptions.")

    @admin.action(permissions=["change"], description="Restore selected subscriptions")
    def restore_access(self, request, queryset):
        count = sum(restrict_subscription(obj.business_id, request.user, restrict=False) for obj in queryset)
        self.message_user(request, f"Restored {count} subscriptions.")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyBillingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(ReadOnlyBillingAdmin):
    list_display = ("subscription", "event_type", "from_status", "to_status", "created_at")
    list_filter = ("event_type", "to_status")
    search_fields = ("subscription__business__name", "event_type")
    readonly_fields = tuple(field.name for field in SubscriptionEvent._meta.fields)


@admin.register(BillingInvoice)
class BillingInvoiceAdmin(ReadOnlyBillingAdmin):
    list_display = ("reference", "subscription", "total", "status", "issued_at", "paid_at")
    list_filter = ("status", "currency")
    search_fields = ("subscription__business__name", "id")
    readonly_fields = tuple(field.name for field in BillingInvoice._meta.fields)


@admin.register(BillingAttempt)
class BillingAttemptAdmin(ReadOnlyBillingAdmin):
    list_display = ("provider_reference", "invoice", "status", "failure_code", "created_at")
    list_filter = ("status",)
    search_fields = ("invoice__subscription__business__name", "provider_transaction")
    exclude = ("checkout_url",)
    readonly_fields = tuple(field.name for field in BillingAttempt._meta.fields if field.name != "checkout_url")
