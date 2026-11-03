from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from decimal import Decimal

from .forms import BidForm, LotForm, RegisterForm, TradeForm
from .models import Asset, Bid, Holding, Lot, Trade, Wallet


def lot_list(request):
    Lot.close_expired()
    status = request.GET.get("status", "open")
    lots = Lot.objects.select_related("owner", "winner")
    if status in ("open", "closed"):
        lots = lots.filter(status=status)
    return render(request, "auctions/lot_list.html", {"lots": lots, "status": status})


def lot_detail(request, pk):
    lot = get_object_or_404(Lot.objects.select_related("owner", "winner"), pk=pk)
    lot.close_if_expired()
    form = BidForm(request.POST or None)
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if form.is_valid():
            try:
                lot.place_bid(request.user, form.cleaned_data["amount"])
                messages.success(request, "Ставка принята!")
                return redirect("lot_detail", pk=pk)
            except ValidationError as e:
                form.add_error("amount", e)
    return render(request, "auctions/lot_detail.html",
                  {"lot": lot, "form": form, "bids": lot.bids.select_related("bidder")})


@login_required
def lot_create(request):
    form = LotForm(request.POST or None)
    if form.is_valid():
        lot = form.save(commit=False)
        lot.owner = request.user
        lot.save()
        return redirect("lot_detail", pk=lot.pk)
    return render(request, "auctions/lot_form.html", {"form": form})


@login_required
def my_activity(request):
    Lot.close_expired()
    return render(request, "auctions/my.html", {
        "my_lots": Lot.objects.filter(owner=request.user),
        "my_bids": Bid.objects.filter(bidder=request.user).select_related("lot")
                      .order_by("-created_at"),
        "won": Lot.objects.filter(winner=request.user),
    })


def register(request):
    form = RegisterForm(request.POST or None)
    if form.is_valid():
        login(request, form.save())
        return redirect("lot_list")
    return render(request, "registration/register.html", {"form": form})


# ============================ Биржа ======================================
def _sparkline(values, w=600, h=140, pad=8):
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = []
    for i, v in enumerate(values):
        x = pad + i * (w - 2 * pad) / (len(values) - 1)
        y = h - pad - float((v - lo) / span) * (h - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _prepare(asset):
    asset.tick()
    ticks = list(asset.ticks.all()[:30])[::-1]
    base = ticks[0].price if ticks else asset.price
    asset.change = ((asset.price - base) / base * 100).quantize(Decimal("0.01")) if base else Decimal("0")
    return ticks


def market(request):
    Asset.ensure_defaults()
    held = {}
    if request.user.is_authenticated:
        held = {h.asset_id: h.quantity for h in Holding.objects.filter(user=request.user)}
    assets = list(Asset.objects.all())
    for a in assets:
        ticks = _prepare(a)
        a.spark = _sparkline([t.price for t in ticks], w=160, h=44, pad=3)
        a.held = held.get(a.pk, Decimal("0"))
    return render(request, "auctions/market.html", {"assets": assets})


def asset_detail(request, symbol):
    Asset.ensure_defaults()
    asset = get_object_or_404(Asset, symbol=symbol)
    ticks = _prepare(asset)
    form = TradeForm(request.POST or None)
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect(f"/accounts/login/?next={request.path}")
        if form.is_valid():
            wallet = Wallet.for_user(request.user)
            qty = form.cleaned_data["quantity"]
            try:
                if request.POST.get("side") == "sell":
                    got = wallet.sell(asset, qty)
                    messages.success(request, f"Продано {qty} {asset.symbol} за {got}.")
                else:
                    paid = wallet.buy(asset, qty)
                    messages.success(request, f"Куплено {qty} {asset.symbol} за {paid}.")
                return redirect("asset_detail", symbol=symbol)
            except ValidationError as e:
                form.add_error("quantity", e)
    held = Holding.objects.filter(user=request.user, asset=asset).first() if request.user.is_authenticated else None
    prices = [t.price for t in ticks]
    return render(request, "auctions/asset_detail.html", {
        "asset": asset, "form": form, "held": held.quantity if held else Decimal("0"),
        "chart": _sparkline(prices), "lo": min(prices) if prices else None,
        "hi": max(prices) if prices else None,
    })


@login_required
def portfolio(request):
    Asset.ensure_defaults()
    wallet = Wallet.for_user(request.user)
    holdings = list(Holding.objects.filter(user=request.user, quantity__gt=0).select_related("asset"))
    for h in holdings:
        h.asset.tick()
    assets_value = sum((h.value for h in holdings), Decimal("0"))
    return render(request, "auctions/portfolio.html", {
        "wallet": wallet, "holdings": holdings, "assets_value": assets_value,
        "reserved": wallet.reserved(), "net_worth": wallet.balance + assets_value,
        "trades": Trade.objects.filter(user=request.user).select_related("asset")[:30],
    })
