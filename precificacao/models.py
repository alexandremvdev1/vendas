from decimal import Decimal
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")

class Empresa(models.Model):
    nome_fantasia = models.CharField(max_length=120)
    razao_social = models.CharField(max_length=200, blank=True)
    cnpj = models.CharField(max_length=18, blank=True)
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    endereco = models.CharField(max_length=200, blank=True)
    cidade = models.CharField(max_length=100, blank=True)
    estado = models.CharField(max_length=2, blank=True)

    def __str__(self):
        return self.nome_fantasia or f"Empresa {self.pk}"

class Cliente(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    nome = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    documento = models.CharField(max_length=30, blank=True)
    endereco = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return self.nome

class ParametrosGlobais(models.Model):
    """Parâmetros por empresa que influenciam o preço final."""
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE)

    # Margens e taxas (%): usar 0–100
    margem_lucro_padrao = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("30.00"))
    acrescimo_padrao_percentual = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    desconto_max_percentual = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("15.00"))

    impostos_percentual_sobre_venda = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    taxa_cartao_percentual = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))

    # Custos fixos rateados por hora
    custo_energia_mensal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    custo_internet_mensal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    outros_custos_fixos_mensais = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # Tinta (opcional): R$ por 1% de cobertura A4 – se usar "percentual de tinta" no produto
    custo_tinta_por_percentual = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0.0000"))

    # Arredondamento (ex.: 0.05 para 5 centavos, 1.00 para inteiro)
    arredondar_para = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("1.00"))

    incluir_taxas_no_preco_base = models.BooleanField(default=True)

    def __str__(self):
        return f"Parâmetros – {self.empresa}"

class TabelaHora(models.Model):
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE)
    renda_mensal_desejada = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    dias_trabalho_mes = models.PositiveIntegerField(default=22)
    horas_por_dia = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("8.00"))

    def horas_mes(self) -> Decimal:
        return (Decimal(self.dias_trabalho_mes) * Decimal(self.horas_por_dia)).quantize(FOUR_PLACES)

    def mao_de_obra_hora(self, parametros: ParametrosGlobais) -> Decimal:
        # Mão de obra (renda desejada / horas no mês)
        h_mes = self.horas_mes() or Decimal("1")
        return (Decimal(self.renda_mensal_desejada) / h_mes).quantize(FOUR_PLACES)

    def fixos_hora(self, parametros: ParametrosGlobais) -> Decimal:
        fixos = (parametros.custo_energia_mensal + parametros.custo_internet_mensal + parametros.outros_custos_fixos_mensais)
        h_mes = self.horas_mes() or Decimal("1")
        return (Decimal(fixos) / h_mes).quantize(FOUR_PLACES)

    def custo_hora_total(self, parametros: ParametrosGlobais) -> Decimal:
        return (self.mao_de_obra_hora(parametros) + self.fixos_hora(parametros)).quantize(FOUR_PLACES)

    def custo_minuto_total(self, parametros: ParametrosGlobais) -> Decimal:
        return (self.custo_hora_total(parametros) / Decimal("60")).quantize(FOUR_PLACES)

    def __str__(self):
        return f"TabelaHora – {self.empresa}"

class Unidade(models.TextChoices):
    UN = "UN", "Unidade"
    FOLHA = "FOLHA", "Folha"
    RESMA = "RESMA", "Resma (500 folhas)"
    ML = "ML", "Mililitro"
    L = "L", "Litro"
    G = "G", "Grama"
    KG = "KG", "Quilo"
    M = "M", "Metro"
    M2 = "M2", "Metro quadrado"
    CX = "CX", "Caixa"

class MateriaPrima(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    nome = models.CharField(max_length=120)

    # Como você compra
    unidade_compra = models.CharField(max_length=10, choices=Unidade.choices, default=Unidade.UN)
    quantidade_compra = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"), validators=[MinValueValidator(Decimal("0.0001"))])
    custo_compra = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    # Como você usa
    unidade_base = models.CharField(max_length=10, choices=Unidade.choices, default=Unidade.UN)
    fator_conversao_para_base = models.DecimalField(
        max_digits=12, decimal_places=6, default=Decimal("1.000000"),
        help_text="Ex.: Resma→Folha = 500; Caixa→Unidade = qtd de itens na caixa."
    )

    perda_percentual = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="% de perda média ao usar (corte, testes etc.)"
    )

    observacoes = models.TextField(blank=True)

    class Meta:
        unique_together = ("empresa", "nome")

    def __str__(self):
        return self.nome

    @property
    def custo_unitario_base(self) -> Decimal:
        # custo por unidade_base
        qty_base = (self.quantidade_compra * self.fator_conversao_para_base) if self.unidade_compra != self.unidade_base else self.quantidade_compra
        if not qty_base:
            return Decimal("0")
        return (self.custo_compra / qty_base).quantize(FOUR_PLACES)

class Produto(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    nome = models.CharField(max_length=150)
    codigo = models.CharField(max_length=50, blank=True)
    descricao = models.TextField(blank=True)

    tempo_producao_minutos = models.PositiveIntegerField(default=0)

    # Opcional: custo extra por tinta via percentual de cobertura
    usa_percentual_tinta = models.BooleanField(default=False)
    percentual_tinta = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))  # 0–100

    margem_lucro_override = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    ativo = models.BooleanField(default=True)

    # caches (apenas informativos; o cálculo oficial vem do service)
    custo_estimado_cache = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    preco_sugerido_cache = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return self.nome

class ComponenteProduto(models.Model):
    produto = models.ForeignKey(Produto, on_delete=models.CASCADE, related_name="componentes")
    materia_prima = models.ForeignKey(MateriaPrima, on_delete=models.PROTECT)
    quantidade_uso = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))])
    perda_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    observacoes = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("produto", "materia_prima")

    def __str__(self):
        return f"{self.materia_prima} em {self.produto}"

class Orcamento(models.Model):
    STATUS = (
        ("rascunho", "Rascunho"),
        ("enviado", "Enviado"),
        ("aprovado", "Aprovado"),
        ("rejeitado", "Rejeitado"),
    )

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT)
    numero = models.CharField(max_length=30, unique=True, blank=True)
    criado_em = models.DateTimeField(default=timezone.now)
    validade_dias = models.PositiveIntegerField(default=7)

    desconto_percentual = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    acrescimo_percentual = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=12, choices=STATUS, default="rascunho")
    observacoes = models.TextField(blank=True)

    valor_total_cache = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    def __str__(self):
        return f"Orçamento {self.numero or self.pk} – {self.cliente}"

class ItemOrcamento(models.Model):
    orcamento = models.ForeignKey(Orcamento, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"), validators=[MinValueValidator(Decimal("0.01"))])

    # Snapshot de preço no momento do orçamento
    preco_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    descricao_externa = models.CharField(max_length=200, blank=True)

    def subtotal(self) -> Decimal:
        return (self.quantidade * self.preco_unitario).quantize(TWO_PLACES)

    def __str__(self):
        return f"{self.produto} x {self.quantidade}"