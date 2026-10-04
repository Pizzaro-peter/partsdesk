from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [('catalog', '0002_branchstock_alter_vehicle_unique_together_and_more'), ('core', '0003_backfill_tenants')]
    operations = [
        migrations.RemoveField(
            model_name='part',
            name='bin_location',
        ),
        migrations.RemoveField(
            model_name='part',
            name='quantity_on_hand',
        ),
        migrations.RemoveField(
            model_name='part',
            name='reorder_level',
        ),
        migrations.RemoveField(
            model_name='part',
            name='reorder_qty',
        ),
    ]
