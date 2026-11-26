from django.shortcuts import render


def access_restricted(request, message):
    return render(request, "403.html", {"access_message": message}, status=403)
