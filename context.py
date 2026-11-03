from .models import Wallet


def wallet(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        w = Wallet.for_user(user)
        return {"wallet": w, "wallet_available": w.available()}
    return {}
