import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import gallery
import service

class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'gallery'; self.root.mkdir()
        self.env = patch.dict(os.environ, FMO_GALLERY_DIR=str(self.root), FMO_KB_ADMIN_TOKEN='test-gallery-token')
        self.env.start(); self.addCleanup(self.env.stop)
        (self.root/'a').mkdir(); (self.root/'a'/'nested').mkdir()
        (self.root/'a'/'one.jpg').write_bytes(b'one')
        (self.root/'a'/'nested'/'two.png').write_bytes(b'two')

    def test_tree_recursive_and_dynamic(self):
        tree = gallery.directory_list()['directories']
        root = tree[0]; a = next(x for x in tree if x['path']=='a')
        self.assertEqual(root['recursive_count'],2); self.assertEqual(a['count'],1)
        self.assertEqual(len(gallery.image_list(a['id'])['images']),1)
        self.assertEqual(len(gallery.image_list(a['id'],True)['images']),2)
        (self.root/'a'/'new.webp').write_bytes(b'new')
        self.assertEqual(len(gallery.image_list(a['id'],True)['images']),3)
        self.assertNotIn(str(self.root),str(tree))

    def test_symlinks_and_limits_and_disabled(self):
        outside = Path(self.tmp.name)/'outside.jpg';outside.write_bytes(b'private')
        (self.root/'linked.jpg').symlink_to(outside)
        (self.root/'linked-dir').symlink_to(self.root/'a',target_is_directory=True)
        (self.root/'zero.jpg').touch(); (self.root/'bad.txt').write_bytes(b'bad')
        _, dirs, images = gallery.scan(); self.assertEqual(len(images),2);self.assertEqual(len(dirs),3)
        with patch.object(gallery,'MAX_ENTRIES',1),self.assertRaises(ValueError):gallery.scan()
        with patch.dict(os.environ,FMO_GALLERY_DIR=''),self.assertRaises(ValueError):gallery.scan()
        with self.assertRaises(ValueError):gallery.image_list('../a')
        with self.assertRaises(ValueError):gallery.read_image(str(outside))

    def test_synology_system_folders_are_excluded(self):
        for name in ('@eaDir', '@tmp', '#recycle', '.hidden'):
            folder=self.root/name;folder.mkdir()
            (folder/'thumbnail.jpg').write_bytes(b'thumbnail')
        nested=self.root/'a'/'@eaDir';nested.mkdir()
        (nested/'thumbnail.jpg').write_bytes(b'thumbnail')
        _, directories, images=gallery.scan()
        self.assertEqual(len(directories),3)
        self.assertEqual(len(images),2)
        self.assertFalse(any('@eaDir' in item['path'] for item in directories.values()))

    def test_read_and_swap_protection(self):
        iid=gallery.identifier('image','a/one.jpg')
        self.assertEqual(gallery.read_image(iid),(b'one','image/jpeg'))
        indexed=gallery.scan()
        source=self.root/'a'/'one.jpg';source.unlink(); source.symlink_to(Path(self.tmp.name)/'secret.jpg')
        (Path(self.tmp.name)/'secret.jpg').write_bytes(b'secret')
        with patch.object(gallery,'scan',return_value=indexed),self.assertRaises(OSError):gallery.read_image(iid)

    def test_routes_require_auth(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),service.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            for route in ['/v1/gallery/directories','/v1/gallery/images','/v1/gallery/image']:
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+route)
                self.assertEqual(error.exception.code,401)
            req=urllib.request.Request(base+'/v1/gallery/directories',headers={'Authorization':'Bearer test-gallery-token'})
            self.assertEqual(urllib.request.urlopen(req).status,200)
        finally:
            server.shutdown();server.server_close();thread.join()
