from django.core.management.base import BaseCommand
from django.utils import timezone
from vendas.models import Order

class Command(BaseCommand):
    help = "Cancela pedidos pendentes cujo prazo (expires_at) já venceu"

    def handle(self, *args, **options):
        now = timezone.now()
        qs = Order.objects.filter(status="pending", expires_at__lt=now)
        count = qs.update(status="cancelled")
        self.stdout.write(self.style.SUCCESS(f"Pedidos cancelados: {count}"))
