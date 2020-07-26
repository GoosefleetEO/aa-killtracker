from django.test import TestCase

from eveuniverse.tools.testdata import create_testdata, ModelSpec

from . import test_data_filename


class CreateEveUniverseTestData(TestCase):
    def test_create_testdata(self):
        testdata_spec = {
            "EveFaction": ModelSpec(ids=[500001], include_children=False),
            "EveType": ModelSpec(
                ids=[603, 2488, 2977, 3756, 34562], include_children=False
            ),
            "EveSolarSystem": ModelSpec(
                ids=[30001161, 30004976, 30004984, 30045349, 31000005],
                include_children=False,
            ),
            "EveRegion": ModelSpec(ids=[10000038], include_children=True,),
        }
        create_testdata(testdata_spec, test_data_filename())
