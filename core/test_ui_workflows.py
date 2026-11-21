from django.test import TestCase

from .models import Membership, Product, Station, User
from .services import create_business, create_order


class BusinessNavigationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('ui-owner@example.com', 'Long-UI-Test-Password-8842!')
        self.business = create_business(owner=self.owner, name='UI Shop', slug='ui-shop')

    def test_owner_has_persistent_navigation_with_current_page_and_skip_link(self):
        self.client.force_login(self.owner)
        response = self.client.get('/app/ui-shop/catalog/')
        self.assertContains(response, 'data-business-navigation')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, 'href="#main-content"')
        self.assertContains(response, 'href="/app/ui-shop/inventory/"')
        self.assertNotContains(response, 'href="/app/ui-shop/purchasing/"')

    def test_collaborator_navigation_only_exposes_permitted_destinations(self):
        staff = User.objects.create_user('ui-staff@example.com', 'Long-UI-Test-Password-8843!')
        Membership.objects.create(business=self.business, user=staff, permissions=['inventory.view'])
        self.client.force_login(staff)
        response = self.client.get('/app/ui-shop/inventory/')
        self.assertContains(response, 'data-business-navigation')
        self.assertNotContains(response, 'href="/app/ui-shop/catalog/"')
        self.assertNotContains(response, 'href="/app/ui-shop/payments/"')

    def test_catalog_pagination_keeps_real_results_reachable(self):
        Product.objects.bulk_create([
            Product(business=self.business, name=f'Product {index:02}', price='25000')
            for index in range(26)
        ])
        self.client.force_login(self.owner)
        first = self.client.get('/app/ui-shop/catalog/')
        self.assertContains(first, 'Page 1 of 2')
        self.assertContains(first, 'Product 00')
        self.assertNotContains(first, 'Product 25')
        second = self.client.get('/app/ui-shop/catalog/?page=2')
        self.assertContains(second, 'Product 25')

    def test_report_validation_preserves_application_and_filter_feedback(self):
        self.client.force_login(self.owner)
        response = self.client.get('/app/ui-shop/reports/?start=2026-11-20&end=2026-11-01')
        self.assertContains(response, 'data-business-navigation', status_code=400)
        self.assertContains(response, 'Choose a date range', status_code=400)
        self.assertContains(response, 'Reset filters', status_code=400)

    def test_station_viewer_can_read_queue_but_cannot_use_mutation_controls(self):
        product = Product.objects.create(business=self.business, name='Queue coffee', price='25000')
        order = create_order(business=self.business, actor=self.owner, items=[(product.pk, 1)])
        station = Station.objects.create(business=self.business, name='Kitchen', kind='kitchen')
        staff = User.objects.create_user('ui-viewer@example.com', 'Long-UI-Test-Password-8844!')
        Membership.objects.create(business=self.business, user=staff, permissions=['orders.view'])
        self.client.force_login(staff)
        path = f'/app/ui-shop/stations/{station.pk}/screen/'
        response = self.client.get(path)
        self.assertContains(response, 'Queue coffee')
        self.assertContains(response, 'data-station-screen')
        self.assertNotContains(response, 'Start preparation')
        self.assertNotContains(response, 'Reprint ticket')
        self.assertEqual(self.client.post(path, {'action':'advance','order_id':order.pk,'status':'preparing'}).status_code,403)

