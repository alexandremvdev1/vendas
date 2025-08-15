# vendas/context_processors.py
from .models import Company

def company(request):
    # entrega a empresa ativa (ou None) para todos os templates
    c = Company.objects.filter(active=True).order_by("-created_at").first()
    return {"company": c}
