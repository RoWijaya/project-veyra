"""Synthetic seed and restore assertions for the disposable staging recovery rehearsal."""

import hashlib
import os
from io import BytesIO
from secrets import token_urlsafe
from datetime import timedelta

import pyotp
from PIL import Image
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.account_mail import queue_verification, redirect_cipher
from core.identity import account_cipher
from core.media_services import normalize_image
from core.models import Authenticator, Business, PasswordRecoveryDelivery, PaymentTransaction, Product, ProductImage, User
from core.services import create_business, create_order, read_payment_credentials, save_payment_connection

mode = os.environ["VEYRA_RECOVERY_MODE"]
expected_database = {"seed": "veyra_staging", "verify": "veyra_staging_restored"}[mode]
assert connection.settings_dict["NAME"] == expected_database
assert not settings.DEBUG and settings.ALLOWED_HOSTS == ["localhost"]

if mode == "seed":
    with transaction.atomic():
        assert not Business.objects.exists(), "Rehearse in a new, isolated staging project."
        owner = User.objects.create_user("backup-owner@example.invalid")
        outsider = User.objects.create_user("backup-outsider@example.invalid")
        business = create_business(owner=owner, name="Restore fixture", slug="restore-fixture")
        create_business(owner=outsider, name="Separate restore tenant", slug="restore-outsider")
        for user in (owner, outsider):
            Authenticator.objects.create(
                user=user, confirmed=True,
                secret_ciphertext=account_cipher().encrypt(pyotp.random_base32().encode()).decode(),
            )
        connection_row = save_payment_connection(
            business=business, actor=owner, provider="midtrans", environment="sandbox",
            merchant_id="synthetic-recovery", credentials={
                "server_key": "synthetic_" + token_urlsafe(20),
                "signature_secret_key": "synthetic_" + token_urlsafe(20),
            },
        )
        product = Product.objects.create(business=business, name="Recovery tea", price=3000)
        photo = BytesIO()
        Image.new("RGB", (64, 64), (173, 125, 83)).save(photo, format="PNG")
        photo.seek(0)
        ProductImage.objects.create(
            product=product, content=normalize_image(SimpleUploadedFile("fixture.png", photo.getvalue(), "image/png")),
        )
        sale = create_order(
            business=business, actor=owner, location_id=business.locations.get().pk, items=[(product.pk, 2)],
        )
        attempt = PaymentTransaction.objects.create(
            business=business, order=sale, connection=connection_row, provider="midtrans", environment="sandbox",
            merchant_reference="disposable-recovery-reference", amount=sale.total,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        save_payment_connection(
            business=business, actor=owner, provider="midtrans", environment="sandbox",
            merchant_id="synthetic-recovery", credentials={
                "server_key": "synthetic_" + token_urlsafe(20),
                "signature_secret_key": "synthetic_" + token_urlsafe(20),
            },
        )
        attempt.refresh_from_db()
        assert attempt.signing_version_id
        pending = User.objects.create_user("restore-pending@example.invalid", is_active=False)
        queue_verification(user_id=pending.pk, origin="https://localhost", next_url="/app/")
        PasswordRecoveryDelivery.objects.create(
            origin="https://localhost", recipient_ciphertext=redirect_cipher().encrypt(owner.email.encode()).decode(),
        )

business = Business.objects.get(slug="restore-fixture")
owner = business.owner
partner = business.payment_connections.get()
keys = read_payment_credentials(connection=partner, actor=owner)
assert all(value.startswith("synthetic_") for value in keys.values())
product = business.products.get()
receipt_material = hashlib.sha256(product.image.content).hexdigest()
order = business.orders.get()
assert order.total == 6000 and order.lines.get().product_id == product.pk

if mode == "verify":
    outsider = User.objects.get(email="backup-outsider@example.invalid")
    owner_browser = Client(HTTP_HOST="localhost")
    owner_browser.force_login(owner)
    session = owner_browser.session
    session["mfa_version"] = str(owner.authenticator.version)
    session.save()
    outsider_browser = Client(HTTP_HOST="localhost")
    outsider_browser.force_login(outsider)
    outsider_session = outsider_browser.session
    outsider_session["mfa_version"] = str(outsider.authenticator.version)
    outsider_session.save()
    image_route = reverse("product_image", args=[business.slug, product.pk])
    restored_image = owner_browser.get(image_route, secure=True)
    assert restored_image.status_code == 200
    assert hashlib.sha256(restored_image.content).hexdigest() == receipt_material
    assert outsider_browser.get(image_route, secure=True).status_code == 404
    assert outsider_browser.get(reverse("payment_settings", args=[business.slug]), secure=True).status_code == 404
    call_command("check_recovery_keys")
    call_command("check")

print(f"Recovery fixture {mode} passed, order total and private media SHA256 verified: {receipt_material}")
