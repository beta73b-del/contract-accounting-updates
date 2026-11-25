import tempfile,json,unittest
from pathlib import Path
from io import BytesIO
from unittest.mock import patch
from preserve_excel_snapshot import snapshot
class Tests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.src=self.root/'Web'/'Приложение';(self.src/'data'/'letter-drafts').mkdir(parents=True);(self.src.parent/'Документы'/'f').mkdir(parents=True)
  for name in ('База_контрактов.xlsx','notes.json','competitors.json'):(self.src/'data'/name).write_bytes(b'fake')
  (self.src.parent/'Документы'/'f'/'a.txt').write_bytes(b'test')
  self.payload={'records':[{'id':'test001','contractNumber':'0001'}],'stock':{'events':[]},'finance':{'rates':[]},'notes':{},'owner':'QA'}
  self.links=[{'id':'test001','path':'docs:f'}]
 def tearDown(self):self.t.cleanup()
 def run_snapshot(self):
  (self.src/'data'/'contract-folders.json').write_text(json.dumps({'entries':self.links}),encoding='utf8')
  with patch('preserve_excel_snapshot.urlopen',return_value=BytesIO(json.dumps(self.payload).encode())):
   return snapshot(self.src,self.root/'result')
 def test_good(self):
  x=self.run_snapshot();self.assertEqual((x['contracts'],x['documentsCopied'],x['status']),(1,1,'SOURCE_COPY_VERIFIED'))
 def test_missing(self):
  self.links[0]['path']='docs:missing';self.assertEqual(self.run_snapshot()['status'],'PARTIAL')
 def test_traversal(self):
  self.links[0]['path']='docs:../../outside'
  with self.assertRaises(ValueError):self.run_snapshot()
 def test_duplicate(self):
  self.payload['records'].append(self.payload['records'][0])
  with self.assertRaises(ValueError):self.run_snapshot()
if __name__=='__main__':unittest.main()
