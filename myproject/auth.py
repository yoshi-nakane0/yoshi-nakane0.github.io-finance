import os


def is_creator_user(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )


def ensure_env_superuser():
    username = (
        os.getenv("DJANGO_SUPERUSER_USERNAME")
        or os.getenv("ADMIN_USERNAME")
        or ""
    ).strip()
    password = (
        os.getenv("DJANGO_SUPERUSER_PASSWORD")
        or os.getenv("ADMIN_PASSWORD")
        or ""
    )
    if not username or not password:
        return None

    email = (
        os.getenv("DJANGO_SUPERUSER_EMAIL")
        or os.getenv("ADMIN_EMAIL")
        or ""
    ).strip()

    from django.contrib.auth import get_user_model

    User = get_user_model()
    user, _ = User.objects.update_or_create(
        username=username,
        defaults={
            "email": email,
            "is_active": True,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    user.set_password(password)
    user.save(update_fields=[
        "email",
        "is_active",
        "is_staff",
        "is_superuser",
        "password",
    ])
    return user
