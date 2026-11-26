class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        nonce = getattr(request, "storefront_style_nonce", None)
        style_policy = "style-src 'self'" + (f" 'nonce-{nonce}'" if nonce else "")
        preview = getattr(request, "ordering_preview", False)
        ancestors = "'self'" if preview else "'none'"
        preview_policy = (
            "; sandbox allow-scripts allow-same-origin; form-action 'none'" if preview else ""
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; {style_policy}; "
            "img-src 'self' data:; connect-src 'self' ws: wss:; object-src 'none'; "
            f"base-uri 'self'; frame-ancestors {ancestors}{preview_policy}",
        )
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response
