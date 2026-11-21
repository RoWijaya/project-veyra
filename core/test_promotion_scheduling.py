import datetime
from datetime import date, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import PromotionForm
from core.models import DiningTable, Location, Order, Product, Promotion, User
from core.pricing import price_product, promotion_is_in_window
from core.services import create_business


class PromotionSchedulingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("promo-owner@example.com", "Password-Secure-9988!safe")
        self.business = create_business(owner=self.owner, name="Promo Cafe", slug="promo-cafe")
        self.location = self.business.locations.get()
        self.product = Product.objects.create(
            business=self.business, name="Iced Latte", price=Decimal("35000.00")
        )
        self.table = DiningTable.objects.create(
            business=self.business, location=self.location, name="Table 1"
        )
        self.table_token = signing.dumps(
            [str(self.table.pk), str(self.table.qr_version)], salt="veyra-table-qr"
        )
        self.client.force_login(self.owner)

    # ------------------------------------------------------------------
    # 1. PERMANENT OFFER
    # ------------------------------------------------------------------

    def test_permanent_offer_without_dates_is_active_and_persisted(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Everyday Discount",
            discount_type=Promotion.DiscountType.FIXED_AMOUNT,
            value=Decimal("5000.00"),
            permanent_offer=True,
            start_date=None,
            end_date=None,
        )
        promo.products.add(self.product)
        promo.refresh_from_db()
        self.assertTrue(promo.permanent_offer)
        self.assertIsNone(promo.start_date)
        self.assertIsNone(promo.end_date)
        # Verify it is in window regardless of date
        any_date = datetime.datetime(2028, 5, 10, 12, 0, tzinfo=dt_timezone.utc)
        self.assertTrue(promotion_is_in_window(promo, at=any_date))
        quote = price_product(product=self.product, at=any_date)
        self.assertEqual(quote.unit_price, Decimal("30000.00"))

    def test_permanent_offer_clears_and_ignores_stale_dates_in_form(self):
        form_data = {
            "name": "Coffee Promo",
            "active": "on",
            "permanent_offer": "on",
            "start_date": "2026-01-01",  # Stale dates submitted by browser
            "end_date": "2026-01-31",
            "discount_type": Promotion.DiscountType.FIXED_PRICE,
            "value": "25000.00",
            "priority": "0",
            "products": [str(self.product.pk)],
            "days_of_week": [],
        }
        form = PromotionForm(form_data, business=self.business)
        self.assertTrue(form.is_valid(), form.errors)
        promo = form.save()
        promo.refresh_from_db()
        self.assertTrue(promo.permanent_offer)
        self.assertIsNone(promo.start_date)
        self.assertIsNone(promo.end_date)
        self.assertIsNone(promo.starts_at)
        self.assertIsNone(promo.ends_at)

    def test_form_initial_sets_permanent_true_when_no_dates_exist(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="No Date Promo",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            permanent_offer=True,
            start_date=None,
            end_date=None,
        )
        form = PromotionForm(business=self.business, instance=promo)
        self.assertTrue(form.initial.get("permanent_offer"))

    def test_form_initial_sets_permanent_false_when_dates_exist(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Seasonal Promo",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("15.00"),
            permanent_offer=False,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
        )
        form = PromotionForm(business=self.business, instance=promo)
        self.assertFalse(form.initial.get("permanent_offer"))

    def test_toggle_permanent_from_date_restricted_to_permanent(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Flash Promo",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("20.00"),
            permanent_offer=False,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 15),
        )
        # Edit form: toggle permanent_offer ON
        form_data = {
            "name": "Flash Promo",
            "active": "on",
            "permanent_offer": "on",
            "discount_type": Promotion.DiscountType.PERCENT,
            "value": "20.00",
            "priority": "0",
            "products": [str(self.product.pk)],
            "days_of_week": [],
        }
        form = PromotionForm(form_data, business=self.business, instance=promo)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertTrue(saved.permanent_offer)
        self.assertIsNone(saved.start_date)
        self.assertIsNone(saved.end_date)

    def test_toggle_permanent_off_enables_date_restrictions(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Open Promo",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            permanent_offer=True,
        )
        # Edit form: toggle permanent_offer OFF, supply valid start and end dates
        form_data = {
            "name": "Open Promo",
            "active": "on",
            # permanent_offer unchecked (omitted from POST)
            "start_date": "2026-12-01",
            "end_date": "2026-12-31",
            "discount_type": Promotion.DiscountType.PERCENT,
            "value": "10.00",
            "priority": "0",
            "products": [str(self.product.pk)],
            "days_of_week": [],
        }
        form = PromotionForm(form_data, business=self.business, instance=promo)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertFalse(saved.permanent_offer)
        self.assertEqual(saved.start_date, date(2026, 12, 1))
        self.assertEqual(saved.end_date, date(2026, 12, 31))

    # ------------------------------------------------------------------
    # 2. DATE RESTRICTIONS
    # ------------------------------------------------------------------

    def test_valid_date_range_is_eligible_during_range(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="November Special",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            permanent_offer=False,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )
        promo.products.add(self.product)
        # November 15: inside range
        in_range = datetime.datetime(2026, 11, 15, 12, 0, tzinfo=dt_timezone.utc)
        self.assertTrue(promotion_is_in_window(promo, at=in_range))
        quote = price_product(product=self.product, at=in_range)
        self.assertEqual(quote.unit_price, Decimal("20000.00"))

    def test_valid_date_range_is_ineligible_before_start_date(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="November Special",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            permanent_offer=False,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )
        promo.products.add(self.product)
        # October 31: before start
        before_range = datetime.datetime(2026, 10, 31, 12, 0, tzinfo=dt_timezone.utc)
        self.assertFalse(promotion_is_in_window(promo, at=before_range))
        quote = price_product(product=self.product, at=before_range)
        self.assertEqual(quote.unit_price, Decimal("35000.00"))

    def test_valid_date_range_is_ineligible_after_end_date(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="November Special",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            permanent_offer=False,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )
        promo.products.add(self.product)
        # December 1: after end
        after_range = datetime.datetime(2026, 12, 1, 12, 0, tzinfo=dt_timezone.utc)
        self.assertFalse(promotion_is_in_window(promo, at=after_range))
        quote = price_product(product=self.product, at=after_range)
        self.assertEqual(quote.unit_price, Decimal("35000.00"))

    def test_start_date_only_is_eligible_on_or_after_start(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Ongoing From Nov",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("28000.00"),
            permanent_offer=False,
            start_date=date(2026, 11, 1),
            end_date=None,
        )
        promo.products.add(self.product)
        self.assertTrue(promotion_is_in_window(
            promo, at=datetime.datetime(2026, 11, 5, 12, 0, tzinfo=dt_timezone.utc)
        ))
        self.assertTrue(promotion_is_in_window(
            promo, at=datetime.datetime(2027, 5, 1, 12, 0, tzinfo=dt_timezone.utc)
        ))
        self.assertFalse(promotion_is_in_window(
            promo, at=datetime.datetime(2026, 10, 30, 12, 0, tzinfo=dt_timezone.utc)
        ))

    def test_end_date_only_is_eligible_until_end(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Expiring Soon",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("28000.00"),
            permanent_offer=False,
            start_date=None,
            end_date=date(2026, 11, 30),
        )
        promo.products.add(self.product)
        self.assertTrue(promotion_is_in_window(
            promo, at=datetime.datetime(2026, 11, 25, 12, 0, tzinfo=dt_timezone.utc)
        ))
        self.assertFalse(promotion_is_in_window(
            promo, at=datetime.datetime(2026, 12, 1, 12, 0, tzinfo=dt_timezone.utc)
        ))

    def test_invalid_date_range_end_before_start_fails_form_validation(self):
        form_data = {
            "name": "Backwards Promo",
            "active": "on",
            "start_date": "2026-11-30",
            "end_date": "2026-11-01",  # Invalid: end < start
            "discount_type": Promotion.DiscountType.FIXED_PRICE,
            "value": "20000.00",
            "priority": "0",
            "products": [str(self.product.pk)],
        }
        form = PromotionForm(form_data, business=self.business)
        self.assertFalse(form.is_valid())
        self.assertIn("end_date", form.errors)

    def test_date_boundaries_evaluate_in_business_timezone(self):
        # Business in Asia/Jakarta (UTC+7)
        promo = Promotion.objects.create(
            business=self.business,
            name="Jakarta Date Test",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            permanent_offer=False,
            start_date=date(2026, 11, 10),
            end_date=date(2026, 11, 10),
        )
        # 2026-11-09 17:00 UTC == 2026-11-10 00:00 Jakarta (Start of Jakarta day)
        at_start = datetime.datetime(2026, 11, 9, 17, 0, tzinfo=dt_timezone.utc)
        self.assertTrue(promotion_is_in_window(promo, at=at_start))
        # 2026-11-09 16:59 UTC == 2026-11-09 23:59 Jakarta (Day before)
        before_start = datetime.datetime(2026, 11, 9, 16, 59, tzinfo=dt_timezone.utc)
        self.assertFalse(promotion_is_in_window(promo, at=before_start))

    # ------------------------------------------------------------------
    # 3. TIME RESTRICTIONS
    # ------------------------------------------------------------------

    def test_no_time_restrictions_is_eligible_all_day(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="All Day Long",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            start_time=None,
            end_time=None,
        )
        morning = datetime.datetime(2026, 11, 15, 2, 0, tzinfo=dt_timezone.utc)  # 09:00 Jakarta
        evening = datetime.datetime(2026, 11, 15, 13, 0, tzinfo=dt_timezone.utc)  # 20:00 Jakarta
        self.assertTrue(promotion_is_in_window(promo, at=morning))
        self.assertTrue(promotion_is_in_window(promo, at=evening))

    def test_both_times_valid_window_eligible_during_window(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Lunch Rush",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("15.00"),
            start_time=time(11, 30),
            end_time=time(14, 0),
        )
        # 12:30 Jakarta == 05:30 UTC
        during = datetime.datetime(2026, 11, 15, 5, 30, tzinfo=dt_timezone.utc)
        self.assertTrue(promotion_is_in_window(promo, at=during))
        # 15:00 Jakarta == 08:00 UTC
        after = datetime.datetime(2026, 11, 15, 8, 0, tzinfo=dt_timezone.utc)
        self.assertFalse(promotion_is_in_window(promo, at=after))

    def test_overnight_midnight_crossing_window_eligible(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Late Night",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            start_time=time(22, 0),
            end_time=time(3, 0),
        )
        # 23:00 Jakarta == 16:00 UTC (before midnight)
        late = datetime.datetime(2026, 11, 15, 16, 0, tzinfo=dt_timezone.utc)
        self.assertTrue(promotion_is_in_window(promo, at=late))
        # 01:30 Jakarta == 18:30 UTC (after midnight)
        early_am = datetime.datetime(2026, 11, 15, 18, 30, tzinfo=dt_timezone.utc)
        self.assertTrue(promotion_is_in_window(promo, at=early_am))
        # 10:00 Jakarta == 03:00 UTC (daytime)
        daytime = datetime.datetime(2026, 11, 15, 3, 0, tzinfo=dt_timezone.utc)
        self.assertFalse(promotion_is_in_window(promo, at=daytime))

    def test_start_time_without_end_time_fails_validation(self):
        promo = Promotion(
            business=self.business,
            name="Incomplete Time Promo",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            start_time=time(10, 0),
            end_time=None,
        )
        with self.assertRaises(ValidationError) as ctx:
            promo.clean()
        self.assertIn("end_time", ctx.exception.message_dict)

    def test_zero_length_time_window_fails_validation(self):
        promo = Promotion(
            business=self.business,
            name="Zero Time Promo",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            start_time=time(12, 0),
            end_time=time(12, 0),
        )
        with self.assertRaises(ValidationError) as ctx:
            promo.clean()
        self.assertIn("end_time", ctx.exception.message_dict)

    # ------------------------------------------------------------------
    # 4. WEEKDAY RESTRICTIONS
    # ------------------------------------------------------------------

    def test_empty_weekdays_is_unrestricted_eligible_every_day(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Any Day Promo",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("22000.00"),
            days_of_week=[],  # Empty list: unrestricted
        )
        promo.products.add(self.product)
        # Test all 7 days of the week
        base_monday = datetime.datetime(2026, 11, 16, 5, 0, tzinfo=dt_timezone.utc)  # Monday in Jakarta
        for day_offset in range(7):
            check_day = base_monday + timedelta(days=day_offset)
            self.assertTrue(
                promotion_is_in_window(promo, at=check_day),
                f"Failed for weekday {check_day.weekday()}",
            )

    def test_all_seven_weekdays_is_unrestricted_eligible_every_day(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Seven Days Promo",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("22000.00"),
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
        )
        promo.products.add(self.product)
        base_monday = datetime.datetime(2026, 11, 16, 5, 0, tzinfo=dt_timezone.utc)
        for day_offset in range(7):
            check_day = base_monday + timedelta(days=day_offset)
            self.assertTrue(promotion_is_in_window(promo, at=check_day))

    def test_specific_weekdays_without_hours_eligible_only_on_selected_days(self):
        # Promotion valid only on Tuesday (1) and Thursday (3), all day (no hours set)
        promo = Promotion.objects.create(
            business=self.business,
            name="Tue Thu Special",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            days_of_week=[1, 3],
            start_time=None,
            end_time=None,
        )
        promo.products.add(self.product)
        monday = datetime.datetime(2026, 11, 16, 5, 0, tzinfo=dt_timezone.utc)  # Mon
        tuesday = datetime.datetime(2026, 11, 17, 5, 0, tzinfo=dt_timezone.utc)  # Tue
        wednesday = datetime.datetime(2026, 11, 18, 5, 0, tzinfo=dt_timezone.utc)  # Wed
        thursday = datetime.datetime(2026, 11, 19, 5, 0, tzinfo=dt_timezone.utc)  # Thu

        self.assertFalse(promotion_is_in_window(promo, at=monday))
        self.assertTrue(promotion_is_in_window(promo, at=tuesday))
        self.assertFalse(promotion_is_in_window(promo, at=wednesday))
        self.assertTrue(promotion_is_in_window(promo, at=thursday))

    def test_weekday_selection_persists_and_restores_on_edit(self):
        # Create via form with Monday(0) and Friday(4)
        form_data = {
            "name": "Mon Fri Promo",
            "active": "on",
            "permanent_offer": "on",
            "discount_type": Promotion.DiscountType.PERCENT,
            "value": "10.00",
            "priority": "0",
            "products": [str(self.product.pk)],
            "days_of_week": ["0", "4"],
        }
        form = PromotionForm(form_data, business=self.business)
        self.assertTrue(form.is_valid(), form.errors)
        promo = form.save()
        self.assertEqual(promo.days_of_week, [0, 4])

        # Open edit form and check initial days_of_week
        edit_form = PromotionForm(business=self.business, instance=promo)
        self.assertEqual(edit_form.initial["days_of_week"], ["0", "4"])

    def test_clear_all_weekdays_submits_as_empty_and_unrestricted(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Was Restricted",
            discount_type=Promotion.DiscountType.PERCENT,
            value=Decimal("10.00"),
            days_of_week=[0, 1],
        )
        # Clear all submitted (days_of_week absent from POST)
        form_data = {
            "name": "Was Restricted",
            "active": "on",
            "permanent_offer": "on",
            "discount_type": Promotion.DiscountType.PERCENT,
            "value": "10.00",
            "priority": "0",
            "products": [str(self.product.pk)],
        }
        form = PromotionForm(form_data, business=self.business, instance=promo)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.days_of_week, [])

    # ------------------------------------------------------------------
    # 5. VIEW & TEMPLATE RENDERING
    # ------------------------------------------------------------------

    def test_promotions_view_renders_redesigned_schedule_controls(self):
        res = self.client.get(f"/app/{self.business.slug}/promotions/")
        self.assertEqual(res.status_code, 200)
        content = res.content.decode("utf-8")

        # 1. Permanent offer checkbox and copy
        self.assertIn("Permanent offer", content)
        self.assertIn("Keep this promotion available indefinitely without a start or expiration date.", content)
        self.assertIn('name="permanent_offer"', content)

        # 2. Offer period labels and inputs
        self.assertIn("Starts on", content)
        self.assertIn("Ends on", content)
        self.assertIn('name="start_date"', content)
        self.assertIn('name="end_date"', content)

        # 3. Specific hours labels and inputs
        self.assertIn("Specific hours (optional)", content)
        self.assertIn("Starts at", content)
        self.assertIn("Ends at", content)
        self.assertIn('name="start_time"', content)
        self.assertIn('name="end_time"', content)

        # 4. Weekday chips and shortcuts
        self.assertIn("Days of the week", content)
        self.assertIn("Select all", content)
        self.assertIn("Clear", content)
        for short_day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            self.assertIn(short_day, content)

        # 5. Native multiple-select for days_of_week must be GONE
        self.assertNotIn('<select name="days_of_week"', content)
        self.assertNotIn('multiple', content.split("days_of_week")[0][-50:] if "days_of_week" in content else "")

    # ------------------------------------------------------------------
    # 6. POS & GUEST ORDERING INTEGRATION
    # ------------------------------------------------------------------

    def test_pos_pricing_respects_permanent_and_weekday_schedules(self):
        # Create promotion eligible only on Saturday (5)
        promo = Promotion.objects.create(
            business=self.business,
            name="Saturday Only",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("15000.00"),
            permanent_offer=True,
            days_of_week=[5],
        )
        promo.products.add(self.product)

        # Friday: ineligible
        friday = datetime.datetime(2026, 11, 20, 5, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(price_product(product=self.product, at=friday).unit_price, Decimal("35000.00"))

        # Saturday: eligible
        saturday = datetime.datetime(2026, 11, 21, 5, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(price_product(product=self.product, at=saturday).unit_price, Decimal("15000.00"))

    def test_guest_qr_menu_respects_permanent_and_weekday_schedules(self):
        promo = Promotion.objects.create(
            business=self.business,
            name="Permanent Coffee Deal",
            section="Today’s Specials",
            discount_type=Promotion.DiscountType.FIXED_PRICE,
            value=Decimal("20000.00"),
            permanent_offer=True,
            days_of_week=[],
        )
        promo.products.add(self.product)

        menu = self.client.get(f"/order/table/{self.table_token}/")
        self.assertEqual(menu.status_code, 200)
        self.assertContains(menu, "Permanent Coffee Deal")

        menu_json = self.client.get(f"/order/table/{self.table_token}/menu.json")
        self.assertEqual(menu_json.status_code, 200)
        products = menu_json.json()["products"]
        self.assertEqual(products[0]["price"], "20000.00")
