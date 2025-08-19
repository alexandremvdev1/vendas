from decimal import Decimal

def fator_resma_para_folha(qtd_resmas: Decimal, folhas_por_resma=Decimal("500")) -> Decimal:
    return (qtd_resmas * folhas_por_resma)

def fator_rolo_m2(largura_cm: Decimal, comprimento_m: Decimal, qtd_rolos=Decimal("1")) -> Decimal:
    largura_m = largura_cm / Decimal("100")
    area_total_m2 = largura_m * comprimento_m * qtd_rolos
    return area_total_m2  # usar como fator_conversao_para_base com unidade_base=M2

def fator_rolo_m(largura_cm: Decimal, comprimento_m: Decimal, qtd_rolos=Decimal("1")) -> Decimal:
    return comprimento_m * qtd_rolos  # usar como fator_conversao_para_base com unidade_base=M