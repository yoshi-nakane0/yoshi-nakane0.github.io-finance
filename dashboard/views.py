from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect
from django.shortcuts import render

from myproject.auth import is_creator_user


def index(request):
    return render(request, 'dashboard/index.html')


def admin_panel(request):
    auth_error = ""

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "logout":
            logout(request)
            return redirect("dashboard:admin_panel")

        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect("dashboard:index")

        auth_error = "ログイン情報が正しくありません。"

    context = {
        "auth_error": auth_error,
        "is_creator": is_creator_user(request.user),
    }
    return render(request, "dashboard/admin.html", context)
