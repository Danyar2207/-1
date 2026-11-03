from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Asset, Lot, Wallet


class AuctionTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("seller", password="x")
        self.a = User.objects.create_user("alice", password="x")
        self.b = User.objects.create_user("bob", password="x")
        self.lot = Lot.objects.create(
            title="Книга", owner=self.seller, start_price=Decimal("10"),
            min_step=Decimal("5"), ends_at=timezone.now() + timedelta(hours=1))

    def test_first_bid_must_reach_start_price(self):
        with self.assertRaises(ValidationError):
            self.lot.place_bid(self.a, Decimal("9"))
        self.lot.place_bid(self.a, Decimal("10"))

    def test_step_is_enforced(self):
        self.lot.place_bid(self.a, Decimal("10"))
        with self.assertRaises(ValidationError):
            self.lot.place_bid(self.b, Decimal("12"))
        self.lot.place_bid(self.b, Decimal("15"))
        self.assertEqual(self.lot.current_price, Decimal("15"))

    def test_owner_cannot_bid(self):
        with self.assertRaises(ValidationError):
            self.lot.place_bid(self.seller, Decimal("100"))

    def test_winner_determined_after_end(self):
        self.lot.place_bid(self.a, Decimal("10"))
        self.lot.place_bid(self.b, Decimal("20"))
        Lot.objects.filter(pk=self.lot.pk).update(ends_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(Lot.close_expired(), 1)
        self.lot.refresh_from_db()
        self.assertEqual((self.lot.winner, self.lot.final_price, self.lot.status),
                         (self.b, Decimal("20"), Lot.Status.CLOSED))

    def test_no_bids_means_no_winner(self):
        Lot.objects.filter(pk=self.lot.pk).update(ends_at=timezone.now() - timedelta(seconds=1))
        Lot.close_expired()
        self.lot.refresh_from_db()
        self.assertIsNone(self.lot.winner)

    def test_cannot_bid_after_end(self):
        Lot.objects.filter(pk=self.lot.pk).update(ends_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            self.lot.place_bid(self.a, Decimal("50"))

    def test_bid_requires_login(self):
        r = self.client.post(reverse("lot_detail", args=[self.lot.pk]), {"amount": "10"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.lot.bids.count(), 0)


class WalletAndMarketTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user("seller", password="x")
        self.a = User.objects.create_user("alice", password="x")
        self.lot = Lot.objects.create(
            title="Книга", owner=self.seller, start_price=Decimal("100"),
            min_step=Decimal("10"), ends_at=timezone.now() + timedelta(hours=1))
        self.asset = Asset.objects.create(symbol="TST", name="Test", price=Decimal("100"),
                                          volatility=Decimal("0"))

    def test_start_balance(self):
        from .models import START_BALANCE
        self.assertEqual(Wallet.for_user(self.a).balance, START_BALANCE)

    def test_cannot_bid_more_than_balance(self):
        with self.assertRaises(ValidationError):
            self.lot.place_bid(self.a, Decimal("999999"))

    def test_winner_pays_seller_receives(self):
        self.lot.place_bid(self.a, Decimal("150"))
        Lot.objects.filter(pk=self.lot.pk).update(ends_at=timezone.now() - timedelta(seconds=1))
        Lot.close_expired()
        self.assertEqual(Wallet.for_user(self.a).balance, Decimal("9850.00"))
        self.assertEqual(Wallet.for_user(self.seller).balance, Decimal("10150.00"))

    def test_leading_bid_is_reserved(self):
        self.lot.place_bid(self.a, Decimal("150"))
        self.assertEqual(Wallet.for_user(self.a).available(), Decimal("9850.00"))

    def test_buy_and_sell(self):
        w = Wallet.for_user(self.a)
        w.buy(self.asset, Decimal("2"))
        self.assertEqual(w.balance, Decimal("9800.00"))
        w.sell(self.asset, Decimal("1"))
        self.assertEqual(w.balance, Decimal("9900.00"))
        with self.assertRaises(ValidationError):
            w.sell(self.asset, Decimal("5"))
        with self.assertRaises(ValidationError):
            w.buy(self.asset, Decimal("1000"))
