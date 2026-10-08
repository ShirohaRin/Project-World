"""Local KUAT identities. No public registration and no IDEA dependencies."""
import hashlib,hmac,secrets,time,re
from pathlib import Path
from permissions import BY_ID
class InputError(ValueError): pass
class AuthError(Exception): pass
class RateLimited(AuthError): pass
class Accounts:
 def __init__(self,store,bootstrap_file):
  self.store=store;self.bootstrap_file=Path(bootstrap_file)
  with store.connect() as db:
   db.executescript('''
   CREATE TABLE IF NOT EXISTS kuat_accounts(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL);
   CREATE TABLE IF NOT EXISTS kuat_sessions(token TEXT PRIMARY KEY,account TEXT NOT NULL,device TEXT NOT NULL,expires REAL NOT NULL);
   CREATE TABLE IF NOT EXISTS kuat_login_attempts(username TEXT NOT NULL,at REAL NOT NULL);
  ''')
   cols=[r[1] for r in db.execute('PRAGMA table_info(kuat_accounts)')]
   for col in ('nickname','signature','avatar'):
    if col not in cols: db.execute(f"ALTER TABLE kuat_accounts ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
   if 'rank' not in [r[1] for r in db.execute('PRAGMA table_info(kuat_accounts)')]:
    db.execute("ALTER TABLE kuat_accounts ADD COLUMN rank TEXT NOT NULL DEFAULT 'Grey'")
    db.execute("UPDATE kuat_accounts SET rank=CASE role WHEN 'owner' THEN 'White' WHEN 'editor' THEN 'Blue' ELSE 'Grey' END")
 @staticmethod
 def password_hash(password,salt=None):
  if not isinstance(password,str) or not password:raise InputError('密码不能为空')
  salt=salt or secrets.token_hex(16)
  digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
  return salt+':'+digest
 @staticmethod
 def token_hash(token):return hashlib.sha256(token.encode()).hexdigest()
 def create(self,username,password,role):
  username=username.strip() if isinstance(username,str) else username
  if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{3,80}',username):raise InputError('账号需3到80位英文字母（支持大小写）、数字或 _ . @ -，不支持中文和中间空格')
  rank={'owner':'White','editor':'Blue','reader':'Grey'}.get(role,role)
  if rank not in BY_ID:raise InputError('级别无效')
  account_role='owner' if role=='owner' else ('editor' if BY_ID[rank]['write'] else 'reader')
  hashed=self.password_hash(password);identity='kuat-'+secrets.token_hex(16)
  with self.store.connect() as db:db.execute('INSERT INTO kuat_accounts(id,username,password,role,active,created,rank) VALUES(?,?,?,?,1,?,?)',(identity,username.lower(),hashed,account_role,time.time(),rank))
  return identity
 def setup(self,code,username,password):
  username=username.strip() if isinstance(username,str) else username
  if not isinstance(code,str) or not self.bootstrap_file.exists():raise AuthError('管理员初始化已关闭')
  if not hmac.compare_digest(code.strip(),self.bootstrap_file.read_text().strip()):raise AuthError('初始化授权无效')
  # Serialize first-owner creation; setup cannot create a second owner.
  hashed=self.password_hash(password)
  if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{3,80}',username):raise InputError('账号需3到80位英文字母（支持大小写）、数字或 _ . @ -，不支持中文和中间空格')
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE')
   if db.execute('SELECT count(*) FROM kuat_accounts').fetchone()[0]:raise AuthError('管理员已初始化')
   db.execute('INSERT INTO kuat_accounts(id,username,password,role,active,created,rank) VALUES(?,?,?,?,1,?,?)',('kuat-'+secrets.token_hex(16),username.lower(),hashed,'owner',time.time(),'White'))
  self.bootstrap_file.unlink(missing_ok=True)
 def login(self,username,password,device):
  if not isinstance(username,str) or not isinstance(password,str) or not device:raise AuthError('账号或密码错误')
  username=username.lower().strip()[:80];now=time.time()
  with self.store.connect() as db:
   db.execute('DELETE FROM kuat_login_attempts WHERE at<?',(now-900,))
   if db.execute('SELECT count(*) FROM kuat_login_attempts WHERE username=?',(username,)).fetchone()[0]>=8:raise RateLimited('尝试过于频繁，请15分钟后重试')
   row=db.execute('SELECT id,password,role,active FROM kuat_accounts WHERE username=?',(username,)).fetchone()
   db.execute('INSERT INTO kuat_login_attempts VALUES(?,?)',(username,now))
  try:valid=row and row[3] and hmac.compare_digest(row[1],self.password_hash(password,row[1].split(':')[0]))
  except ValueError:valid=False
  if not valid:raise AuthError('账号或密码错误')
  token=secrets.token_urlsafe(48)
  with self.store.connect() as db:
   db.execute('DELETE FROM kuat_login_attempts WHERE username=?',(username,))
   db.execute('DELETE FROM kuat_sessions WHERE expires<?',(now,))
   db.execute('INSERT INTO kuat_sessions VALUES(?,?,?,?)',(self.token_hash(token),row[0],device,now+30*24*3600))
  return {'access_token':token,'expires_at':now+30*24*3600,'principal':{'principal_id':row[0],'role':row[2]}}
 def logout(self,token,device):
  with self.store.connect() as db:db.execute('DELETE FROM kuat_sessions WHERE token=? AND device=?',(self.token_hash(token),device))
 def authenticate(self,token,device):
  with self.store.connect() as db:row=db.execute('SELECT a.id,a.role,a.username,a.rank FROM kuat_sessions s JOIN kuat_accounts a ON a.id=s.account WHERE s.token=? AND s.device=? AND s.expires>? AND a.active=1',(self.token_hash(token),device,time.time())).fetchone()
  if not row:raise AuthError('登录已过期，请重新登录')
  return {'principal_id':row[0],'role':row[1],'username':row[2],'rank':row[3]}
 def profile(self,identity):
  with self.store.connect() as db:
   row=db.execute('SELECT username,nickname,signature,avatar,rank FROM kuat_accounts WHERE id=?',(identity,)).fetchone()
  if not row:raise AuthError('账号不存在')
  return {'username':row[0],'nickname':row[1],'signature':row[2],'avatar':row[3],'rank':row[4]}
 def update_profile(self,identity,nickname,signature,avatar):
  values=(nickname or '',signature or '',avatar or '')
  if any(not isinstance(x,str) or len(x)>200 for x in values):raise InputError('个人资料长度无效')
  with self.store.connect() as db:db.execute('UPDATE kuat_accounts SET nickname=?,signature=?,avatar=? WHERE id=?',(*values,identity))
  return self.profile(identity)
 def list(self):
  with self.store.connect() as db:return [dict(zip(('id','username','role','active','rank'),row)) for row in db.execute('SELECT id,username,role,active,rank FROM kuat_accounts ORDER BY created')]
 def update(self,identity,active=None,password=None,rank=None):
  with self.store.connect() as db:
   row=db.execute('SELECT role FROM kuat_accounts WHERE id=?',(identity,)).fetchone()
   if not row or row[0]=='owner':raise InputError('此操作仅用于已发放的成员账号')
   if rank is not None:
    if rank not in BY_ID:raise InputError('级别无效')
    db.execute('UPDATE kuat_accounts SET rank=?,role=? WHERE id=?',(rank,'editor' if BY_ID[rank]['write'] else 'reader',identity))
   if active is not None:
    if type(active) is not bool:raise InputError('状态无效')
    db.execute('UPDATE kuat_accounts SET active=? WHERE id=?',(int(active),identity))
   if password is not None:db.execute('UPDATE kuat_accounts SET password=? WHERE id=?',(self.password_hash(password),identity))
   db.execute('DELETE FROM kuat_sessions WHERE account=?',(identity,))
 def change_password(self,identity,old,new):
  with self.store.connect() as db:
   row=db.execute('SELECT password FROM kuat_accounts WHERE id=?',(identity,)).fetchone()
   if not row or not hmac.compare_digest(row[0],self.password_hash(old,row[0].split(':')[0])):raise AuthError('当前密码不正确')
   db.execute('UPDATE kuat_accounts SET password=? WHERE id=?',(self.password_hash(new),identity));db.execute('DELETE FROM kuat_sessions WHERE account=?',(identity,))
