# precificacao/services/pricing.py
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from ..models import Produto, ComponenteProduto, ParametrosGlobais, TabelaHora, MateriaPrima, Empresa

TWO  = Decimal("0.01")
FOUR = Decimal("0.0001")

@dataclass
class Breakdown:
    materiais: Decimal
    mao_de_obra: Decimal
    tinta: Decimal
    custo_total: Decimal
    margem_percentual: Decimal
    preco_sem_taxas: Decimal
    impostos: Decimal
    taxa_cartao: Decimal
    acrescimo_padrao: Decimal
    preco_final: Decimal

def _q(x, places=TWO):
    return (x or Decimal("0")).quantize(places, rounding=ROUND_HALF_UP)

def _arredondar(valor: Decimal, passo: Decimal) -> Decimal:
    if not passo or passo <= 0:
        return _q(valor, TWO)
    # arredonda para múltiplos de "passo" (ex.: 1.00 = inteiro, 0.05 = 5 centavos)
    mult = (valor / passo).to_integral_value(rounding=ROUND_HALF_UP)
    return _q(mult * passo, TWO)

def custo_materiais(produto: Produto) -> Decimal:
    total = Decimal("0")
    for comp in produto.componentes.select_related("materia_prima").all():
        mp: MateriaPrima = comp.materia_prima
        unit = mp.custo_unitario_base  # já converte resma→folha etc.
        # perda definida NO COMPONENTE (por item). Se quiser somar com a perda da MP, troque por: (comp.perda_percentual + mp.perda_percentual)
        perda = (Decimal(comp.perda_percentual) or Decimal("0")) / Decimal("100")
        # usa-se a aproximação (1 + perda) para superfaturar a quantidade conforme perda
        qtd_efetiva = Decimal(comp.quantidade_uso) * (Decimal("1") + perda)
        total += unit * qtd_efetiva
    return _q(total, FOUR)

def custo_mao_de_obra(produto: Produto, parametros: ParametrosGlobais, tabela: TabelaHora) -> Decimal:
    custo_min = tabela.custo_minuto_total(parametros)
    return _q(custo_min * Decimal(produto.tempo_producao_minutos or 0), FOUR)

def custo_tinta_percentual(produto: Produto, parametros: ParametrosGlobais) -> Decimal:
    if not produto.usa_percentual_tinta:
        return Decimal("0")
    pct = (Decimal(produto.percentual_tinta) or Decimal("0")) / Decimal("100")
    # custo por 1% de cobertura (ex.: A4). Ajuste conforme sua unidade: aqui multiplicamos a % pela tabela
    return _q(pct * Decimal(parametros.custo_tinta_por_percentual or 0), FOUR)

def preco_sugerido(produto: Produto, empresa: Empresa) -> Breakdown:
    # garante existência de parâmetros e tabela hora
    parametros, _ = ParametrosGlobais.objects.get_or_create(
        empresa=empresa,
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
        empresa=empresa,
        defaults=dict(
            renda_mensal_desejada=Decimal("3000.00"),
            dias_trabalho_mes=22,
            horas_por_dia=Decimal("8.00"),
        ),
    )

    materiais = custo_materiais(produto)
    mo        = custo_mao_de_obra(produto, parametros, tabela)
    tinta     = custo_tinta_percentual(produto, parametros)

    custo_total = _q(materiais + mo + tinta, FOUR)

    margem = Decimal(produto.margem_lucro_override) if produto.margem_lucro_override is not None else Decimal(parametros.margem_lucro_padrao)
    preco_sem_taxas = _q(custo_total * (Decimal("1") + margem/Decimal("100")), FOUR)

    impostos = _q(preco_sem_taxas * (Decimal(parametros.impostos_percentual_sobre_venda) / Decimal("100")), FOUR)
    com_impostos = _q(preco_sem_taxas + impostos, FOUR)

    taxa_cartao = _q(com_impostos * (Decimal(parametros.taxa_cartao_percentual) / Decimal("100")), FOUR)
    com_taxas = _q(com_impostos + taxa_cartao, FOUR)

    acrescimo_padrao = _q(com_taxas * (Decimal(parametros.acrescimo_padrao_percentual) / Decimal("100")), FOUR)
    bruto = _q(com_taxas + acrescimo_padrao, FOUR)

    final = _arredondar(bruto, Decimal(parametros.arredondar_para or 0))

    return Breakdown(
        materiais=_q(materiais),
        mao_de_obra=_q(mo),
        tinta=_q(tinta),
        custo_total=_q(custo_total),
        margem_percentual=_q(margem),
        preco_sem_taxas=_q(preco_sem_taxas),
        impostos=_q(impostos),
        taxa_cartao=_q(taxa_cartao),
        acrescimo_padrao=_q(acrescimo_padrao),
        preco_final=_q(final),
    )
