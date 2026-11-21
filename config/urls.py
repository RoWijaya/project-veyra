from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

from core import views

urlpatterns = [path("admin/login/", views.sign_in), path("admin/", admin.site.urls), path("", include("core.urls"))]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
