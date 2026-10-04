from django import forms

from catalog.models import Part

from .models import PurchaseOrder


class PartQtyMixin(forms.Form):
    part = forms.ModelChoiceField(Part.objects.filter(is_active=True), widget=forms.HiddenInput)

    def __init__(self, *a, tenant, branch=None, **kw):
        super().__init__(*a, **kw)
        self.fields["part"].queryset = Part.objects.filter(tenant=tenant, is_active=True)


class AdjustForm(PartQtyMixin):
    REASONS = [("Stock-take count", "Stock-take count"), ("Damaged", "Damaged"),
               ("Lost / stolen", "Lost / stolen"), ("Found", "Found"), ("Other", "Other")]
    counted = forms.DecimalField(label="Counted quantity", min_value=0, decimal_places=2)
    reason = forms.ChoiceField(choices=REASONS)
    note = forms.CharField(required=False, max_length=150)


class POForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["supplier", "expected_date", "notes"]
        widgets = {"expected_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, tenant, branch=None, **kw):
        super().__init__(*a, **kw)
        self.instance.branch = branch
        self.fields["supplier"].queryset = self.fields["supplier"].queryset.filter(tenant=tenant, is_active=True)


class POLineForm(PartQtyMixin):
    quantity = forms.DecimalField(min_value=0.01, decimal_places=2)
    unit_cost = forms.DecimalField(min_value=0, decimal_places=2, required=False,
                                   help_text="Blank = the part's current cost")
