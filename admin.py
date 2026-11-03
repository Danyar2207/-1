from django.contrib import admin
from .models import Asset, Bid, Holding, Lot, Trade, Wallet


class BidInline(admin.TabularInline):
    model = Bid
    extra = 0
    readonly_fields = ("bidder", "amount", "created_at")
    can_delete = False


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "status", "ends_at", "winner", "final_price")
    list_filter = ("status",)
    inlines = [BidInline]


admin.site.register(Bid)


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance")


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "price", "volatility")


admin.site.register(Holding)
admin.site.register(Trade)
