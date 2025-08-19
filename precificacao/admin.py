from django.contrib import admin
from .models import (
    Empresa, Cliente, ParametrosGlobais, TabelaHora,
    MateriaPrima, Produto, ComponenteProduto,
    Orcamento, ItemOrcamento
)

@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nome_fantasia", "cidade", "estado")

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nome", "email", "telefone", "empresa")
    list_filter = ("empresa",)
    search_fields = ("nome", "email", "documento")

@admin.register(ParametrosGlobais)
class ParametrosAdmin(admin.ModelAdmin):
    list_display = ("empresa", "margem_lucro_padrao", "impostos_percentual_sobre_venda", "taxa_cartao_percentual")

@admin.register(TabelaHora)
class TabelaHoraAdmin(admin.ModelAdmin):
    list_display = ("empresa", "renda_mensal_desejada", "dias_trabalho_mes", "horas_por_dia")

class ComponenteInline(admin.TabularInline):
    model = ComponenteProduto
    extra = 1

@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ("nome", "empresa", "tempo_producao_minutos", "preco_sugerido_cache")
    list_filter = ("empresa", "ativo")
    search_fields = ("nome", "codigo")
    inlines = [ComponenteInline]

@admin.register(MateriaPrima)
class MateriaPrimaAdmin(admin.ModelAdmin):
    list_display = ("nome", "empresa", "unidade_compra", "quantidade_compra", "custo_compra", "unidade_base", "fator_conversao_para_base", "custo_unitario_base")
    list_filter = ("empresa", "unidade_compra", "unidade_base")
    search_fields = ("nome",)

class ItemInline(admin.TabularInline):
    model = ItemOrcamento
    extra = 1

@admin.register(Orcamento)
class OrcamentoAdmin(admin.ModelAdmin):
    list_display = ("numero", "empresa", "cliente", "status", "valor_total_cache", "criado_em")
    list_filter = ("empresa", "status")
    date_hierarchy = "criado_em"
    inlines = [ItemInline]