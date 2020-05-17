import logging
from django.db import models
from esi.models import esi_client_factory


_client = None
logger = logging.getLogger(__name__)


class EveEntityManager(models.Manager):

    @staticmethod
    def _get_client():
        global _client
        if _client is None:
            logger.info('Loading ESI client...')
            _client = esi_client_factory()
        return _client
    
    def fetch_entities(self, ids: set):
        
        ids = {int(x) for x in ids}
        ids_found = self.filter(id__in=ids).values_list(flat=True)
        if ids_found:
            ids_found = set(ids_found)
        else:
            ids_found = set()
        
        ids_not_found = ids - ids_found
        if ids_not_found:
            client = self._get_client()
            results = \
                client.Universe.post_universe_names(ids=list(ids_not_found)).result()
            for item in results:
                self.update_or_create(
                    id=item['id'],
                    defaults={
                        'name': item['name'],
                        'category': item['category']
                    }
                )
        return self.filter(id__in=ids)
