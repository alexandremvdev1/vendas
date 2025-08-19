# --- imports já existentes ---
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum
from decimal import Decimal, ROUND_HALF_UP
from django.contrib.auth.decorators import login_required
from .models import Produto, ParametrosGlobais, TabelaHora
# precificacao/views.py (trecho)
from decimal import Decimal, ROUND_HALF_UP
from django.contrib.auth.decorators import login_required
from .models import Produto, ParametrosGlobais, TabelaHora, Unidade  # 👈 adicionado Unidade
from .services.pricing import preco_sugerido


# ✅ novo import
from django.contrib.auth.decorators import login_required
# (opcional) para staff only:
# from django.contrib.auth.decorators import user_passes_test

# --- models/forms que vamos usar ---
from .models import Produto, Orcamento, MateriaPrima, Cliente
from .forms import (
    ProdutoForm, ComponenteFormSet,
    OrcamentoForm, ItemFormSet,
    MateriaPrimaForm
)
from .services.pricing import preco_sugerido


# ------------------ NOVO: PAINEL ------------------
@login_required
def painel(request):
    now = timezone.now()
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_30d = now - timedelta(days=30)

    produtos_count   = Produto.objects.count()
    mps_count        = MateriaPrima.objects.count()
    clientes_count   = Cliente.objects.count()
    orcamentos_30d_q = Orcamento.objects.filter(criado_em__gte=start_30d).count()
    orcamentos_hoje  = Orcamento.objects.filter(criado_em__gte=start_today).count()
    receita_30d      = (Orcamento.objects
                        .filter(criado_em__gte=start_30d)
                        .aggregate(total=Sum('valor_total_cache'))['total']) or Decimal('0.00')

    recentes_produtos   = Produto.objects.order_by('-id')[:8]
    recentes_orcamentos = Orcamento.objects.order_by('-criado_em')[:8]

    ctx = dict(
        produtos_count=produtos_count,
        mps_count=mps_count,
        clientes_count=clientes_count,
        orcamentos_30d_q=orcamentos_30d_q,
        orcamentos_hoje=orcamentos_hoje,
        receita_30d=receita_30d,
        recentes_produtos=recentes_produtos,
        recentes_orcamentos=recentes_orcamentos,
    )
    return render(request, "precificacao/dashboard.html", ctx)


# ------------------ já existentes ------------------
@login_required
@transaction.atomic
def produto_create(request):
    if request.method == "POST":
        form = ProdutoForm(request.POST)
        if form.is_valid():
            produto = form.save()
            formset = ComponenteFormSet(request.POST, instance=produto)
            if formset.is_valid():
                formset.save()
                bd = preco_sugerido(produto, empresa=produto.empresa)
                produto.custo_estimado_cache = bd.custo_total
                produto.preco_sugerido_cache = bd.preco_final
                produto.save(update_fields=["custo_estimado_cache", "preco_sugerido_cache"])
                messages.success(request, "Produto salvo com sucesso!")
                return redirect("precificacao:produto_detail", pk=produto.pk)
        else:
            formset = ComponenteFormSet(request.POST)
    else:
        form = ProdutoForm()
        formset = ComponenteFormSet()
    return render(request, "precificacao/produto_form.html", {"form": form, "formset": formset})


Q2   = Decimal("0.01")
Q4   = Decimal("0.0001")

# Limiares de alerta (ajuste à vontade)
FOLHA_ALERT_THRESHOLD = Decimal("1.00")  # ⚠️ alerta se custo/folha > R$ 1,00

def _q(x, places=Q2):
    if x is None:
        x = Decimal("0")
    return Decimal(x).quantize(places, rounding=ROUND_HALF_UP)

@login_required
def produto_detail(request, pk):
    produto = get_object_or_404(Produto, pk=pk)
    bd = preco_sugerido(produto, empresa=produto.empresa)

    # parâmetros e tabela-hora (mesmo padrão usado no service)
    parametros, _ = ParametrosGlobais.objects.get_or_create(
        empresa=produto.empresa,
        defaults=dict(
            margem_lucro_padrao=Decimal("30.00"),
            acrescimo_padrao_percentual=Decimal("0.00"),
            impostos_percentual_sobre_venda=Decimal("0.00"),
            taxa_cartao_percentual=Decimal("0.00"),
            custo_energia_mensal=Decimal("0.00"),
            custo_internet_mensal=Decimal("0.00"),
            outros_custos_fixos_mensais=Decimal("0.00"),
            custo_tinta_por_percentual=Decimal("0.0000"),
            arredondar_para=Decimal("1.00"),
            incluir_taxas_no_preco_base=True,
        ),
    )
    tabela, _ = TabelaHora.objects.get_or_create(
        empresa=produto.empresa,
        defaults=dict(
            renda_mensal_desejada=Decimal("3000.00"),
            dias_trabalho_mes=22,
            horas_por_dia=Decimal("8.00"),
        ),
    )

    # ---------- Materiais (com ALERTAS) ----------
    comp_rows = []
    total_materiais = Decimal("0")
    alerts = []

    for comp in produto.componentes.select_related("materia_prima").all():
        mp = comp.materia_prima
        unit = _q(mp.custo_unitario_base, Q4)  # R$ por unidade_base
        perda_pct = Decimal(comp.perda_percentual or 0)
        qtd_uso = _q(comp.quantidade_uso, Q4)
        qtd_efetiva = _q(qtd_uso * (Decimal("1") + perda_pct/Decimal("100")), Q4)
        subtotal = _q(unit * qtd_efetiva, Q2)
        total_materiais += subtotal

        # ---- regras de alerta ----
        alert = False
        alert_msg = None

        # (A) Folha muito cara
        if mp.unidade_base == Unidade.FOLHA and unit > FOLHA_ALERT_THRESHOLD:
            alert = True
            alert_msg = f"Custo por folha alto (R$ {unit}). Verifique fator (ex.: resma→500) e unidade base."

        # (B) Conversão suspeita: compra ≠ base mas fator ≤ 1
        if not alert and mp.unidade_compra != mp.unidade_base and (mp.fator_conversao_para_base or Decimal("0")) <= 1:
            alert = True
            alert_msg = "Fator de conversão possivelmente incorreto (compra ≠ base, fator ≤ 1)."

        # (C) Resma→Folha com fator muito baixo
        if not alert and mp.unidade_compra == Unidade.RESMA and mp.unidade_base == Unidade.FOLHA and (mp.fator_conversao_para_base or 0) < 300:
            alert = True
            alert_msg = "Resma para folha com fator baixo (<300). Normalmente é 500."

        if alert:
            alerts.append({
                "mp_nome": mp.nome,
                "msg": alert_msg,
            })

        comp_rows.append({
            "mp_nome": mp.nome,
            "un_base": mp.unidade_base,
            "unit": unit,
            "qtd": qtd_uso,
            "perda": perda_pct,
            "qtd_efetiva": qtd_efetiva,
            "subtotal": subtotal,
            "alert": alert,
            "alert_msg": alert_msg,
        })

    # ---------- Mão de obra e fixos ----------
    horas_mes = _q(tabela.horas_mes(), Q4)
    mao_hora  = _q(tabela.mao_de_obra_hora(parametros), Q4)
    fixos_hora = _q(tabela.fixos_hora(parametros), Q4)
    custo_hora_total = _q(tabela.custo_hora_total(parametros), Q4)
    custo_minuto_total = _q(tabela.custo_minuto_total(parametros), Q4)
    tempo_min = Decimal(produto.tempo_producao_minutos or 0)
    custo_mo = _q(custo_minuto_total * tempo_min, Q2)

    # ---------- Tinta ----------
    tinta_pct = Decimal(produto.percentual_tinta or 0)
    tinta_custo_pct = Decimal(parametros.custo_tinta_por_percentual or 0)
    custo_tinta = _q((tinta_pct/Decimal("100")) * tinta_custo_pct, Q2) if produto.usa_percentual_tinta else Decimal("0.00")

    margem_usada = (Decimal(produto.margem_lucro_override)
                    if produto.margem_lucro_override is not None
                    else Decimal(parametros.margem_lucro_padrao))
    impostos_pct = Decimal(parametros.impostos_percentual_sobre_venda or 0)
    taxa_cartao_pct = Decimal(parametros.taxa_cartao_percentual or 0)
    acrescimo_pct = Decimal(parametros.acrescimo_padrao_percentual or 0)

    ctx = {
        "produto": produto,
        "bd": bd,

        "comp_rows": comp_rows,
        "alerts": alerts,                    # 👈 lista de alertas para o topo
        "total_materiais": _q(total_materiais, Q2),

        "horas_mes": horas_mes,
        "renda_mensal": _q(tabela.renda_mensal_desejada, Q2),
        "mao_hora": mao_hora,
        "fixos_hora": fixos_hora,
        "custo_hora_total": custo_hora_total,
        "custo_minuto_total": custo_minuto_total,
        "tempo_min": tempo_min,
        "custo_mo": custo_mo,

        "usa_tinta": produto.usa_percentual_tinta,
        "tinta_pct": tinta_pct,
        "tinta_custo_pct": _q(tinta_custo_pct, Q2),
        "custo_tinta": _q(custo_tinta, Q2),

        "margem_usada": _q(margem_usada, Q2),
        "impostos_pct": _q(impostos_pct, Q2),
        "taxa_cartao_pct": _q(taxa_cartao_pct, Q2),
        "acrescimo_pct": _q(acrescimo_pct, Q2),
        "arredondar_para": _q(parametros.arredondar_para, Q2),
    }
    return render(request, "precificacao/produto_detail.html", ctx)




@login_required
@transaction.atomic
def orcamento_create(request):
    if request.method == "POST":
        form = OrcamentoForm(request.POST)
        if form.is_valid():
            orc = form.save(commit=False)
            orc.save()
            orc.numero = f"{orc.criado_em:%Y%m%d}-{orc.pk}"
            orc.save(update_fields=["numero"])

            formset = ItemFormSet(request.POST, instance=orc)
            if formset.is_valid():
                itens = formset.save(commit=False)
                for it in itens:
                    bd = preco_sugerido(it.produto, empresa=orc.empresa)
                    it.preco_unitario = bd.preco_final
                    it.save()
                formset.save_m2m()

                total = sum((i.subtotal() for i in orc.itens.all()), Decimal("0"))
                if orc.desconto_percentual:
                    total *= (Decimal("1") - Decimal(orc.desconto_percentual)/Decimal("100"))
                if orc.acrescimo_percentual:
                    total *= (Decimal("1") + Decimal(orc.acrescimo_percentual)/Decimal("100"))
                orc.valor_total_cache = total.quantize(Decimal("0.01"))
                orc.save(update_fields=["valor_total_cache"])
                messages.success(request, "Orçamento criado!")
                return redirect("precificacao:orcamento_detail", pk=orc.pk)
        else:
            formset = ItemFormSet(request.POST)
    else:
        form = OrcamentoForm()
        formset = ItemFormSet()
    return render(request, "precificacao/orcamento_form.html", {"form": form, "formset": formset})


@login_required
def orcamento_detail(request, pk):
    orc = get_object_or_404(Orcamento, pk=pk)
    return render(request, "precificacao/orcamento_detail.html", {"orc": orc})


# --------- NOVO: cadastro rápido de Matéria-prima ----------
@login_required
@transaction.atomic
def materia_prima_create(request):
    if request.method == "POST":
        form = MateriaPrimaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Matéria-prima salva!")
            return redirect("precificacao:dashboard")
    else:
        form = MateriaPrimaForm()
    return render(request, "precificacao/materia_prima_form.html", {"form": form})


@login_required
def orcamento_print(request, pk):
    orc = get_object_or_404(Orcamento, pk=pk)
    validade_ate = orc.criado_em + timedelta(days=orc.validade_dias or 0)
    auto = request.GET.get("auto")  # ?auto=1 para imprimir automaticamente
    ctx = {"orc": orc, "validade_ate": validade_ate, "auto": auto}
    return render(request, "precificacao/orcamento_print.html", ctx)
