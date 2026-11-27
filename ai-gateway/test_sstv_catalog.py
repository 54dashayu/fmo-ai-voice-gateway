import unittest
from unittest.mock import patch
import sstv_catalog as catalog
import audio_library

ITEM={'id':'abc-123','title':'test','mode':'Robot 36','duration':36.91,'wavBytes':3500000,
      'wavUrl':catalog.FILES+'audio/abc.wav','imageUrl':catalog.FILES+'images/abc.png'}


class CatalogTests(unittest.TestCase):
    def setUp(self):catalog._cache.clear()
    def test_list_cache_and_paging(self):
        with patch.object(catalog,'fetch',return_value={'items':[ITEM],'total':21}) as fetch:
            self.assertEqual(catalog.listing()['items'][0]['id'],'abc-123')
            catalog.listing();self.assertEqual(fetch.call_count,1)
            catalog.listing(20);self.assertEqual(fetch.call_count,2)
    def test_explicit_refresh_bypasses_cache(self):
        with patch.object(catalog,'fetch',return_value={'items':[ITEM],'total':1}) as fetch:
            catalog.listing();catalog.listing(refresh=True)
            self.assertEqual(fetch.call_count,2)
    def test_reject_untrusted_urls_and_ids(self):
        for changes in [{'wavUrl':'http://127.0.0.1/audio.wav'}, {'imageUrl':catalog.FILES+'images/../bad.png'}, {'wavUrl':catalog.FILES+'audio/abc.wav?token=x'}, {'id':'../../secret'}]:
            with self.assertRaises(audio_library.MediaError):catalog.clean({**ITEM,**changes})
    def test_import_preserves_stable_provenance(self):
        with patch.object(catalog,'fetch',return_value={'item':ITEM}),patch.object(catalog.media,'import_media',return_value={'id':'cached'}) as imp:
            asset=catalog.import_item('abc-123')
            imp.assert_called_once_with('url',ITEM['wavUrl'])
            self.assertEqual(asset['source_id'],'abc-123');self.assertEqual(asset['source'],'SSTV: test')
    def test_missing_hidden_or_invalid_item_fails(self):
        with patch.object(catalog,'fetch',return_value={'item':None}),self.assertRaises(audio_library.MediaError):catalog.import_item('abc-123')
        with self.assertRaises(audio_library.MediaError):catalog.listing(-1)

if __name__=='__main__':unittest.main()
