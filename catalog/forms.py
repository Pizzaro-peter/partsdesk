from django import forms

from core.models import Branch, Sequence

from .models import BranchStock, Category, Part, PartNumber, Supplier, Vehicle


class PartForm(forms.ModelForm):
    cost_price = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2, initial=0)
    sell_price = forms.DecimalField(label="Retail price", min_value=0, max_digits=12, decimal_places=2, initial=0)
    trade_price = forms.DecimalField(label="Trade price", required=False, min_value=0, max_digits=12, decimal_places=2)
    bin_location = forms.CharField(required=False, max_length=40)
    reorder_level = forms.DecimalField(required=False, min_value=0, decimal_places=2, initial=0)
    reorder_qty = forms.DecimalField(required=False, min_value=0, decimal_places=2, initial=0)
    class Meta:
        model = Part
        fields = [
            "sku", "barcode", "name", "category", "brand", "part_type", "condition", "oem_number",
            "unit", "bin_location", "cost_price", "sell_price", "trade_price",
            "reorder_level", "reorder_qty", "preferred_supplier", "is_universal", "is_active", "description",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        self.tenant = kwargs.pop("tenant")
        self.branch = kwargs.pop("branch")
        super().__init__(*args, **kwargs)
        self.instance.tenant = self.tenant
        self.fields["sku"].required = False
        self.fields["sku"].help_text = "Leave blank to generate one automatically."
        self.fields["preferred_supplier"].queryset = Supplier.objects.filter(tenant=self.tenant, is_active=True)
        self.fields["category"].queryset = Category.objects.filter(tenant=self.tenant)
        if self.instance.pk:
            item = BranchStock.objects.filter(branch=self.branch, part=self.instance).first()
            if item:
                for field in ("bin_location", "reorder_level", "reorder_qty"):
                    self.fields[field].initial = getattr(item, field)

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip().upper()
        sku = sku or f"{self.branch.code}-{Sequence.next(self.branch, 'part', 'P')}"
        if Part.objects.filter(tenant=self.tenant, sku=sku).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This SKU already exists in this business.")
        return sku

    def clean_barcode(self):
        code = (self.cleaned_data.get("barcode") or "").strip() or None
        if code and Part.objects.filter(tenant=self.tenant, barcode=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This barcode already exists in this business.")
        return code

    def save(self, commit=True):
        part = super().save(commit=commit)
        if commit:
            BranchStock.objects.bulk_create([
                BranchStock(branch=b, part=part, cost_price=part.cost_price)
                for b in Branch.objects.filter(tenant=self.tenant).exclude(pk=self.branch.pk)
            ], ignore_conflicts=True)
            item, _ = BranchStock.objects.get_or_create(branch=self.branch, part=part,
                                                        defaults={"cost_price": part.cost_price})
            for field in ("bin_location", "reorder_level", "reorder_qty"):
                setattr(item, field, self.cleaned_data.get(field) or ("" if field == "bin_location" else 0))
            item.save()
        return part


class PartCreateForm(PartForm):
    opening_qty = forms.DecimalField(
        label="Opening stock", required=False, min_value=0, decimal_places=2, initial=0,
        help_text="How many are on the shelf right now.")


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]

    def __init__(self, *a, tenant, **kw):
        super().__init__(*a, **kw)
        self.instance.tenant = tenant

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Category.objects.filter(tenant=self.instance.tenant, name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Category already exists in this business.")
        return name


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ["make", "model", "year_from", "year_to", "engine"]

    def __init__(self, *a, tenant, branch=None, **kw):
        super().__init__(*a, **kw)
        self.instance.tenant = tenant

    def clean(self):
        data = super().clean()
        fields = ("make", "model", "year_from", "year_to", "engine")
        if all(f in data for f in fields) and Vehicle.objects.filter(
            tenant=self.instance.tenant, **{f: data[f] for f in fields}
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Vehicle application already exists in this business.")
        return data


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "email", "address", "payment_terms", "notes", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, tenant, branch=None, **kw):
        super().__init__(*a, **kw)
        self.instance.tenant = tenant

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Supplier.objects.filter(tenant=self.instance.tenant, name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Supplier already exists in this business.")
        return name


class CrossRefForm(forms.ModelForm):
    class Meta:
        model = PartNumber
        fields = ["number", "kind", "note"]


class FitmentForm(forms.Form):
    vehicle = forms.ModelChoiceField(Vehicle.objects.all())

    def __init__(self, *a, tenant, **kw):
        super().__init__(*a, **kw)
        self.fields["vehicle"].queryset = Vehicle.objects.filter(tenant=tenant)


class CatalogueUploadForm(forms.Form):
    file = forms.FileField(label='Excel catalogue', widget=forms.ClearableFileInput(attrs={'accept': '.xlsx'}),
                           help_text='Excel .xlsx only, up to 5 MB and 5,000 part rows. Use the template below.')
    mode = forms.ChoiceField(label='Import mode', choices=[('create', 'Add new parts only'),
                             ('update', 'Add and update matching SKUs')], initial='create')

    def clean_file(self):
        upload = self.cleaned_data['file']
        if not upload.name.lower().endswith('.xlsx'):
            raise forms.ValidationError('Choose an Excel .xlsx file. Save older .xls files as .xlsx first.')
        if upload.size > 5 * 1024 * 1024:
            raise forms.ValidationError('The file exceeds the 5 MB upload limit.')
        return upload
