from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory
from .models import (
    Empresa, Cliente,
    Produto, ComponenteProduto,
    MateriaPrima, Orcamento, ItemOrcamento,
    Unidade
)

# =========================
# Matéria-prima
# =========================
class MateriaPrimaForm(forms.ModelForm):
    # Helpers opcionais para calcular fator_conversao_para_base automaticamente
    folhas_por_resma = forms.DecimalField(
        required=False, min_value=Decimal("1"), label="Folhas por resma (ajuda)"
    )
    largura_cm = forms.DecimalField(
        required=False, min_value=Decimal("0"), decimal_places=2, label="Largura do rolo (cm, ajuda)"
    )
    comprimento_m = forms.DecimalField(
        required=False, min_value=Decimal("0"), decimal_places=2, label="Comprimento do rolo (m, ajuda)"
    )

    class Meta:
        model = MateriaPrima
        fields = [
            "empresa", "nome",
            "unidade_compra", "quantidade_compra", "custo_compra",
            "unidade_base", "fator_conversao_para_base",
            "perda_percentual", "observacoes",
        ]

    def clean(self):
        data = super().clean()
        unidade_base = data.get("unidade_base")
        fator = data.get("fator_conversao_para_base")
        qtd_compra = data.get("quantidade_compra") or Decimal("1")
        folhas = data.get("folhas_por_resma")
        largura = data.get("largura_cm")
        comp = data.get("comprimento_m")

        # Se já preencheu fator manualmente, respeita.
        if fator and fator > 0:
            return data

        calc = None
        if unidade_base == Unidade.FOLHA and folhas:
            # RESMA -> FOLHA
            calc = folhas * qtd_compra
        elif unidade_base == Unidade.M and comp:
            # rolo por metro linear
            calc = comp * qtd_compra
        elif unidade_base == Unidade.M2 and largura and comp:
            # rolo por m²
            largura_m = largura / Decimal("100")
            calc = (largura_m * comp) * qtd_compra

        if calc and calc > 0:
            data["fator_conversao_para_base"] = calc

        return data


# =========================
# Produto + Componentes
# =========================
class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produto
        fields = [
            "empresa", "nome", "codigo", "descricao",
            "tempo_producao_minutos",
            "usa_percentual_tinta", "percentual_tinta",
            "margem_lucro_override", "ativo",
        ]


class ComponenteProdutoForm(forms.ModelForm):
    # Modo de entrada (campos auxiliares NÃO vão para o modelo)
    MODO_CHOICES = (
        ("quantidade", "Quantidade direta / Páginas"),
        ("area", "Área (cm) → m²"),
        ("comprimento", "Comprimento (m)"),
    )
    modo_entrada = forms.ChoiceField(choices=MODO_CHOICES, initial="quantidade", required=False)

    # Helpers gerais
    largura_cm = forms.DecimalField(required=False, min_value=Decimal("0"), decimal_places=2, label="Largura (cm)")
    altura_cm = forms.DecimalField(required=False, min_value=Decimal("0"), decimal_places=2, label="Altura (cm)")
    comprimento_m = forms.DecimalField(required=False, min_value=Decimal("0"), decimal_places=2, label="Comprimento (m)")

    # Helpers para PAPEL (base = FOLHA): cálculo por páginas/itens/duplex
    paginas = forms.IntegerField(required=False, min_value=1, label="Páginas")
    itens_por_folha = forms.IntegerField(required=False, min_value=1, initial=1, label="Itens/folha")
    duplex = forms.BooleanField(required=False, initial=False, label="Duplex (frente/verso)")

    class Meta:
        model = ComponenteProduto
        fields = ["materia_prima", "quantidade_uso", "perda_percentual", "observacoes"]

    def clean(self):
        cd = super().clean()
        mp: MateriaPrima = cd.get("materia_prima")
        modo = (self.data.get(self.add_prefix("modo_entrada")) or cd.get("modo_entrada") or "quantidade").lower()

        if modo == "area":
            # requer M2
            if not mp or mp.unidade_base != Unidade.M2:
                raise forms.ValidationError("Modo 'Área' requer matéria-prima com unidade base em m².")
            L = self._dec(self.data.get(self.add_prefix("largura_cm")))
            A = self._dec(self.data.get(self.add_prefix("altura_cm")))
            if L is None or A is None or L <= 0 or A <= 0:
                raise forms.ValidationError("Informe largura e altura (cm) válidas para calcular a área.")
            cd["quantidade_uso"] = (L * A) / Decimal("10000")  # cm² → m²

        elif modo == "comprimento":
            # requer M
            if not mp or mp.unidade_base != Unidade.M:
                raise forms.ValidationError("Modo 'Comprimento' requer matéria-prima com unidade base em metros.")
            comp = self._dec(self.data.get(self.add_prefix("comprimento_m")))
            if comp is None or comp <= 0:
                raise forms.ValidationError("Informe o comprimento (m) para calcular a quantidade.")
            cd["quantidade_uso"] = comp

        else:
            # quantidade direta OU cálculo por páginas (para FOLHA)
            q = cd.get("quantidade_uso")

            # Se for papel (base FOLHA) e vierem páginas, calculamos folhas
            if mp and mp.unidade_base == Unidade.FOLHA:
                pags = self._int(self.data.get(self.add_prefix("paginas")))
                itens = self._int(self.data.get(self.add_prefix("itens_por_folha"))) or 1
                is_duplex = bool(self.data.get(self.add_prefix("duplex")))
                if pags:
                    denom = itens * (2 if is_duplex else 1)
                    if denom <= 0:
                        raise forms.ValidationError("Itens/folha inválido.")
                    # ceil(pags / denom)
                    folhas = (pags + denom - 1) // denom
                    cd["quantidade_uso"] = Decimal(folhas)

            # validação final
            q2 = cd.get("quantidade_uso")
            if q2 is None or q2 <= 0:
                raise forms.ValidationError("Informe a quantidade de uso (ou páginas) maior que zero.")

        return cd

    @staticmethod
    def _dec(v):
        try:
            if v in (None, ""):
                return None
            return Decimal(str(v))
        except Exception:
            return None

    @staticmethod
    def _int(v):
        try:
            if v in (None, ""):
                return None
            return int(Decimal(str(v)))
        except Exception:
            return None


ComponenteFormSet = inlineformset_factory(
    parent_model=Produto,
    model=ComponenteProduto,
    form=ComponenteProdutoForm,
    extra=1,
    can_delete=True,
)


# =========================
# Orçamento + Itens
# =========================
class OrcamentoForm(forms.ModelForm):
    class Meta:
        model = Orcamento
        fields = [
            "empresa", "cliente",
            "validade_dias",
            "desconto_percentual", "acrescimo_percentual",
            "status", "observacoes",
        ]


class ItemOrcamentoForm(forms.ModelForm):
    # preço opcional; se vier vazio a view calcula pela precificação
    preco_unitario = forms.DecimalField(required=False)

    class Meta:
        model = ItemOrcamento
        fields = ["produto", "quantidade", "preco_unitario", "descricao_externa"]


ItemFormSet = inlineformset_factory(
    parent_model=Orcamento,
    model=ItemOrcamento,
    form=ItemOrcamentoForm,
    extra=1,
    can_delete=True,
)
