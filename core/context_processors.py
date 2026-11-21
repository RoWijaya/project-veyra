from django.conf import settings


def identity_options(request):
    return {
        "google_available": bool(
            settings.GOOGLE_CLIENT_ID
            and settings.GOOGLE_CLIENT_SECRET
            and settings.GOOGLE_REDIRECT_URI
        )
    }
