from django import forms

from .models import Customer, CustomerVehicle, Sale


class CustomerForm(forms.ModelForm):
    def __init__(self, *a, tenant, branch=None, **kw):
        super().__init__(*a, **kw)
        self.instance.tenant = tenant

    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address", "customer_type", "credit_limit", "notes", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}


class VehicleForm(forms.ModelForm):
    class Meta:
        model = CustomerVehicle
        fields = ["reg_number", "make", "model", "year", "engine", "vin", "notes"]

    def clean_reg_number(self):
        return self.cleaned_data["reg_number"].strip().upper()


class PaymentForm(forms.Form):
    amount = forms.DecimalField(min_value=0.01, decimal_places=2)
    method = forms.ChoiceField(choices=[c for c in Sale.Method.choices if c[0] != "credit"])
    reference = forms.CharField(required=False, max_length=60)
