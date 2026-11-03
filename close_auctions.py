from django.core.management.base import BaseCommand
from auctions.models import Lot


class Command(BaseCommand):
    help = "Завершает просроченные лоты и определяет победителей (запускать по cron)."

    def handle(self, *args, **options):
        self.stdout.write(f"Закрыто лотов: {Lot.close_expired()}")
