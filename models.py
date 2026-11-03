import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

START_BALANCE = Decimal("10000.00")
QTY_STEP = Decimal("0.0001")
MONEY = Decimal("0.01")


# ======================= Кошелёк (виртуальная валюта) ===================
class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="wallet")
    balance = models.DecimalField("Баланс", max_digits=14, decimal_places=2,
                                  default=START_BALANCE)

    def __str__(self):
        return f"{self.user}: {self.balance}"

    @classmethod
    def for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def reserved(self, exclude_lot=None):
        """Сумма лидирующих ставок пользователя на ещё не закрытых лотах."""
        total = Decimal("0")
        lots = Lot.objects.filter(status=Lot.Status.OPEN, bids__bidder=self.user).distinct()
        for lot in lots:
            if exclude_lot is not None and lot.pk == exclude_lot.pk:
                continue
            top = lot.top_bid
            if top and top.bidder_id == self.user_id:
                total += top.amount
        return total

    def available(self, exclude_lot=None):
        return self.balance - self.reserved(exclude_lot)

    # --- торговля ---------------------------------------------------------
    @transaction.atomic
    def buy(self, asset, qty):
        qty = Decimal(qty).quantize(QTY_STEP)
        if qty <= 0:
            raise ValidationError("Количество должно быть больше нуля.")
        cost = (asset.price * qty).quantize(MONEY)
        if cost > self.available():
            raise ValidationError(f"Недостаточно средств: нужно {cost}, доступно {self.available()}.")
        Wallet.objects.filter(pk=self.pk).update(balance=F("balance") - cost)
        holding, _ = Holding.objects.get_or_create(user=self.user, asset=asset)
        holding.quantity += qty
        holding.save()
        Trade.objects.create(user=self.user, asset=asset, side=Trade.Side.BUY,
                             quantity=qty, price=asset.price, total=cost)
        self.refresh_from_db()
        return cost

    @transaction.atomic
    def sell(self, asset, qty):
        qty = Decimal(qty).quantize(QTY_STEP)
        if qty <= 0:
            raise ValidationError("Количество должно быть больше нуля.")
        holding = Holding.objects.filter(user=self.user, asset=asset).first()
        if holding is None or holding.quantity < qty:
            raise ValidationError("У вас нет столько этого актива.")
        proceeds = (asset.price * qty).quantize(MONEY)
        holding.quantity -= qty
        holding.save()
        Wallet.objects.filter(pk=self.pk).update(balance=F("balance") + proceeds)
        Trade.objects.create(user=self.user, asset=asset, side=Trade.Side.SELL,
                             quantity=qty, price=asset.price, total=proceeds)
        self.refresh_from_db()
        return proceeds


# ============================== Аукцион ==================================
class Lot(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Идут торги"
        CLOSED = "closed", "Завершён"

    title = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="lots", verbose_name="Продавец")
    start_price = models.DecimalField("Стартовая цена", max_digits=10, decimal_places=2)
    min_step = models.DecimalField("Минимальный шаг", max_digits=10, decimal_places=2,
                                   default=Decimal("1.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField("Окончание торгов")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    winner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="won_lots")
    final_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def top_bid(self):
        return self.bids.order_by("-amount", "created_at").first()

    @property
    def current_price(self):
        top = self.top_bid
        return top.amount if top else self.start_price

    @property
    def min_next_bid(self):
        top = self.top_bid
        return self.start_price if top is None else top.amount + self.min_step

    @property
    def is_active(self):
        return self.status == self.Status.OPEN and self.ends_at > timezone.now()

    @transaction.atomic
    def place_bid(self, user, amount):
        lot = Lot.objects.select_for_update().get(pk=self.pk)
        lot.close_if_expired()
        if lot.status != self.Status.OPEN:
            raise ValidationError("Торги по лоту завершены.")
        if user.pk == lot.owner_id:
            raise ValidationError("Нельзя делать ставки на собственный лот.")
        if amount < lot.min_next_bid:
            raise ValidationError(f"Минимальная ставка сейчас: {lot.min_next_bid}.")
        wallet = Wallet.for_user(user)
        if amount > wallet.available(exclude_lot=lot):
            raise ValidationError(
                f"Недостаточно монет: доступно {wallet.available(exclude_lot=lot)}.")
        return Bid.objects.create(lot=lot, bidder=user, amount=amount)

    @transaction.atomic
    def close_if_expired(self):
        """Победитель — максимальная ставка. Победитель платит, продавец получает монеты."""
        if self.status != self.Status.OPEN or self.ends_at > timezone.now():
            return False
        top = self.top_bid
        self.status = self.Status.CLOSED
        if top:
            self.winner, self.final_price = top.bidder, top.amount
            Wallet.for_user(top.bidder)
            Wallet.for_user(self.owner)
            Wallet.objects.filter(user=top.bidder).update(balance=F("balance") - top.amount)
            Wallet.objects.filter(user=self.owner).update(balance=F("balance") + top.amount)
        self.save(update_fields=["status", "winner", "final_price"])
        return True

    @classmethod
    def close_expired(cls):
        return sum(lot.close_if_expired() for lot in
                   cls.objects.filter(status=cls.Status.OPEN, ends_at__lte=timezone.now()))


class Bid(models.Model):
    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="bids")
    bidder = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="bids")
    amount = models.DecimalField("Сумма", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-amount", "created_at"]

    def __str__(self):
        return f"{self.bidder} → {self.lot}: {self.amount}"


# =============================== Биржа ===================================
TICK_SECONDS = 10

DEFAULT_ASSETS = [
    ("USD", "Доллар США", "90.00", "0.004"),
    ("EUR", "Евро", "98.00", "0.004"),
    ("GOLD", "Золото (1 г)", "7500.00", "0.008"),
    ("OIL", "Нефть (баррель)", "6800.00", "0.012"),
    ("BTC", "Биткоин", "60000.00", "0.02"),
]


class Asset(models.Model):
    symbol = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    volatility = models.DecimalField("Волатильность", max_digits=6, decimal_places=4,
                                     default=Decimal("0.01"))
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["symbol"]

    def __str__(self):
        return self.symbol

    @classmethod
    def ensure_defaults(cls):
        if not cls.objects.exists():
            start = timezone.now() - timedelta(seconds=TICK_SECONDS * 30)
            for sym, name, price, vol in DEFAULT_ASSETS:
                cls.objects.create(symbol=sym, name=name, price=Decimal(price),
                                   volatility=Decimal(vol), updated_at=start)

    def tick(self):
        """Имитация рынка: цена блуждает случайно, шаг раз в 10 секунд (подтягивается при заходе)."""
        if not self.ticks.exists():
            PriceTick.objects.create(asset=self, price=self.price, created_at=self.updated_at)
        now = timezone.now()
        raw = int((now - self.updated_at).total_seconds() // TICK_SECONDS)
        steps = min(raw, 30)
        if steps <= 0:
            return
        t = self.updated_at if raw <= 30 else now - timedelta(seconds=TICK_SECONDS * steps)
        for _ in range(steps):
            t += timedelta(seconds=TICK_SECONDS)
            change = Decimal(str(random.gauss(0, float(self.volatility))))
            self.price = max(Decimal("0.01"), (self.price * (1 + change)).quantize(MONEY))
            PriceTick.objects.create(asset=self, price=self.price, created_at=t)
        self.updated_at = t
        self.save(update_fields=["price", "updated_at"])


class PriceTick(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="ticks")
    price = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]


class Holding(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="holdings")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="holdings")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))

    class Meta:
        unique_together = ("user", "asset")

    @property
    def value(self):
        return (self.quantity * self.asset.price).quantize(MONEY)


class Trade(models.Model):
    class Side(models.TextChoices):
        BUY = "buy", "Покупка"
        SELL = "sell", "Продажа"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="trades")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="trades")
    side = models.CharField(max_length=4, choices=Side.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    total = models.DecimalField(max_digits=16, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
