"""Start an isolated ASGI builder fixture and an outbox worker for Playwright."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen
from uuid import uuid4

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.environ["DJANGO_SQLITE_TEST"] = "0"
os.environ["SUPABASE_MEDIA_URL"] = ""
os.environ["SUPABASE_MEDIA_BUCKET"] = ""
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
os.environ["RESEND_API_KEY"] = ""
os.environ["VEYRA_BILLING_MIDTRANS_SERVER_KEY"] = ""
os.environ["VEYRA_BILLING_MIDTRANS_SIGNATURE_SECRET_KEY"] = ""

import django

django.setup()

from django.core.signing import dumps
from django.db import connection
from django.test.runner import DiscoverRunner
from django.urls import reverse
from django.utils import timezone

from core.media_services import normalize_image
from core.models import Category, DiningTable, Location, OrderingPage, Product, ProductImage, User
from core.services import create_business
from core.storefront import pickup_token
from core.test_catalog_restore import photo

directory = Path(sys.argv[1])
directory.mkdir(parents=True, exist_ok=True)
database = connection.settings_dict.copy()
assert database["HOST"] in ("127.0.0.1", "localhost")
assert database["NAME"].startswith("veyra_builder_") and database["NAME"].endswith("_browser")
assert urlsplit(os.environ["REDIS_URL"]).hostname in ("127.0.0.1", "localhost")
fixture_file = directory / "audit-browser-fixture.json"
finish = directory / "audit-finish"
assert not fixture_file.exists() and not finish.exists(), "Use a fresh fixture directory."

runner = DiscoverRunner(verbosity=0, interactive=False)
databases = runner.setup_databases()
children = []
try:
    assert connection.settings_dict["NAME"] == "test_" + database["NAME"]

    def seed_storefront(slug, business_name):
        email = f"{slug}@example.invalid"
        password = secrets.token_urlsafe(30)
        owner = User.objects.create_user(email=email, password=password, email_verified_at=timezone.now())
        business = create_business(owner=owner, name=business_name, slug=slug)
        location = business.locations.get()
        location.takeaway_version = uuid4()
        location.save(update_fields=["takeaway_version"])
        Location.objects.create(business=business, name="Riverside")
        table = DiningTable.objects.create(business=business, location=location, name="Table 1")
        token = dumps([str(table.pk), str(table.qr_version)], salt="veyra-table-qr")
        for category_name, name in (
            ("Single origin coffees", "Filter coffee"),
            ("Baked pastries", "Butter croissant"),
            ("Chilled seasonal drinks", "Iced pandan"),
            ("Breads and breakfast", "Toast"),
        ):
            category = Category.objects.create(business=business, name=category_name)
            Product.objects.create(business=business, category=category, name=name,
                                   price=25000, description=f"Fresh {name.lower()}")
        ProductImage.objects.create(product=business.products.get(name="Filter coffee"),
                                    content=normalize_image(photo()), alt_text="Coffee photo")
        return {
            "email": email, "password": password, "root": f"/app/{business.slug}/",
            "menu": reverse("table_order", args=[token]),
            "pickup": reverse("pickup_menu", args=[pickup_token(location)]),
        }

    fixtures = {}
    storefronts = {}
    for engine in ("chrome", "firefox", "webkit"):
        main_slug, neighbor_slug = f"builder-{engine}", f"builder-neighbor-{engine}"
        fixtures[engine] = seed_storefront(main_slug, "Builder fixture")
        fixtures[engine]["neighbor"] = seed_storefront(neighbor_slug, "Neighbor fixture")
        storefronts[engine] = (main_slug, neighbor_slug)

    parsed = urlsplit(os.environ["DATABASE_URL"])
    environment = {**os.environ, "DATABASE_URL": urlunsplit(parsed._replace(path="/" + connection.settings_dict["NAME"]))}
    for name, command in (
        ("server", ["-m", "uvicorn", "config.asgi:application", "--host", "127.0.0.1", "--port", "8112", "--no-access-log", "--log-level", "warning"]),
        ("outbox", ["manage.py", "process_outbox", "--interval", "0.5"]),
        ("account-mail", ["manage.py", "process_account_mail", "--interval", "0.5"]),
    ):
        with (directory / f"{name}.log").open("w", encoding="utf8") as output:
            children.append(subprocess.Popen([sys.executable, *command], env=environment,
                                             stdout=output, stderr=subprocess.STDOUT))
    for _ in range(60):
        if any(child.poll() is not None for child in children):
            raise RuntimeError("A fixture process exited before startup. Check the local logs.")
        try:
            with urlopen("http://127.0.0.1:8112/healthz/", timeout=1):
                break
        except OSError:
            time.sleep(0.5)
    else:
        raise RuntimeError("The local ASGI fixture did not become ready.")
    fixture_file.write_text(json.dumps(fixtures), encoding="utf8")
    print("Disposable builder fixture is ready.", flush=True)
    while not finish.exists():
        if any(child.poll() is not None for child in children):
            raise RuntimeError("A fixture process exited. Check the local fixture logs.")
        time.sleep(0.5)

    publications = {}
    for engine, slugs in storefronts.items():
        publications[engine] = []
        for slug in slugs:
            page = OrderingPage.objects.filter(business__slug=slug).first()
            if page is None:
                continue
            snapshots = []
            for snapshot in page.snapshots.filter(number__isnull=False).order_by("number"):
                snapshots.append({
                    "number": snapshot.number,
                    "data": snapshot.data,
                    "logo_sha256": (hashlib.sha256(snapshot.logo_content).hexdigest()
                                    if snapshot.logo_content else None),
                    "cover_sha256": (hashlib.sha256(snapshot.banner_content).hexdigest()
                                     if snapshot.banner_content else None),
                })
            publications[engine].append({
                "business": slug, "live": page.is_published, "active_version": page.published_number,
                "snapshots": snapshots,
            })
    (directory / "publications.json").write_text(json.dumps(publications, indent=2), encoding="utf-8")
finally:
    for child in children:
        child.terminate()
        child.wait(timeout=10)
    fixture_file.unlink(missing_ok=True)
    runner.teardown_databases(databases)
    print("Disposable builder fixture closed.", flush=True)
