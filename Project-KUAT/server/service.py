"""K.U.A.T cloud workspace service. Python standard library; reverse-proxy HTTPS required."""
import json, os, shutil, sqlite3, time
from accounts import Accounts, AuthError, RateLimited, InputError
from permissions import RANKS, BY_ID, permissions
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import unquote

DOCUMENTS = {'world', 'entries', 'timeline', 'state', 'archive'}
LIMIT = 25 * 1024 * 1024
UPDATE_ROOT = Path(os.environ.get('KUAT_UPDATE_DIR', '/var/lib/kuat/updates'))


class Conflict(Exception): pass
class Store:
    def __init__(self, file):
        self.file = str(file)
        Path(file).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents(name TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL, actor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, name TEXT, revision INTEGER, body TEXT, updated REAL, actor TEXT);
            CREATE TABLE IF NOT EXISTS members(principal TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('editor','reader')));
            ''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.file, timeout=15)
        try:
            with db: yield db
        finally: db.close()
    def all(self):
        with self.connect() as db:
            return {name: {'revision': rev, 'data': json.loads(body)} for name, rev, body in db.execute('SELECT name,revision,body FROM documents')}
    def save(self, name, data, revision, actor):
        if name not in DOCUMENTS or not isinstance(data, dict) or type(revision) is not int or revision < 0:
            raise ValueError('档案或版本格式无效')
        if name == 'entries' and not isinstance(data.get('entries'), list): raise ValueError('缺少词条列表')
        if name == 'timeline' and not isinstance(data.get('events'), list): raise ValueError('缺少时间线列表')
        if name == 'archive' and not isinstance(data.get('documents'), list): raise ValueError('缺少档案列表')
        body = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(body.encode()) > LIMIT: raise ValueError('档案过大')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT revision,body,updated,actor FROM documents WHERE name=?', (name,)).fetchone()
            if (old[0] if old else 0) != revision: raise Conflict('其他成员已更新此档案，请重新载入云端数据后再编辑')
            if old: db.execute('INSERT INTO history(name,revision,body,updated,actor) VALUES(?,?,?,?,?)', (name, *old))
            db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)', (name, revision+1, body, time.time(), actor))
        return {'revision': revision+1, 'data': data}

def make_server(store, address=('127.0.0.1', 8910), accounts=None):
    accounts=accounts or Accounts(store,os.environ.get('KUAT_BOOTSTRAP_FILE','/var/lib/kuat/bootstrap.key'))
    class Handler(BaseHTTPRequestHandler):
        server_version = 'KUAT'
        def log_message(self, *_): pass  # Never log authorization or document bodies.
        def reply(self, code, value):
            raw=json.dumps(value, ensure_ascii=False).encode();self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def reply_file(self, file, content_type='application/octet-stream'):
            self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(file.stat().st_size));self.end_headers()
            with file.open('rb') as source: shutil.copyfileobj(source,self.wfile)
        def body(self):
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>LIMIT: raise ValueError('请求大小无效')
            return json.loads(self.rfile.read(size))
        def run_request(self):
            path=self.path.split('?')[0].removeprefix('/kuat-api')
            if path=='/health' and self.command=='GET':return self.reply(200,{'status':'ok','service':'kuat'})
            # 更新清单和安装包允许未登录读取；客户端下载后还会校验清单中的 SHA-256。
            if path=='/update/latest.json' and self.command=='GET':
                manifest=UPDATE_ROOT/'latest.json'
                if not manifest.is_file():return self.reply(404,{'error':'暂无可用更新'})
                try:return self.reply(200,json.loads(manifest.read_text(encoding='utf-8-sig')))
                except (OSError,ValueError):return self.reply(503,{'error':'更新清单暂不可用'})
            if path.startswith('/update/') and self.command=='GET':
                filename=unquote(path.rsplit('/',1)[1])
                if not filename or filename in {'.','..'} or Path(filename).name!=filename:return self.reply(404,{'error':'更新文件不存在'})
                file=UPDATE_ROOT/filename
                if not file.is_file():return self.reply(404,{'error':'更新文件不存在'})
                return self.reply_file(file)
            device=self.headers.get('X-Device-ID','')[:100]
            if path=='/setup' and self.command=='POST':
                body=self.body();accounts.setup(body.get('code'),body.get('username'),body.get('password'))
                return self.reply(200,{'ok':True})
            if path=='/login' and self.command=='POST':
                body=self.body();return self.reply(200,accounts.login(body.get('username'),body.get('password'),device))
            scheme,_,token=self.headers.get('Authorization','').partition(' ')
            if scheme!='Bearer' or not token:return self.reply(401,{'error':'请先登录'})
            if path=='/logout' and self.command=='POST':
                accounts.logout(token,device);return self.reply(200,{'ok':True})
            principal=accounts.authenticate(token,device);role=principal['role'];caps=permissions(principal)
            if path=='/profile':
                if self.command=='GET':return self.reply(200,accounts.profile(principal['principal_id']))
                if self.command=='PUT':
                    body=self.body();return self.reply(200,accounts.update_profile(principal['principal_id'],body.get('nickname'),body.get('signature'),body.get('avatar')))
            if path=='/password' and self.command=='POST':
                body=self.body();accounts.change_password(principal['principal_id'],body.get('old'),body.get('password'));return self.reply(200,{'ok':True})
            if path=='/accounts':
                if role!='owner':return self.reply(403,{'error':'仅管理员可以发放账号'})
                if self.command=='GET':return self.reply(200,{'accounts':accounts.list()})
                body=self.body()
                if self.command=='POST':
                    if body.get('role') not in (*BY_ID,'editor','reader'):raise ValueError('请选择有效的K.U.A.T级别')
                    return self.reply(200,{'id':accounts.create(body.get('username'),body.get('password'),body.get('role'))})
                if self.command=='PUT':
                    accounts.update(body.get('id'),body.get('active'),body.get('password'),body.get('rank'));return self.reply(200,{'ok':True})
            if path=='/workspace' and self.command=='GET':return self.reply(200,{'documents':store.all(),'role':role,'rank':principal['rank'],'username':principal['username'],'permissions':caps,'ranks':RANKS})
            if path.startswith('/documents/') and self.command=='PUT':
                if path.rsplit('/',1)[1] not in caps['write']:return self.reply(403,{'error':'当前K.U.A.T级别无权修改此模块'})
                name=path.rsplit('/',1)[1];body=self.body()
                return self.reply(200,store.save(name,body.get('data'),body.get('revision'),principal['principal_id']))
            return self.reply(404,{'error':'接口不存在'})
        def handle_request(self):
            try:self.run_request()
            except InputError as e:self.reply(400,{'error':str(e)})
            except RateLimited as e:self.reply(429,{'error':str(e)})
            except AuthError as e:self.reply(401,{'error':str(e)})
            except sqlite3.IntegrityError:self.reply(400,{'error':'账号名称已存在'})
            except Conflict as e:self.reply(409,{'error':str(e)})
            except (ValueError,KeyError,TypeError):self.reply(400,{'error':'提交的数据格式不正确'})
            except Exception:self.reply(503,{'error':'云端服务暂不可用，数据未保存'})
        do_GET=handle_request;do_POST=handle_request;do_PUT=handle_request
    return ThreadingHTTPServer(address,Handler)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--init');args=parser.parse_args()
    store=Store(os.environ.get('KUAT_DB','/var/lib/kuat/workspace.sqlite3'))
    if args.init:
        bundle=json.loads(Path(args.init).read_text(encoding='utf-8-sig'))
        if store.all():raise SystemExit('Refusing to overwrite existing cloud workspace')
        for name,data in bundle.items():store.save(name,data,0,'initial-migration')
        print('Initialized:', ', '.join(bundle))
    else:make_server(store).serve_forever()
