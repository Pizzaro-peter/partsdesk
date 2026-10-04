from django.db import models, transaction


class TrackedModel(models.Model):
    """Keep an ordinary save and its signal-generated event in one transaction."""
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        with transaction.atomic(using=kwargs.get('using') or self._state.db or 'default'):
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic(using=kwargs.get('using') or self._state.db or 'default'):
            return super().delete(*args, **kwargs)
