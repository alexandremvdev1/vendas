# vendas/forms.py
from django import forms
import re
from .models import Address

# ---------------- UF choices (embed no forms para não depender do models) ----------------
UF_CHOICES = [
    ("AC", "AC"), ("AL", "AL"), ("AP", "AP"), ("AM", "AM"), ("BA", "BA"),
    ("CE", "CE"), ("DF", "DF"), ("ES", "ES"), ("GO", "GO"), ("MA", "MA"),
    ("MT", "MT"), ("MS", "MS"), ("MG", "MG"), ("PA", "PA"), ("PB", "PB"),
    ("PR", "PR"), ("PE", "PE"), ("PI", "PI"), ("RJ", "RJ"), ("RN", "RN"),
    ("RS", "RS"), ("RO", "RO"), ("RR", "RR"), ("SC", "SC"), ("SP", "SP"),
    ("SE", "SE"), ("TO", "TO"),
]

# ---------------- helpers ----------------
def _only_digits(s: str) -> str:
    return re.sub(r"\D", "", (s or ""))

def _format_cpf(digits: str) -> str:
    if len(digits) != 11:
        return digits
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"

def _normalize_br_phone_to_e164(v: str) -> str:
    s = _only_digits(v)
    if not s:
        return ""
    s = s.lstrip("0")
    core = s[2:] if s.startswith("55") else s
    if len(core) > 11:
        core = core[-11:]
    return f"+55{core}" if core else ""

# ---------------- forms ----------------
class CheckoutForm(forms.Form):
    full_name = forms.CharField(
        label="Nome completo",
        max_length=160,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "name",
            "placeholder": "Seu nome completo",
            "autofocus": "autofocus",
        })
    )
    cpf = forms.CharField(
        label="CPF",
        max_length=14,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "inputmode": "numeric",
            "placeholder": "000.000.000-00",
            "data-mask": "cpf",
        })
    )
    phone = forms.CharField(
        label="Telefone/WhatsApp",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "inputmode": "tel",
            "placeholder": "(00) 00000-0000",
            "data-mask": "phone",
        })
    )
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "autocomplete": "email",
            "placeholder": "seu@email.com",
        })
    )

    # validações/normalizações
    def clean_full_name(self):
        name = (self.cleaned_data.get("full_name") or "").strip()
        if " " not in name:
            raise forms.ValidationError("Informe nome e sobrenome.")
        return re.sub(r"\s+", " ", name)

    def clean_cpf(self):
        raw = self.cleaned_data.get("cpf") or ""
        digits = _only_digits(raw)
        if len(digits) != 11:
            raise forms.ValidationError("CPF deve ter 11 dígitos.")
        return _format_cpf(digits)

    def clean_phone(self):
        raw = (self.cleaned_data.get("phone") or "").strip()
        if not raw:
            return ""
        e164 = _normalize_br_phone_to_e164(raw)
        if not re.fullmatch(r"\+55\d{10,11}", e164):
            raise forms.ValidationError("Telefone inválido. Use DDD + número (ex.: (63) 99999-9999).")
        return e164


class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        # IMPORTANTe: estes campos devem existir no teu models.Address
        fields = [
            "recipient_name",
            "cep",
            "street",
            "number",
            "complement",
            "neighborhood",
            "city",
            "state",
            "country",  # se preferir ocultar, pode manter default no model e tirar do form/template
        ]
        labels = {
            "recipient_name": "Nome do destinatário",
            "cep": "CEP",
            "street": "Logradouro",
            "number": "Número",
            "complement": "Complemento",
            "neighborhood": "Bairro",
            "city": "Cidade",
            "state": "UF",
            "country": "País",
        }
        widgets = {
            "recipient_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Quem vai receber (opcional)"
            }),
            "cep": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "00000-000",
                "id": "id_cep",
            }),
            "street": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Rua/Av."
            }),
            "number": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Nº"
            }),
            "complement": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Apto, bloco... (opcional)"
            }),
            "neighborhood": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Bairro"
            }),
            "city": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Cidade"
            }),
            "state": forms.Select(choices=UF_CHOICES, attrs={"class": "form-select"}),
            "country": forms.TextInput(attrs={
                "class": "form-control",
                "value": "Brasil"
            }),
        }

    def clean_cep(self):
        cep = (self.cleaned_data.get("cep") or "").strip()
        digits = _only_digits(cep)
        if len(digits) != 8:
            raise forms.ValidationError("Informe um CEP válido com 8 dígitos.")
        return f"{digits[:5]}-{digits[5:]}"

    def clean_state(self):
        uf = (self.cleaned_data.get("state") or "").strip().upper()
        valid = {u for u, _ in UF_CHOICES}
        if uf not in valid:
            raise forms.ValidationError("UF inválida.")
        return uf

    def clean_country(self):
        c = (self.cleaned_data.get("country") or "").strip()
        return c or "Brasil"
