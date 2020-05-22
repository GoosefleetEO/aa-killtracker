from unittest.mock import patch, Mock

from bravado.exception import HTTPNotFound

from ..models import EveEntity
from ..utils import NoSocketsTestCase, set_test_logger

MODULE_PATH = 'killtracker.managers'
logger = set_test_logger(MODULE_PATH, __file__)

EVE_ENTITIES = {
    1001: {
        'id': 1001,
        'name': 'Bruce Wayne',
        'category': 'character'
    },
    1002: {
        'id': 1002,
        'name': 'Peter Parker',
        'category': 'character'
    },
    1101: {
        'id': 1101,
        'name': 'Wayne Technologies',
        'category': 'corporation'
    }
}


def my_esi_fetch(*args, **kwargs):
    result = []
    if args[0] == 'Universe.post_universe_names':
        for id in kwargs['args']['ids']:
            if id in EVE_ENTITIES:
                result.append(EVE_ENTITIES[id])
            else:                
                raise HTTPNotFound(Mock(**{'status_code': 404}))
    return result


@patch(MODULE_PATH + '.esi_fetch')
class TestEveEntityQuerySet(NoSocketsTestCase):

    def setUp(self):
        EveEntity.objects.all().delete()
        self.e1 = EveEntity.objects.create(id=1001)
        self.e2 = EveEntity.objects.create(id=1002)
        self.e3 = EveEntity.objects.create(id=1101)
    
    def test_can_update_one(self, mock_esi_fetch):
        mock_esi_fetch.side_effect = my_esi_fetch        
        entities = EveEntity.objects.filter(id=1001)
        
        result = entities.update_from_esi()
        self.e1.refresh_from_db()
        self.assertEqual(result, 1)
        self.assertEqual(self.e1.name, 'Bruce Wayne')
        self.assertEqual(self.e1.category, EveEntity.CATEGORY_CHARACTER)
    
    def test_can_update_many(self, mock_esi_fetch):
        mock_esi_fetch.side_effect = my_esi_fetch        
        entities = EveEntity.objects.filter(id__in=[1001, 1002, 1101])
        
        result = entities.update_from_esi()
        self.assertEqual(result, 3)

        self.e1.refresh_from_db()        
        self.assertEqual(self.e1.name, 'Bruce Wayne')
        self.assertEqual(self.e1.category, EveEntity.CATEGORY_CHARACTER)

        self.e2.refresh_from_db()        
        self.assertEqual(self.e2.name, 'Peter Parker')
        self.assertEqual(self.e2.category, EveEntity.CATEGORY_CHARACTER)

        self.e3.refresh_from_db()        
        self.assertEqual(self.e3.name, 'Wayne Technologies')
        self.assertEqual(self.e3.category, EveEntity.CATEGORY_CORPORATION)

    def test_can_divide_and_conquer(self, mock_esi_fetch):
        mock_esi_fetch.side_effect = my_esi_fetch        
        EveEntity.objects.create(id=9999)
        entities = EveEntity.objects.filter(id__in=[1001, 1002, 1101, 9999])
        
        result = entities.update_from_esi()
        self.assertEqual(result, 3)

        self.e1.refresh_from_db()        
        self.assertEqual(self.e1.name, 'Bruce Wayne')
        self.assertEqual(self.e1.category, EveEntity.CATEGORY_CHARACTER)

        self.e2.refresh_from_db()        
        self.assertEqual(self.e2.name, 'Peter Parker')
        self.assertEqual(self.e2.category, EveEntity.CATEGORY_CHARACTER)

        self.e3.refresh_from_db()        
        self.assertEqual(self.e3.name, 'Wayne Technologies')
        self.assertEqual(self.e3.category, EveEntity.CATEGORY_CORPORATION)
