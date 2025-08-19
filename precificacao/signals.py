# precificacao/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Produto, ComponenteProduto
from .services.pricing import preco_sugerido

@receiver(post_save, sender=Produto)
def _refresh_prod_cache_on_save(sender, instance: Produto, **kwargs):
    try:
        bd = preco_sugerido(instance, empresa=instance.empresa)
        Produto.objects.filter(pk=instance.pk).update(
            custo_estimado_cache=bd.custo_total,
            preco_sugerido_cache=bd.preco_final
        )
    except Exception:
        pass

@receiver([post_save, post_delete], sender=ComponenteProduto)
def _refresh_prod_cache_on_components(sender, instance: ComponenteProduto, **kwargs):
    prod = instance.produto
    try:
        bd = preco_sugerido(prod, empresa=prod.empresa)
        Produto.objects.filter(pk=prod.pk).update(
            custo_estimado_cache=bd.custo_total,
            preco_sugerido_cache=bd.preco_final
        )
    except Exception:
        pass
