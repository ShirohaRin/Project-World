import importlib.util, json, sys, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'server'))
import service as m
from accounts import Accounts, AuthError
class CloudTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=m.Store(Path(self.tmp.name)/'data.sqlite');self.key=Path(self.tmp.name)/'bootstrap';self.key.write_text('one-time-secret')
  self.accounts=Accounts(self.store,self.key);self.accounts.setup('one-time-secret','owner','OwnerPassword123!');self.reader=self.accounts.create('reader','ReaderPassword123!','reader')
  self.tokens={name:self.accounts.login(name,password,'test-device')['access_token'] for name,password in [('owner','OwnerPassword123!'),('reader','ReaderPassword123!')]}
  self.server=m.make_server(self.store,('127.0.0.1',0),self.accounts);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.url='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup()
 def request(self,path,token='',data=None,method=None):
  r=urllib.request.Request(self.url+path,headers={'Authorization':'Bearer '+self.tokens.get(token,token),'X-Device-ID':'test-device','Content-Type':'application/json'},data=json.dumps(data).encode() if data is not None else None,method=method or ('PUT' if data is not None else 'GET'))
  try:
   with urllib.request.urlopen(r) as res:return res.status,json.load(res)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def test_authorization_and_conflict(self):
  self.assertEqual(self.request('/workspace')[0],401);self.assertEqual(self.request('/workspace','old-idea-token')[0],401)
  body={'data':{'name':'world'},'revision':0};self.assertEqual(self.request('/documents/world','reader',body)[0],403)
  self.assertEqual(self.request('/documents/world','owner',body)[0],200);self.assertEqual(self.request('/documents/world','owner',body)[0],409)
 def test_no_registration_or_second_owner(self):
  self.assertEqual(self.request('/register','',{'username':'any'},'POST')[0],401)
  self.assertEqual(self.request('/setup','',{'code':'one-time-secret','username':'hacker','password':'Password12345!'},'POST')[0],401)
  self.assertEqual(self.request('/accounts','reader',{'username':'new','password':'Password12345!','role':'editor'},'POST')[0],403)
  self.assertEqual(self.request('/accounts','owner',{'username':'new','password':'Password12345!','role':'owner'},'POST')[0],400)
  self.assertFalse(self.key.exists())
 def test_owner_issues_and_revokes(self):
  self.assertEqual(self.request('/accounts','owner',{'username':'new','password':'Password12345!','role':'editor'},'POST')[0],200)
  self.assertEqual(self.request('/login','',{'username':'new','password':'Password12345!'},'POST')[0],200)
  self.accounts.update(self.reader,False);self.assertEqual(self.request('/workspace','reader')[0],401)
 def test_passwords_and_device_binding(self):
  with self.store.connect() as db:hashed=db.execute("SELECT password FROM kuat_accounts WHERE username='owner'").fetchone()[0]
  self.assertNotIn('OwnerPassword',hashed)
  with self.assertRaises(AuthError):self.accounts.authenticate(self.tokens['owner'],'wrong-device')
  with self.assertRaises(AuthError):self.accounts.login('owner','bad','test-device')
  self.accounts.change_password(self.reader,'ReaderPassword123!','NewPassword123!');self.assertEqual(self.request('/workspace','reader')[0],401)
 def test_unrestricted_password_lengths(self):
  identity=self.accounts.create('short','1','editor');self.assertIn('access_token',self.accounts.login('short','1','test-device'))
  self.accounts.update(identity,password='长'*300);self.assertIn('access_token',self.accounts.login('short','长'*300,'test-device'))
  self.accounts.change_password(identity,'长'*300,'x');self.assertIn('access_token',self.accounts.login('short','x','test-device'))
  with self.assertRaises(ValueError):self.accounts.create('empty','','reader')
 def test_uppercase_and_trimmed_username(self):
  with tempfile.TemporaryDirectory() as folder:
   key=Path(folder)/'key';key.write_text('setup-code');a=Accounts(m.Store(Path(folder)/'test.sqlite'),key)
   a.setup('setup-code','  MixedCASE  ','A');self.assertIn('access_token',a.login('MixedCASE','A','test'))
  code,result=self.request('/accounts','owner',{'username':'中 文','password':'A','role':'reader'},'POST')
  self.assertEqual(code,400);self.assertIn('账号需',result['error']);self.assertNotIn('密码不能为空',result['error'])
 def test_rank_permissions_and_issuer_separation(self):
  from permissions import RANKS
  for rank in RANKS:
   name='rank-'+rank['id'].lower();identity=self.accounts.create(name,'A',rank['id']);token=self.accounts.login(name,'A','test-device')['access_token']
   result=self.request('/workspace',token)[1];self.assertEqual(result['rank'],rank['id']);self.assertEqual(result['permissions']['write'],rank['write'])
   self.assertEqual(self.request('/accounts',token)[0],403)
   code,_=self.request('/documents/world',token,{'revision':0,'data':{'name':'test'}})
   self.assertIn(code,(200,409) if 'world' in rank['write'] else (403,))
  self.accounts.update(self.reader,rank='Blue');self.assertEqual(self.request('/workspace','reader')[0],401)
 def test_remembered_session_expiry_and_logout(self):
  import time
  result=self.accounts.login('owner','OwnerPassword123!','test-device');token=result['access_token']
  self.assertAlmostEqual(result['expires_at']-time.time(),30*24*3600,delta=5)
  restored=Accounts(m.Store(self.store.file),self.key)
  self.assertEqual(restored.authenticate(token,'test-device')['role'],'owner')
  self.assertEqual(self.request('/logout',token,{},'POST')[0],200)
  self.assertEqual(self.request('/workspace',token)[0],401)
  self.assertEqual(self.request('/workspace','owner')[0],200)
  with self.store.connect() as db:db.execute('UPDATE kuat_sessions SET expires=0')
  self.assertEqual(self.request('/workspace','owner')[0],401)
 def test_history_persistence(self):
  self.store.save('world',{'name':'first'},0,'owner');self.store.save('world',{'name':'second'},1,'owner');self.assertEqual(m.Store(self.store.file).all()['world']['revision'],2)
if __name__=='__main__':unittest.main()
