"""Original UI with SQLite compatibility API. Local-only, experimental until acceptance."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, unquote, urlencode
import secrets, mimetypes, webbrowser, threading, traceback, hashlib, logging, signal, time, re
from contextlib import closing
from urllib.request import urlopen, Request
from urllib.error import URLError
import math
import auxiliary as a
from auxiliary import *
PORT=int(os.environ.get("CONTRACTSIP_PORT", "8877")); BUILD="sqlite-original-ui-20261127-build-026"; NONCE=secrets.token_hex(32); INSTANCE=secrets.token_hex(16); LOCK=threading.RLock()

# Competitor products are independent of stock; legacy model identifiers stay valid.
COMPETITOR_PRODUCT_NAMES={'lite':'Рутокен Lite 1010','ecp':'Рутокен ЭЦП 3120'}
COMPETITOR_PRODUCT_ALIASES={'рутокен lite 1010':'lite','рутокен 1010':'lite',
    'рутокен эцп 3120':'ecp','рутокен эцп 3.0 3120':'ecp','рутокен 3120':'ecp'}
def competitor_product(entry,rows):
    if 'productName' not in entry:
        model=entry.get('model');require(isinstance(model,str) and model in COMPETITOR_PRODUCT_NAMES,'Укажите наименование товара')
        return model,COMPETITOR_PRODUCT_NAMES[model]
    raw=entry['productName'];require(isinstance(raw,str),'Укажите наименование товара')
    require(not any(unicodedata.category(ch) in ('Cc','Cf','Cs') for ch in raw),'Недопустимые символы в наименовании товара')
    name=unicodedata.normalize('NFC',' '.join(raw.split()));require(1<=len(name)<=200,'Наименование товара: от 1 до 200 символов')
    key=name.lower();model=COMPETITOR_PRODUCT_ALIASES.get(key,'custom:'+key)
    if model in COMPETITOR_PRODUCT_NAMES:return model,COMPETITOR_PRODUCT_NAMES[model]
    # Reuse the existing spelling for the same normalized product.
    previous=next((x.get('productName') for x in rows if x.get('model')==model and x.get('productName')),None)
    return model,previous or name

def competitors(c,b):
    doc=snapshot(c,'competitors');rows=doc['entries'];action=b.get('action');require(action in ('add','edit','delete'),'Неизвестное действие');old=next((r for r in rows if r['id']==b.get('id')),None)
    if action=='add':
        request=s(b.get('requestId')).lower();require(re.fullmatch('[a-f0-9-]{36}',request),'Некорректный ID сохранения');existing=next((r for r in rows if r.get('requestId')==request),None)
        if existing:return dict(result=existing,entries=rows)
        old=dict(id=uid(),version=0,requestId=request,created=now());rows.append(old)
    else:require(old and s(old['version'])==s(b.get('version')),'Запись уже изменена или не найдена')
    if action=='delete':require(b.get('confirmed') is True,'Удаление требует подтверждения');rows.remove(old);result={'deleted':old['id']}
    else:
        r=b['entry'];model,product_name=competitor_product(r,rows);dt=valid_date(r.get('date'),True);require(dt<=today(),'Дата процедуры позже сегодняшней');inn=txt(r.get('inn'),12,one=True);require(not inn or re.fullmatch(r'\d{10}|\d{12}',inn),'ИНН должен содержать 10 или 12 цифр');price=money(s(r.get('price')).replace(',','.'),True);require(0<price<=900000000,'Цена вне диапазона');old.update(version=old['version']+1,model=model,productName=product_name,date=dt,platform=txt(r.get('platform'),120,True,True),winner=txt(r.get('winner'),200,True,True),inn=inn,quantity=qty(r.get('quantity'),1),priceKopecks=int(price*100),updated=now());result=old
    put(c,'competitors',doc);return dict(result=result,entries=rows)


LAUNCH_DEFAULTS=dict(browser='system',browserPath='',autostart=False,autoOpen=True,background=False,fixedPort=0)
BROWSERS={
 'chrome':('chrome.exe',('Google/Chrome/Application/chrome.exe',)),
 'edge':('msedge.exe',('Microsoft/Edge/Application/msedge.exe',)),
 'firefox':('firefox.exe',('Mozilla Firefox/firefox.exe',)),
 'gost':('chromium-gost.exe',('Chromium-Gost/Application/chrome.exe','Chromium-Gost/Application/chromium-gost.exe','Chromium-Gost/chrome.exe','Chromium-Gost/chromium-gost.exe','ChromiumGost/Application/chrome.exe')),
 'yandex':('browser.exe',('Yandex/YandexBrowser/Application/browser.exe',))}
RUN_NAME='ContractsIP.SQLite'
def launch_settings(raw=None):
    if raw is None:
        p=ROOT/'launch-settings.json'
        raw=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}
    require(isinstance(raw,dict),'Некорректные настройки запуска')
    out={**LAUNCH_DEFAULTS,**{k:raw[k] for k in LAUNCH_DEFAULTS if k in raw}}
    require(out['browser'] in ('system','custom',*BROWSERS),'Неизвестный браузер')
    require(type(out['browserPath']) is str and len(out['browserPath'])<=2048,'Некорректный путь браузера')
    for k in ('autostart','autoOpen','background'):require(type(out[k]) is bool,'Некорректный параметр '+k)
    require(type(out['fixedPort']) is int and out['fixedPort'] in (0,*range(8877,8887)),'Порт: 8877–8886 или автоматический выбор')
    return out

def detect_browsers():
    found={}
    if sys.platform!='win32':return found
    import winreg
    for key,(exe,relative) in BROWSERS.items():
        candidates=[]
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for flags in (0,winreg.KEY_WOW64_32KEY,winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths'+'\\'+exe,0,winreg.KEY_READ|flags) as h:candidates.append(Path(winreg.QueryValue(h,None).strip('"')))
                except OSError:pass
        for name in ('LOCALAPPDATA','ProgramFiles','ProgramFiles(x86)'):
            if os.environ.get(name):candidates.extend(Path(os.environ[name])/r for r in relative)
        for p in candidates:
            if p.is_absolute() and p.is_file() and p.suffix.lower()=='.exe':found[key]=str(p);break
    return found

def apply_autostart(cfg):
    if sys.platform!='win32':
        require(not cfg['autostart'],'Автозапуск доступен только в Windows');return
    import winreg
    path=r'Software\Microsoft\Windows\CurrentVersion\Run'
    exe=ROOT/'ContractsIP.exe'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,path,0,winreg.KEY_READ|winreg.KEY_WRITE) as h:
        if cfg['autostart']:
            require(exe.is_file(),'Не найден ContractsIP.exe')
            winreg.SetValueEx(h,RUN_NAME,0,winreg.REG_SZ,'"'+str(exe)+'" --autostart')
        else:
            try:
                # Do not remove another installed copy's registration.
                value,_=winreg.QueryValueEx(h,RUN_NAME)
                if value=='"'+str(exe)+'" --autostart':winreg.DeleteValue(h,RUN_NAME)
            except FileNotFoundError:pass

def save_launch_settings(raw):
    cfg=launch_settings(raw)
    if cfg['browser']!='system':
        path=cfg['browserPath'] or detect_browsers().get(cfg['browser'],'');p=Path(path)
        require(p.is_absolute() and p.is_file() and p.suffix.lower()=='.exe','Укажите существующий EXE выбранного браузера')
        cfg['browserPath']=str(p)
    else:cfg['browserPath']=''
    require(not cfg['autostart'] or cfg['autoOpen'] or cfg['background'],'Для автозапуска без вкладки включите фоновый режим')
    old=launch_settings();p=ROOT/'launch-settings.json';tmp=p.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8');apply_autostart(cfg)
        try:os.replace(tmp,p)
        except OSError:apply_autostart(old);raise
    finally:tmp.unlink(missing_ok=True)
    return cfg

VIEWS={'home','contracts','stock','calculator','proposal','act','summary','profile','competitors','trash','settings'}
def ui_state(raw):
    require(isinstance(raw,dict),'Некорректное состояние интерфейса')
    out={}
    for k,choices in [('view',VIEWS),('contractView',{'list','board'}),('contractSort',{'newest','oldest','customer'}),('stockMode',{'month','year','custom'}),('stockTab',{'stockBalances','stockHistory'})]:
        if k in raw:require(isinstance(raw[k],str) and raw[k] in choices,'Некорректный параметр '+k);out[k]=raw[k]
    for k in ('year','summaryYear','month'):
        if k in raw:require(type(raw[k]) is int and (1<=raw[k]<=12 if k=='month' else 2026<=raw[k]<=2100),'Некорректный период');out[k]=raw[k]
    for k in ('stockFrom','stockTo','stockProduct','homeSearch','cardId'):
        if k in raw:require(isinstance(raw[k],str) and len(raw[k])<=200,'Некорректный фильтр');out[k]=raw[k]
    if 'expandedMonths' in raw:
        values=raw['expandedMonths'];require(isinstance(values,list) and len(values)<=120 and all(isinstance(v,str) and re.fullmatch(r'20[2-9][0-9]-(?:[1-9]|1[0-2])|2100-(?:[1-9]|1[0-2])',v) for v in values),'Некорректные раскрытые месяцы');out['expandedMonths']=values
    if 'summaryShowAllMonths' in raw:require(type(raw['summaryShowAllMonths']) is bool,'Некорректное отображение');out['summaryShowAllMonths']=raw['summaryShowAllMonths']
    return out

class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()
def connect():
    c=sqlite3.connect(DB,timeout=15,factory=ClosingConnection);c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=15000');c.execute('PRAGMA synchronous=FULL');return c
def meta(c):
    settings=snapshot(c,'settings',{});last=None;warning=settings.get('backupWarning','')
    try:
        if BACKUPS.is_dir():last=max(BACKUPS.glob('База_*.sqlite3'),key=lambda p:p.stat().st_mtime,default=None)
        local_at=datetime.fromtimestamp(last.stat().st_mtime,MSK).strftime('%d.%m.%Y %H:%M:%S') if last else ''
    except OSError as e:
        local_at='';last=None;warning='Не удалось проверить локальные копии: '+str(e)
    cloud_path=settings.get('cloudPath','');cloud_at=settings.get('cloudAt','');status=settings.get('cloudStatus','Локальная база SQLite; облако не настроено')
    return dict(backupAt=local_at,backupPath=str(last) if last else '',backupWarning=warning,cloudStatus=status,cloudAt=cloud_at,cloudPath=cloud_path,cloudVerified=settings.get('cloudVerified') is True,cloudVerifyAt=settings.get('cloudVerifyAt',''),cloudLastVerifiedAt=settings.get('cloudLastVerifiedAt',''),cloudLastVerifiedPath=settings.get('cloudLastVerifiedPath',''),cloudServerPath=settings.get('cloudServerPath',''),cloudChecksum=settings.get('cloudChecksum',''),cloudSize=settings.get('cloudSize',0),cloudPending=bool(settings.get('cloudPending')),cloudCheckError=settings.get('cloudCheckError',''),cloudRetentionWarning=settings.get('cloudRetentionWarning',''))
def boot(c):
    settings=snapshot(c,'settings',{});return dict(owner=snapshot(c,'owner'),records=records(c),stock=snapshot(c,'stock'),finance=snapshot(c,'finance'),notes=snapshot(c,'notes'),cloudFolder=settings.get('cloudFolder',''),defaultCloudFolder='',cloudRemote=settings.get('cloudRemote',''),dbPath=str(DB),nonce=NONCE,version=int(BUILD.rsplit('-',1)[1]),launch=launch_settings(),browsers=detect_browsers(),port=PORT,uiState=settings.get('uiState',{}),**meta(c))
def update_meta(c,**values):
    settings=snapshot(c,'settings',{});settings.update(values);put(c,'settings',settings);return settings
def changed(c):return dict(records=records(c),stock=snapshot(c,'stock'),finance=snapshot(c,'finance'),**meta(c))
def cloud_time():return datetime.now(MSK).strftime('%d.%m.%Y %H:%M:%S')
def cloud_remote(value):
    if not value:return ''
    require(re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_ -]{0,79}:[^:\\\x00-\x1f]*',value) is not None,'Укажите настроенный remote rclone: например cloud:Резервные копии')
    name,path=value.split(':',1);parts=path.strip('/').split('/') if path.strip('/') else []
    require(all(x not in ('','.','..') for x in parts),'Некорректная папка remote')
    return name+':'+('/'.join(parts)+'/' if parts else '')

def cloud_hash(path):
    h=hashlib.md5(usedforsecurity=False);size=0
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block);size+=len(block)
    return size,h.hexdigest()

def rclone_run(args,stop):
    exe=shutil.which('rclone')
    if not exe:raise ValueError('rclone не найден в PATH. Установите rclone и настройте облачный диск.')
    if stop.is_set():raise InterruptedError('Завершение работы')
    # No shell, no credentials in database, no raw stderr (may contain sensitive paths/tokens).
    with subprocess.Popen([exe,*args,'--contimeout','10s','--timeout','30s','--retries','1','--low-level-retries','1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=0x08000000 if sys.platform=='win32' else 0) as proc:
        deadline=time.monotonic()+120
        while True:
            try:out,_=proc.communicate(timeout=.5);break
            except subprocess.TimeoutExpired:
                if stop.is_set() or time.monotonic()>=deadline:
                    proc.kill();proc.communicate();raise InterruptedError('Завершение работы') if stop.is_set() else ValueError('Истекло время ожидания rclone')
        require(proc.returncode==0,'rclone: операция '+args[0]+' не выполнена (код '+str(proc.returncode)+'). Проверьте интернет и авторизацию облачного диска.')
        return out.decode('utf-8-sig')

def verify_cloud_file(job,stop,run=rclone_run):
    remote=cloud_remote(job['remote']);name=remote.split(':',1)[0]
    remotes=run(['listremotes','--long'],stop)
    backend=next((m.group(1) for line in remotes.splitlines() if (m:=re.fullmatch(re.escape(name)+r':\s+(\S+)(?:\s+.*)?',line.strip()))),None)
    require(backend and backend not in ('local','alias','union','combine','chunker','crypt','hasher','cache','overlay'),'Укажите настроенный remote облачного диска, а не локальный или составной remote')
    path=Path(job['path']);require(path.is_file(),'Локальный архив не найден; создайте новую копию')
    size,md5=cloud_hash(path);require((size,md5)==(job['size'],job['md5']),'Локальный архив изменён; создайте новую копию')
    target=remote+'ContractsIP-SQLite-backups/'+path.name
    if not job.get('verifyOnly'):run(['copyto',str(path),target,'--immutable','--checksum'],stop)
    info=json.loads(run(['lsjson',target,'--stat','--hash'],stop))
    require(isinstance(info,dict) and info.get('IsDir') is False and info.get('Name')==path.name,'Файл в облачном хранилище не найден')
    require(type(info.get('Size')) is int and info['Size']==size,'Размер облачного архива не совпадает')
    hashes=info.get('Hashes');actual=hashes.get('MD5','') if isinstance(hashes,dict) else ''
    if actual:
        require(isinstance(actual,str) and actual.lower()==md5,'Контрольная сумма MD5 облачного архива не совпадает')
    else:
        # Providers such as OneDrive and Dropbox may not expose MD5. Download to a private temporary directory and verify actual bytes.
        with tempfile.TemporaryDirectory(prefix='contractsip-cloud-verify-') as folder:
            downloaded=Path(folder)/'verify.zip'
            run(['copyto',target,str(downloaded)],stop)
            require(downloaded.is_file() and downloaded.stat().st_size==size and digest(downloaded)==digest(path),'Содержимое облачного архива не совпадает')
    require(cloud_hash(path)==(size,md5),'Локальный архив изменился при загрузке')
    return target

def cloud_destination(raw):
    # Do not create a missing drive root: a disconnected cloud mount must be an error.
    p=Path(raw);require(p.is_absolute(),'Укажите полный путь к папке резервных копий')
    require(p.is_dir(),'Выбранная папка не найдена или недоступна: '+raw)
    p=p.resolve();dest=(p if p.name.casefold()=='contractsip-sqlite-backups' else p/'ContractsIP-SQLite-backups').resolve()
    require(all(not dest.is_relative_to(x.resolve()) and not x.resolve().is_relative_to(dest) for x in (ROOT,DOCS)),'Выберите папку копий вне приложения и документов')
    dest.mkdir(exist_ok=True);require(dest.is_dir(),'Путь копий не является папкой')
    return dest

AUTO_BACKUP_INTERVAL=30*60
AUTO_BACKUP_RETRY=60
ARCHIVE_LIMIT=30
ARCHIVE_NAME=re.compile(r'ContractsIP-\d{8}-\d{6}-\d{6}-[0-9a-f]{8}\.zip')
SYSTEM_DOCS={'desktop.ini','thumbs.db','ehthumbs.db'}

# Full user-data archive. Program binaries, runtime, credentials from rclone and
# browser-only storage are deliberately not restored from a ZIP.
PERSONAL_ASSETS=('profile.mjs','act-constructor.html','quote-editor.html','cover-letter.html','cover-logo.png','cover-signature.png','cover-stamp.png')
FULL_MANIFEST='backup-manifest.json'
FULL_LIMIT=20*1024**3
FULL_ENTRIES=100000
RESTORE_MARKER='.restore-state.json'

def stream_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def checked_path(p):
    require(not p.is_symlink() and p.resolve()==p.absolute(),'Ссылки и перенаправления не допускаются: '+str(p))
    return p

def archive_parts(name):
    parts=name.split('/')
    for part in parts:
        require(part and part not in ('.','..') and not re.search(r'[<>:"\\|?*\x00-\x1f]',part) and part==part.rstrip(' .') and not re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?',part),'Недопустимый путь в архиве: '+name)
    return parts

def document_inventory():
    a.checked_docs_root();dirs=[];files=[]
    def fail(e):raise e
    for root,ds,fs in os.walk(DOCS,followlinks=False,onerror=fail):
        root=checked_path(Path(root));ds.sort();fs.sort()
        for n in ds:
            f=checked_path(root/n);archive_parts(f.relative_to(DOCS).as_posix());dirs.append(f)
        for n in fs:
            if n.casefold() in SYSTEM_DOCS:continue
            f=checked_path(root/n);require(f.is_file(),'Необычный файл документов: '+str(f));archive_parts(f.relative_to(DOCS).as_posix());files.append(f)
    return dirs,files

def backup_documents():return document_inventory()[1]

def personal_paths():
    app=ROOT/'app';result={}
    for n in PERSONAL_ASSETS:
        p=app/n
        if p.exists():require(checked_path(p).is_file(),'Необычный файл шаблона');result['personal/app/'+n]=p
    mf=ROOT/'original-ui-sha256.json'
    if mf.is_file():
        m=json.loads(mf.read_text(encoding='utf-8'));alias=m.get('__local_act_copy__.html')
        if alias:
            candidates=[f for f in app.glob('*.html') if f.name not in m and checked_path(f).is_file() and stream_hash(f)==alias]
            require(len(candidates)==1,'Не найден персональный шаблон акта');result['personal/local-act.html']=candidates[0]
    return result

def backup():
    # Full SQLite snapshot, including notes and all auxiliary tables. Keep old
    # recovery files readable by the legacy partial-restore function.
    BACKUPS.mkdir(exist_ok=True);name='База_'+datetime.now(MSK).strftime('%Y-%m-%d_%H%M%S%f')+'.sqlite3';p=BACKUPS/name;tmp=p.with_suffix('.tmp')
    try:
        with closing(sqlite3.connect(DB)) as src,closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst);validate_backup_db(dst)
        os.replace(tmp,p)
    finally:tmp.unlink(missing_ok=True)
    return p

def validate_backup_db(c):
    require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not c.execute('PRAGMA foreign_key_check').fetchall(),'Копия SQLite повреждена')
    require(model(raw_events(c))[2]==snapshot(c,'stock'),'Журнал склада не совпадает')
    records(c)
    for name in ('owner','stock','finance','notes','folders','competitors','letter_drafts','settings'):
        require(snapshot(c,name) is not None,'В базе отсутствует раздел '+name)
    for link in snapshot(c,'folders')['entries']:
        path=link['path'];require(path.startswith('docs:'),'Непереносимая ссылка на документы');parts=archive_parts(path[5:]);require(len(parts)==1 or len(parts)==2 and parts[0]==a.ARCHIVE_NAME,'Некорректная привязка папки')

def backup_fingerprint(c):
    # Include notes and personal settings/assets; ignore volatile verification and UI navigation.
    h=hashlib.sha256()
    for table,order in [('contracts','id'),('contract_items','contract_id,seq'),('stock_events','seq'),('snapshots','name'),('workbook_cells','sheet,row,col'),('documents','path')]:
        h.update(table.encode()+b'\n')
        where=" WHERE name != 'settings'" if table=='snapshots' else ''
        for row in c.execute('SELECT * FROM '+table+where+' ORDER BY '+order):h.update(enc(row).encode('utf-8')+b'\n')
    cfg=snapshot(c,'settings',{});stable={k:v for k,v in cfg.items() if not k.startswith(('cloud','autoBackup','backup')) and k!='uiState'}
    stable.update(cloudFolder=cfg.get('cloudFolder',''),cloudRemote=cfg.get('cloudRemote',''))
    h.update(enc(stable).encode());h.update(enc(launch_settings()).encode())
    dirs,files=document_inventory()
    for d in dirs:h.update(enc('documents/'+d.relative_to(DOCS).as_posix()+'/').encode())
    for name,f in [('documents/'+f.relative_to(DOCS).as_posix(),f) for f in files]+list(personal_paths().items()):
        h.update(enc(name).encode('utf-8')+b'\n');h.update(stream_hash(f).encode())
    return h.hexdigest()

def write_full_archive(out,db):
    dirs,files=document_inventory();personal=personal_paths();entries={};launch=enc(launch_settings()).encode('utf-8')
    tmp=out.with_suffix('.tmp');out.parent.mkdir(parents=True,exist_ok=True)
    def add(z,name,path):
        entries[name]={'size':path.stat().st_size,'sha256':stream_hash(path)};z.write(path,name)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
            add(z,'contracts.sqlite3',db)
            for name in ['documents/']+['documents/'+d.relative_to(DOCS).as_posix()+'/' for d in dirs]:z.writestr(name,b'')
            for f in files:add(z,'documents/'+f.relative_to(DOCS).as_posix(),f)
            for name,path in personal.items():add(z,name,path)
            name='personal/launch-settings.json';entries[name]={'size':len(launch),'sha256':hashlib.sha256(launch).hexdigest()};z.writestr(name,launch)
            z.writestr(FULL_MANIFEST,enc(dict(format=2,created=cloud_time(),build=BUILD,files=entries,directories=['documents/']+['documents/'+d.relative_to(DOCS).as_posix()+'/' for d in dirs])))
        verify_full_archive(tmp)
        os.replace(tmp,out)
    finally:tmp.unlink(missing_ok=True)
    return out

def verify_full_archive(path,extract=None):
    """Validate exact archive contents and SHA-256; never use extractall."""
    allowed={'contracts.sqlite3','personal/launch-settings.json','personal/local-act.html',*('personal/app/'+n for n in PERSONAL_ASSETS)}
    with zipfile.ZipFile(path) as z:
        infos=z.infolist();require(len(infos)<=FULL_ENTRIES and sum(x.file_size for x in infos)<=FULL_LIMIT,'Архив превышает допустимый размер или число файлов')
        names=[x.filename for x in infos];require(len(names)==len(set(names)) and len(names)==len({n.rstrip('/').casefold() for n in names}),'Повторяющиеся пути в архиве')
        require(FULL_MANIFEST in names,'Это старая или неполная копия; нужен полный ZIP формата 2')
        require(z.getinfo(FULL_MANIFEST).file_size<=32*1024**2,'Слишком большой манифест')
        m=json.loads(z.read(FULL_MANIFEST));require(m.get('format')==2 and isinstance(m.get('files'),dict) and isinstance(m.get('directories'),list),'Неподдерживаемый формат архива')
        files=m['files'];dirs=m['directories'];require(len(set(dirs))==len(dirs) and set(names)==set(files)|set(dirs)|{FULL_MANIFEST} and not set(files)&set(dirs),'Состав архива не совпадает с манифестом')
        require('contracts.sqlite3' in files and 'personal/launch-settings.json' in files and 'documents/' in dirs,'Неполный архив')
        for info in infos:
            n=info.filename;parts=archive_parts(n.rstrip('/'));mode=(info.external_attr>>16)&0o170000
            require(mode in (0,0o100000,0o040000) and not info.flag_bits&1,'Недопустимый тип файла архива')
            if n==FULL_MANIFEST:continue
            if info.is_dir():
                require(n in dirs and parts[0]=='documents' and info.file_size==0 and (len(parts)==1 or '/'.join(parts[:-1])+'/' in dirs),'Недопустимая папка архива')
                if extract is not None:(extract.joinpath(*parts)).mkdir(parents=True,exist_ok=True)
                continue
            require(n in files and (n in allowed or parts[0]=='documents' and len(parts)>1),'Недопустимый файл архива: '+n)
            if parts[0]=='documents':require('/'.join(parts[:-1])+'/' in dirs,'Отсутствует родительская папка')
            f=files[n];require(type(f.get('size')) is int and f['size']==info.file_size and re.fullmatch(r'[0-9a-f]{64}',f.get('sha256','')),'Некорректная контрольная сумма')
            h=hashlib.sha256();target=extract.joinpath(*parts) if extract is not None else None
            if target is not None:target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as src, (target.open('xb') if target is not None else open(os.devnull,'wb')) as dst:
                for block in iter(lambda:src.read(1024*1024),b''):h.update(block);dst.write(block)
            require(h.hexdigest()==f['sha256'],'Контрольная сумма не совпадает: '+n)
        return m

def restore_db_from(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DB)) as dst:
        src.backup(dst);validate_backup_db(dst)

def recover_full_restore():
    marker=ROOT/RESTORE_MARKER
    if not marker.exists():return
    checked_path(marker);j=json.loads(marker.read_text(encoding='utf-8'));name=j['stage'];require(re.fullmatch(r'\.contracts-restore-[0-9a-f]{32}',name),'Некорректный журнал восстановления')
    stage=checked_path(ROOT.parent/name);require(stage.is_dir(),'Не найдена папка отката восстановления')
    # Always roll back an interrupted restore. Old files remain available until commit.
    old=stage/'old';require(old.is_dir(),'Нет страховочных данных')
    with closing(sqlite3.connect((old/'contracts.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:validate_backup_db(c)
    restore_db_from(old/'contracts.sqlite3')
    if (stage/'old-documents').exists():
        if DOCS.exists():DOCS.rename(stage/('discard-'+uid()))
        (stage/'old-documents').rename(DOCS)
    for name,path in j['assets'].items():
        # Paths are only known local assets, launch settings and the UI hash manifest.
        require(name in ('launch-settings.json','original-ui-sha256.json') or name.startswith('app/') and len(archive_parts(name))==2 and (name[4:] in PERSONAL_ASSETS or name[4:].endswith('.html') and name[4:] not in ('index.html','auction-calculator.html')),'Недопустимый путь отката')
        dest=checked_path(ROOT/name);src=old/name
        if path:require(src.is_file(),'Нет файла отката: '+name);a.atomic_write(dest,src.read_bytes())
        else:dest.unlink(missing_ok=True)
    marker.unlink();shutil.rmtree(stage,ignore_errors=True)

def full_restore(b):
    require(b.get('confirmed') is True,'Подтвердите замену всех данных из доверенного архива')
    src=Path(txt(b.get('path'),4096,True));require(src.is_absolute() and src.is_file() and src.suffix.lower()=='.zip','Выберите существующий ZIP-архив')
    require(not (ROOT/RESTORE_MARKER).exists(),'Предыдущее восстановление не завершено; перезапустите программу')
    stage=ROOT.parent/('.contracts-restore-'+uid());stage.mkdir();old=stage/'old';old.mkdir();new=stage/'new';new.mkdir();marker=ROOT/RESTORE_MARKER
    safety=None
    try:
        # Work on a stable private copy, not an archive changing during cloud sync.
        archive=stage/'incoming.zip';require(src.stat().st_size<=FULL_LIMIT,'Архив слишком большой');shutil.copyfile(src,archive)
        m=verify_full_archive(archive,new)
        with closing(sqlite3.connect((new/'contracts.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:validate_backup_db(c)
        launch_settings(json.loads((new/'personal/launch-settings.json').read_text(encoding='utf-8')))
        personal=personal_paths();require(set(personal)=={n for n in m['files'] if n.startswith('personal/') and n!='personal/launch-settings.json'},'Состав персональных шаблонов не соответствует установленному комплекту')
        # Keep the full prior state separately, beyond automatic rotation.
        db=backup();safety=BACKUPS/'before-restore'/('Before-restore-'+datetime.now(MSK).strftime('%Y%m%d-%H%M%S-%f')+'-'+uid()[:8]+'.zip');write_full_archive(safety,db);shutil.copyfile(db,old/'contracts.sqlite3')
        dests={n:f for n,f in personal.items()};dests['personal/launch-settings.json']=ROOT/'launch-settings.json'
        mf=ROOT/'original-ui-sha256.json';rollback={}
        for dest in [*dests.values(),mf]:
            rel=dest.relative_to(ROOT).as_posix();exists=dest.is_file();rollback[rel]=exists
            if exists:out=old/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest,out)
        a.atomic_write(marker,enc({'stage':stage.name,'assets':rollback}).encode())
        # Swap document directory on the same volume, preserving old tree until commit.
        a.checked_docs_root();DOCS.rename(stage/'old-documents');(new/'documents').rename(DOCS)
        restore_db_from(new/'contracts.sqlite3')
        for name,dest in dests.items():a.atomic_write(dest,(new/name).read_bytes())
        if mf.is_file():
            hashes=json.loads(mf.read_text(encoding='utf-8'))
            for name,dest in personal.items():hashes['__local_act_copy__.html' if name=='personal/local-act.html' else dest.name]=stream_hash(dest)
            a.atomic_write(mf,(json.dumps(hashes,ensure_ascii=False,indent=2)+'\n').encode())
            verify_ui()
        with connect() as c:
            # Historical success markers refer to the old PC/archive, not the restored state.
            cfg=snapshot(c,'settings',{});cfg={k:v for k,v in cfg.items() if not k.startswith(('autoBackup','backup','cloud')) or k in ('cloudFolder','cloudRemote')};cfg.update(cloudStatus='Данные восстановлены; проверьте папку резервных копий на этом ПК',cloudVerified=False);put(c,'settings',cfg)
        count=len(records_from_db());marker.unlink();return dict(ok=True,safetyPath=str(safety),records=count,restartRecommended=True)
    except Exception:
        if marker.exists():recover_full_restore()
        raise
    finally:
        if not marker.exists():shutil.rmtree(stage,ignore_errors=True)

def records_from_db():
    with connect() as c:return records(c)

def prune_archives(dest,latest):
    # Never touch arbitrary ZIPs, directories, symlinks, or files in other folders.
    # Always retain the just-created verified archive, even if the PC clock moved back.
    try:
        candidates=[f for f in dest.iterdir() if f!=latest and ARCHIVE_NAME.fullmatch(f.name) and not f.is_symlink() and f.is_file()]
        candidates.sort(key=lambda f:(f.stat().st_mtime_ns,f.name),reverse=True)
        for f in candidates[ARCHIVE_LIMIT-1:]:f.unlink()
        return ''
    except OSError as e:return 'Не удалось удалить старые архивы: '+str(e)

def sync_copy(c,backup_file=None):
    settings=snapshot(c,'settings',{});raw=settings.get('cloudFolder','');remote=cloud_remote(settings.get('cloudRemote',''))
    if not raw and not remote:return update_meta(c,cloudStatus='Облачная папка не настроена',cloudVerified=False,cloudPending=None,cloudCheckError='')
    # Keep a recovery snapshot even if the selected drive is disconnected.
    backup_file=backup_file or backup()
    if raw:dest=cloud_destination(raw)
    else:
        dest=BACKUPS/'cloud-outbox';dest.mkdir(parents=True,exist_ok=True)
    out=dest/('ContractsIP-'+datetime.now(MSK).strftime('%Y%m%d-%H%M%S-%f')+'-'+uid()[:8]+'.zip')
    write_full_archive(out,backup_file)
    size,md5=cloud_hash(out);job=dict(id=uid(),remote=remote,path=str(out),size=size,md5=md5) if remote else None
    warning=prune_archives(dest,out)
    return update_meta(c,cloudRetentionWarning=warning,cloudStatus='Копия сохранена; ожидается загрузка и проверка через rclone' if remote else 'Копия сохранена и проверена в выбранной папке; загрузку на сервер выполняет программа облачного диска',cloudAt=cloud_time(),cloudPath=str(out),cloudVerified=False,cloudVerifyAt='',cloudServerPath='',cloudChecksum=md5,cloudSize=size,cloudPending=job,cloudCheckError='')

def safe_sync_copy(c,backup_file=None):
    cfg=snapshot(c,'settings',{});fp=''
    if not cfg.get('cloudFolder') and not cfg.get('cloudRemote'):return sync_copy(c,backup_file)
    try:
        fp=backup_fingerprint(c)
        # Retry a disconnected destination without accumulating identical local SQLite snapshots.
        if backup_file is None and fp and cfg.get('autoBackupRetry') and cfg.get('autoBackupLocalFingerprint')==fp:
            old=Path(cfg.get('autoBackupLocalPath',''))
            if old.is_file() and old.parent.resolve()==BACKUPS.resolve() and re.fullmatch(r'База_\d{4}-\d{2}-\d{2}_\d{12}\.sqlite3',old.name) and cfg.get('autoBackupLocalHash')==digest(old):backup_file=old
        backup_file=backup_file or backup()
        result=sync_copy(c,backup_file)
        return update_meta(c,autoBackupFingerprint=fp,autoBackupRetry=False,autoBackupLocalPath='',autoBackupLocalFingerprint='',autoBackupLocalHash='')
    except Exception as e:
        # Even if a document cannot be read, preserve a recovery database when possible.
        if backup_file is None:
            try:backup_file=backup()
            except Exception:logging.exception('Local recovery backup failed')
        try:local_hash=digest(backup_file) if backup_file else ''
        except OSError:local_hash=''
        return update_meta(c,cloudStatus='ОШИБКА резервного копирования: '+str(e),cloudVerified=False,cloudPending=None,cloudCheckError=str(e),autoBackupRetry=True,autoBackupLocalPath=str(backup_file or ''),autoBackupLocalFingerprint=fp,autoBackupLocalHash=local_hash)

def auto_backup_tick(force=False):
    """Run under the same lock as writes, regardless of browser activity. Return retry flag."""
    with LOCK,connect() as c:
        if (ROOT/RESTORE_MARKER).exists():return True
        cfg=snapshot(c,'settings',{})
        if not (cfg.get('cloudFolder') or cfg.get('cloudRemote')):return False
        try:
            last=Path(cfg.get('cloudPath',''))
            if not force and not cfg.get('autoBackupRetry') and last.is_file() and cfg.get('autoBackupFingerprint')==backup_fingerprint(c) and cloud_hash(last)==(cfg.get('cloudSize'),cfg.get('cloudChecksum')):return False
        except Exception:pass # safe_sync_copy reports the error and retains local recovery
        result=safe_sync_copy(c)
        return bool(result.get('autoBackupRetry'))

def auto_backup_worker(stop,wake,config_wake):
    # First check on start, then every 30 minutes; failed copies retry every minute.
    while not stop.is_set():
        retry=False
        try:retry=auto_backup_tick()
        except Exception:logging.exception('Automatic backup failed');retry=True
        wake.set() # wake the independent rclone verifier, if configured
        config_wake.wait(AUTO_BACKUP_RETRY if retry else AUTO_BACKUP_INTERVAL);config_wake.clear()

def queue_cloud_check(c):
    cfg=snapshot(c,'settings',{});remote=cloud_remote(cfg.get('cloudRemote',''));require(remote,'Настройте remote rclone в настройках')
    path=cfg.get('cloudPath','');md5=cfg.get('cloudChecksum','');size=cfg.get('cloudSize');require(path and re.fullmatch('[0-9a-f]{32}',md5) and type(size) is int and size>0,'Сначала создайте облачную копию')
    job=dict(id=uid(),remote=remote,path=path,size=size,md5=md5,verifyOnly=bool(cfg.get('cloudVerified') or cfg.get('cloudServerPath')))
    update_meta(c,cloudPending=job,cloudVerified=False,cloudStatus='Ожидается проверка через rclone',cloudCheckError='')

def cloud_attempt(stop,run=rclone_run):
    with LOCK,connect() as c:
        if (ROOT/RESTORE_MARKER).exists():return False
        job=snapshot(c,'settings',{}).get('cloudPending')
    if not job or stop.is_set():return False
    try:
        target=verify_cloud_file(job,stop,run);err=''
    except InterruptedError:return False
    except Exception as exc:target='';err=str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else 'Не удалось проверить облачный архив'
    if stop.is_set():return False
    with LOCK,connect() as c:
        cfg=snapshot(c,'settings',{})
        if cfg.get('cloudPending')!=job:return False # stale result after settings change/new backup
        at=cloud_time()
        if err:update_meta(c,cloudVerified=False,cloudVerifyAt=at,cloudStatus='ОШИБКА облачной проверки: '+err+' Повтор через 60 секунд.',cloudCheckError=err)
        else:update_meta(c,cloudVerified=True,cloudVerifyAt=at,cloudLastVerifiedAt=at,cloudLastVerifiedPath=target,cloudServerPath=target,cloudStatus='Копия подтверждена через rclone (размер и содержимое совпадают)',cloudCheckError='',cloudPending=None)
    return True

def cloud_worker(stop,wake):
    while not stop.is_set():
        try:cloud_attempt(stop)
        except Exception:logging.exception('Cloud verification worker failed')
        wake.wait(60);wake.clear()

class BrowserLifecycle:
    """Explicit tab-close + grace period; long lease for throttled background tabs."""
    def __init__(self, clock=time.monotonic, grace=15, lease=180, startup=120):
        self.clock=clock;self.grace=grace;self.lease=lease;self.startup=startup
        self.lock=threading.Lock();self.tabs={};self.deadline=clock()+startup
        self.operations=0;self.closing=False;self.background=False
    def wake(self):
        with self.lock:
            if self.closing:return False
            if not any(v[1] for v in self.tabs.values()):self.deadline=self.clock()+self.startup
            return True
    def update(self, tab, seq, opened):
        with self.lock:
            if self.closing:return False
            now=self.clock();previous=self.tabs.get(tab)
            if previous and seq<=previous[0]:return True
            if len(self.tabs)>=4096 and tab not in self.tabs:return False
            self.tabs[tab]=(seq,opened,now)
            if opened:self.deadline=None
            elif not any(v[1] for v in self.tabs.values()):self.deadline=now+self.grace
            return True
    def begin(self):
        with self.lock:
            if self.closing:return False
            self.operations+=1;return True
    def end(self):
        with self.lock:self.operations-=1
    def set_background(self, enabled):
        with self.lock:
            self.background=enabled
            if not enabled and not any(v[1] for v in self.tabs.values()):self.deadline=self.clock()+self.grace
    def expire(self):
        with self.lock:
            if self.closing:return True
            now=self.clock()
            for k,(seq,opened,last) in list(self.tabs.items()):
                if opened and now-last>=self.lease:self.tabs[k]=(seq,False,now)
                elif not opened and now-last>=self.lease*2:del self.tabs[k]
            if self.background:return False
            if any(v[1] for v in self.tabs.values()):return False
            if self.deadline is None:self.deadline=now+self.grace
            if now>=self.deadline and not self.operations:self.closing=True;return True
            return False

# Account-free city lookup. Explicit requests only, cached and serialized.
from functools import lru_cache
GEO_ENDPOINTS={'search':'https://photon.komoot.io/api/', 'timezone':'https://timeapi.io/api/timezone/coordinate'}
GEO_LOCK=threading.Lock()
GEO_LAST=0.0

def geo_request(endpoint,params):
    global GEO_LAST
    require(endpoint in GEO_ENDPOINTS,'Недопустимый запрос поиска города')
    url=GEO_ENDPOINTS[endpoint]; host=urlsplit(url).hostname
    with GEO_LOCK:
        time.sleep(max(0,1-(time.monotonic()-GEO_LAST)))
        try:
            with urlopen(Request(url+'?'+urlencode(params,doseq=True),headers={'Accept':'application/json','Accept-Language':'ru','User-Agent':'ContractsIP/021 (https://github.com/beta73b-del/contract-accounting-updates)'}),timeout=8) as response:
                require(urlsplit(response.geturl()).scheme=='https' and urlsplit(response.geturl()).hostname==host,'Недопустимое перенаправление')
                data=response.read(524289)
            require(len(data)<=524288,'Слишком большой ответ сервиса')
            payload=json.loads(data)
        except (OSError,URLError,UnicodeError,json.JSONDecodeError):
            raise ValueError('Интернет-поиск недоступен. Проверьте интернет или воспользуйтесь локальным справочником.') from None
        finally:GEO_LAST=time.monotonic()
    require(isinstance(payload,dict),'Некорректный ответ сервиса')
    return payload

def geo_coordinates(lat,lng):
    require(not isinstance(lat,bool) and not isinstance(lng,bool),'Некорректные координаты')
    try:lat,lng=float(lat),float(lng)
    except (TypeError,ValueError,OverflowError):raise ValueError('Некорректные координаты') from None
    require(math.isfinite(lat) and math.isfinite(lng) and -90<=lat<=90 and -180<=lng<=180,'Некорректные координаты')
    return lat,lng

def geo_zone(value):
    return value if isinstance(value,str) and len(value)<=100 and re.fullmatch(r'[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*',value) else ''

def geo_search(body):
    q=body.get('query');require(isinstance(q,str) and 2<=len(q.strip())<=120,'Введите название населённого пункта (2–120 символов)')
    return geo_search_cached(' '.join(q.split()).lower())

@lru_cache(maxsize=256)
def geo_search_cached(q):
    payload=geo_request('search',dict(q=q,limit=20,layer=['city','locality','district']))
    rows=payload.get('features');require(isinstance(rows,list),'Некорректный список населённых пунктов')
    result=[];seen=set()
    def clean(v):return v.strip()[:200] if isinstance(v,str) else ''
    for row in rows[:20]:
        if not isinstance(row,dict):continue
        p=row.get('properties');g=row.get('geometry')
        if not isinstance(p,dict) or not isinstance(g,dict) or g.get('type')!='Point':continue
        coords=g.get('coordinates')
        if not isinstance(coords,list) or len(coords)<2:continue
        try:lat,lng=geo_coordinates(coords[1],coords[0])
        except ValueError:continue
        name=clean(p.get('name'));key=(name,clean(p.get('state')),clean(p.get('country')),lat,lng)
        if not name or key in seen:continue
        seen.add(key)
        result.append(dict(id=len(result)+1,name=name,region=clean(p.get('state')),district=clean(p.get('county') or p.get('district')),country=clean(p.get('country')),lat=lat,lng=lng,zone=''))
    return dict(places=result)

def geo_timezone(body):
    lat,lng=geo_coordinates(body.get('lat'),body.get('lng'))
    return geo_timezone_cached(lat,lng)

@lru_cache(maxsize=256)
def geo_timezone_cached(lat,lng):
    payload=geo_request('timezone',dict(latitude=lat,longitude=lng))
    zone=geo_zone(payload.get('timeZone'));require(bool(zone),'Часовой пояс для выбранного пункта не найден')
    return dict(zone=zone)

# 019's updater excludes this personal editor file. Serve the verified Moscow-date change without replacing it.
CALCULATOR_SOURCE_HASH='4f07723b9c7810e02afb85997cca9f4942c02563db7d2e058a8c6a088007ae15'
CALCULATOR_SERVED_HASH='0f06b93188abf932e4465bf9eec500c0fb14c69466b94ce5227cea34dd1dc65e'
CALCULATOR_REPLACEMENTS=[(b'new Date().getFullYear()', b"Number(new Intl.DateTimeFormat('en',{timeZone:'Europe/Moscow',year:'numeric'}).format(new Date()))"), (b"new Date().toLocaleDateString('ru-RU')", b"new Date().toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})")]
# Keep the personal calculator file unchanged; strip only routine success labels in served UI.
CALCULATOR_REPLACEMENTS += [
    ('● Сохранено автоматически'.encode(), b''),
    ('Сохранение…'.encode(), b''),
    ('<p>Несколько контрактов и позиций · независимые расчёты · УСН 5% / 15%</p>'.encode(), b''),
    (b'</style>', b'#save-status:empty,.tax-info:not(.warning){display:none!important}</style>'),
]
def ui_bytes(p):
    raw=p.read_bytes()
    if p==APP/'auction-calculator.html':
        require(hashlib.sha256(raw).hexdigest()==CALCULATOR_SOURCE_HASH,'Исходный файл калькулятора не соответствует сборке')
        for before,after in CALCULATOR_REPLACEMENTS:
            require(raw.count(before)==1,'Несовместимая версия калькулятора');raw=raw.replace(before,after)
        require(hashlib.sha256(raw).hexdigest()==CALCULATOR_SERVED_HASH,'Ошибка проверки калькулятора')
    return raw

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.getLogger("contracts").info(format, *args)
    def allowed(self):return self.headers.get('Host','').lower() in ('localhost','127.0.0.1',f'localhost:{PORT}',f'127.0.0.1:{PORT}')
    def send(self,b,typ,code=200,filename=None):
        self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('X-Contracts-Build',BUILD);self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(b)
    def js(self,v,code=200):self.send(enc(v).encode(),'application/json; charset=utf-8',code)
    def error(self,msg,code=400):self.js({'error':msg},code)
    def do_GET(self):
        if not self.allowed():return self.error('Только локальное подключение',403)
        path=urlsplit(self.path).path
        if path=='/__build':return self.js(dict(build=BUILD,instance=INSTANCE,storage='SQLite',port=PORT,dbPath=str(DB)))
        if path=='/api/browser-session':
            life=getattr(self.server,'lifecycle',None)
            if self.headers.get('X-Contracts-Launch')!='1':return self.error('Требуется локальный запрос',403)
            if life and not life.wake():return self.error('Сервер завершает работу',503)
            return self.js(dict(key=NONCE))
        if path=='/__check':return self.send((ROOT/'browser-check.html').read_bytes(),'text/html; charset=utf-8')
        if path in ('/api/bootstrap','/api/notes','/api/competitors'):
            try:
                with LOCK,connect() as c:return self.js(boot(c) if path.endswith('bootstrap') else snapshot(c,path.rsplit('/',1)[1]))
            except Exception as e:traceback.print_exc();return self.error(str(e),500)
        if path=='/':name='index.html'
        elif path=='/favicon.ico':name='favicon.ico'
        elif path.startswith('/app/'):name=unquote(path[5:])
        else:return self.error('Не найдено',404)
        p=(APP/name).resolve()
        if not p.is_relative_to(APP.resolve()) or not p.is_file():return self.error('Не найдено',404)
        typ={'.mjs':'text/javascript','.js':'text/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.png':'image/png','.ico':'image/x-icon'}.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        return self.send(ui_bytes(p),typ+('; charset=utf-8' if typ.startswith('text/') else ''))
    def do_POST(self):
        life=getattr(self.server,'lifecycle',None)
        if urlsplit(self.path).path=='/api/browser-presence':
            if not self.allowed() or self.headers.get('Origin')!='http://'+self.headers.get('Host',''):return self.error('Только локальная сессия',403)
            if not self.headers.get('Content-Type','').startswith('application/json'):return self.error('Требуется JSON',415)
            try:
                n=int(self.headers.get('Content-Length','0'));require(0<n<=1024,'Некорректный размер');b=json.loads(self.rfile.read(n));require(isinstance(b,dict),'Некорректный JSON')
                require(isinstance(b.get('key'),str) and secrets.compare_digest(b['key'],NONCE),'Неверный ключ сессии')
                tab=b.get('tab');seq=b.get('seq');opened=b.get('open')
                require(isinstance(tab,str) and re.fullmatch(r'[a-f0-9-]{36}',tab) and type(seq) is int and 0<seq<2**53 and type(opened) is bool,'Некорректная сессия')
                if life and not life.update(tab,seq,opened):return self.error('Сервер завершает работу',503)
                return self.js(dict(ok=True))
            except (ValueError,TypeError,KeyError) as e:return self.error(str(e),400)
        if life and not life.begin():return self.error('Сервер завершает работу',503)
        try:return self.handle_operation()
        finally:
            if life:life.end()
    def handle_operation(self):
        if not self.allowed() or not secrets.compare_digest(self.headers.get('X-Contracts-Key',''),NONCE):return self.error('Сессия устарела. Обновите страницу',403)
        if not self.headers.get('Content-Type','').startswith('application/json'):return self.error('Требуется Content-Type: application/json',415)
        try:
            length=int(self.headers.get('Content-Length','0'));require(0<=length<=131072,'Превышен размер запроса');b=json.loads(self.rfile.read(length) or b'{}');require(isinstance(b,dict),'Некорректный JSON');path=urlsplit(self.path).path
            # Read-only network lookup does not hold the database write lock.
            if path in ('/api/city/search','/api/city/timezone'):
                if getattr(self.server,'update_restart',False):return self.error('Выполняется обновление',503)
                if (ROOT/RESTORE_MARKER).exists():return self.error('Восстановление не завершено; перезапустите приложение',503)
                return self.js(geo_search(b) if path.endswith('/search') else geo_timezone(b))
            with LOCK:
                if getattr(self.server,'update_restart',False):return self.error('Выполняется обновление',503)
                if (ROOT/RESTORE_MARKER).exists():return self.error('Восстановление не завершено; перезапустите приложение',503)
                if path=='/api/launch/settings':
                    cfg=save_launch_settings(b);life=getattr(self.server,'lifecycle',None)
                    if life:life.set_background(cfg['background'])
                    return self.js(dict(launch=cfg,browsers=detect_browsers(),port=PORT))
                if path=='/api/choose-browser':
                    import tkinter as tk
                    from tkinter import filedialog
                    t=tk.Tk();t.withdraw();t.attributes('-topmost',True)
                    try:return self.js(dict(path=filedialog.askopenfilename(parent=t,filetypes=[('Windows EXE','*.exe')])))
                    finally:t.destroy()
                if path=='/api/update/check':
                    import updater
                    result=updater.check();result['last']=updater.last_status();return self.js(result)
                if path=='/api/update/install':
                    import updater
                    require(b.get('confirmed') is True,'Подтвердите обновление');require(sys.platform=='win32' and (ROOT/'ContractsIP.exe').is_file(),'Установка через интерфейс доступна в Windows')
                    result=updater.prepare(expected=b.get('version'));self.server.update_restart=True
                    life=getattr(self.server,'lifecycle',None)
                    if life:
                        with life.lock:life.closing=True
                    try:self.js(result)
                    finally:threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                if path=='/api/restore-backup':return self.js(restore(b))
                if path=='/api/choose-backup-zip':
                    import tkinter as tk
                    from tkinter import filedialog
                    t=tk.Tk();t.withdraw();t.attributes('-topmost',True)
                    try:return self.js(dict(path=filedialog.askopenfilename(parent=t,filetypes=[('Резервная копия ZIP','*.zip')])))
                    finally:t.destroy()
                if path=='/api/restore-full-backup':return self.js(full_restore(b))
                if path=='/api/shutdown':require(b.get('confirmed') is True,'Подтвердите завершение');self.js(dict(ok=True,message='Завершение программы'));threading.Thread(target=self.server.shutdown,daemon=True).start();return
                # Acquire exclusive write transaction before reading a revision. Context manager rolls back all errors.
                with connect() as c:
                    # Read-only auxiliary and backup operations must not hold a write transaction while copying DB.
                    if path=='/api/transfer-package':result=a.transfer_package(c,b)
                    elif path=='/api/backup':
                        p=backup();safe_sync_copy(c,p);update_meta(c,backupAt=cloud_time(),backupWarning='');result=meta(c)
                    elif path=='/api/sync':
                        safe_sync_copy(c)
                        result=meta(c)
                    elif path=='/api/cloud/check':queue_cloud_check(c);result=meta(c)
                    elif path=='/api/cloud/status':result=meta(c)
                    elif path=='/api/backups':result=dict(backups=backup_list())
                    elif path=='/api/export-executed':return self.send(export_executed(c,b),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename='executed-contracts.xlsx')
                    elif path=='/api/open-folder':
                        require(b.get('kind') in ('backup','database','package'),'Неизвестная папка');BACKUPS.mkdir(exist_ok=True);open_dir({'backup':BACKUPS,'database':ROOT,'package':ROOT.parent}[b['kind']]);result=dict(ok=True)
                    elif path=='/api/choose-folder':
                        import tkinter as tk
                        from tkinter import filedialog
                        t=tk.Tk();t.withdraw();t.attributes('-topmost',True)
                        try:result=dict(folder=filedialog.askdirectory(parent=t))
                        finally:t.destroy()
                    else:
                        if path in ('/api/delete-record','/api/trash/restore','/api/trash/clear'):backup()
                        c.execute('BEGIN IMMEDIATE')
                        if path=='/api/record':
                            r=save_record(c,b['record']);folder_error='';f=None
                            try:
                                f=folder(c,r['id'],'status')
                                if not f['known'] and r.get('customer') and r.get('product'):f=folder(c,r['id'],'ensure')
                                if f['known']:f=folder(c,r['id'],'sync');require(f['exists'],'Папка документов не найдена; пустая папка не создана')
                            except Exception as e:folder_error=str(e)
                            result=dict(record=r,folder=f,folderError=folder_error,**changed(c))
                        elif path=='/api/delete-record':delete_record(c,b);result=changed(c)
                        elif path=='/api/stock':stock_action(c,b);result=changed(c)
                        elif path=='/api/trash/list':result=dict(items=trash_items(c))
                        elif path=='/api/trash/restore':trash_restore(c,b);result=dict(items=trash_items(c),**changed(c))
                        elif path=='/api/trash/clear':trash_clear(c,b);result=dict(items=trash_items(c),**changed(c))
                        elif path=='/api/finance':result=dict(finance=finance(c,b),**meta(c))
                        elif path=='/api/notes':result=dict(notes=notes(c,b),cloudStatus=meta(c)['cloudStatus'])
                        elif path=='/api/competitors':result=competitors(c,b)
                        elif path=='/api/contract-folder':result=folder(c,b.get('id'),b.get('action'))
                        elif path=='/api/letter':result=letter(c,b)
                        elif path=='/api/ui-state':
                            value=ui_state(b);session=b.get('session','');seq=b.get('seq',0);require(isinstance(session,str) and len(session)<=100 and type(seq) is int and 0<=seq<2**53,'Некорректная последовательность');old=snapshot(c,'settings',{});seen=old.get('uiSequence',{});
                            if not session or seq>seen.get(session,-1):
                                seen[session]=seq;update_meta(c,uiState=value,uiSequence=dict(list(seen.items())[-32:]))
                            result=dict(ok=True)
                        elif path=='/api/settings':
                            owner=txt(b.get('owner'),160,True);cloud=txt(b.get('cloudFolder'),2000);remote=txt(b.get('cloudRemote'),2000);cloud_remote(remote);require(not cloud or Path(cloud).is_absolute(),'Укажите полный путь к папке резервных копий')
                            put(c,'owner',owner);old=snapshot(c,'settings',{})
                            changed_destination=(cloud,remote)!=(old.get('cloudFolder',''),old.get('cloudRemote',''))
                            reset=dict(cloudAt='',cloudPath='',cloudVerified=False,cloudPending=None,cloudVerifyAt='',cloudLastVerifiedAt='',cloudLastVerifiedPath='',cloudServerPath='',cloudChecksum='',cloudSize=0,cloudCheckError='',cloudRetentionWarning='',autoBackupFingerprint='',autoBackupRetry=False,autoBackupLocalPath='',autoBackupLocalFingerprint='',autoBackupLocalHash='') if changed_destination else {}
                            update_meta(c,cloudFolder=cloud,cloudRemote=remote,cloudStatus='Настройки сохранены' if changed_destination else old.get('cloudStatus','Облачная папка не настроена'),**reset)
                            # Finish SQLite write before taking a snapshot on a separate connection.
                            c.commit()
                            if cloud or remote:safe_sync_copy(c)
                            else:update_meta(c,cloudStatus='Облачная папка не настроена',cloudVerified=False,cloudPending=None)
                            result=dict(owner=owner,cloudFolder=cloud,cloudRemote=remote,**meta(c))
                        else:return self.error('Неизвестный маршрут',404)
                if path in ('/api/sync','/api/backup','/api/cloud/check','/api/settings') and hasattr(self.server,'cloud_wake'):self.server.cloud_wake.set()
                if path in ('/api/sync','/api/backup','/api/settings') and hasattr(self.server,'auto_wake'):self.server.auto_wake.set()
                return self.js(result)
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as e:return self.error(str(e))
        except Exception as e:traceback.print_exc();return self.error('Операция не завершена: '+str(e),500)
def verify_ui():
    manifest=json.loads((ROOT/'original-ui-sha256.json').read_text(encoding='utf-8'))
    require(len(manifest)==28,'Неполный манифест интерфейса сборки')
    local_digest=manifest.pop('__local_act_copy__.html',None)
    if local_digest is not None:
        candidates=[f for f in APP.glob('*.html') if f.name not in manifest and hashlib.sha256(f.read_bytes()).hexdigest()==local_digest]
        require(len(candidates)==1,'Не найден локальный шаблон акта');manifest[candidates[0].name]=local_digest
    for name,digest in manifest.items():
        f=APP/name
        require(f.is_file() and hashlib.sha256(ui_bytes(f)).hexdigest()==digest,'Файл интерфейса отсутствует или изменён: '+name)
    return manifest

def startup_probe(server, manifest):
    url=f'http://127.0.0.1:{server.server_port}'
    with urlopen(url+'/__build',timeout=5) as response:
        info=json.load(response)
        require(info['build']==BUILD and info['instance']==INSTANCE,'Ответил другой сервер, запуск остановлен')
    from urllib.parse import quote
    for name,digest in manifest.items():
        with urlopen(url+'/app/'+quote(name),timeout=5) as response:
            require(response.headers.get('X-Contracts-Build')==BUILD,'Ответил другой сервер')
            require(hashlib.sha256(response.read()).hexdigest()==digest,'Ошибка передачи файла: '+name)
            if name.endswith('.mjs'):require(response.headers.get_content_type()=='text/javascript','Неверный MIME: '+name)
            if name.endswith('.css'):require(response.headers.get_content_type()=='text/css','Неверный MIME: '+name)
    with urlopen(url+'/',timeout=5) as response:
        require(hashlib.sha256(response.read()).hexdigest()==manifest['index.html'],'Главная страница не соответствует сборке')
    with urlopen(url+'/api/bootstrap',timeout=5) as response:
        data=json.load(response)
        require(isinstance(data['records'],list) and isinstance(data['stock']['items'],list),'Ошибка структуры данных')
    return len(data['records'])

class GracefulServer(ThreadingHTTPServer):
    daemon_threads=False
    block_on_close=True
    def get_request(self):
        sock,addr=super().get_request();sock.settimeout(15);return sock,addr

def install_shutdown_handlers(stop,done):
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    if sys.platform!='win32':return
    # Hidden top-level window receives Windows session-end notifications even when Python runs without a console.
    def listen():
        try:
            import ctypes as C
            from ctypes import wintypes as W
            user=C.WinDLL('user32',use_last_error=True);kernel=C.WinDLL('kernel32',use_last_error=True)
            LRESULT=C.c_ssize_t;WNDPROC=C.WINFUNCTYPE(LRESULT,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
            class WC(C.Structure):
                _fields_=[('style',W.UINT),('lpfnWndProc',WNDPROC),('cbClsExtra',C.c_int),('cbWndExtra',C.c_int),('hInstance',W.HINSTANCE),('hIcon',W.HICON),('hCursor',W.HANDLE),('hbrBackground',W.HBRUSH),('lpszMenuName',W.LPCWSTR),('lpszClassName',W.LPCWSTR)]
            user.DefWindowProcW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];user.DefWindowProcW.restype=LRESULT
            kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];kernel.GetModuleHandleW.restype=W.HMODULE
            @WNDPROC
            def proc(hwnd,msg,wparam,lparam):
                if msg==0x0011:return 1 # WM_QUERYENDSESSION: do not veto OS shutdown
                if msg==0x0016 and wparam: # WM_ENDSESSION
                    stop.set();done.wait(3);return 0
                return user.DefWindowProcW(hwnd,msg,wparam,lparam)
            inst=kernel.GetModuleHandleW(None);name='ContractsIPShutdown_'+INSTANCE;wc=WC();wc.lpfnWndProc=proc;wc.hInstance=inst;wc.lpszClassName=name
            user.RegisterClassW.argtypes=[C.POINTER(WC)];user.RegisterClassW.restype=W.ATOM
            if not user.RegisterClassW(C.byref(wc)):raise C.WinError(C.get_last_error())
            user.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID];user.CreateWindowExW.restype=W.HWND
            hwnd=user.CreateWindowExW(0,name,name,0,0,0,0,0,None,None,inst,None)
            if not hwnd:raise C.WinError(C.get_last_error())
            user.GetMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT];user.GetMessageW.restype=C.c_int
            user.TranslateMessage.argtypes=[C.POINTER(W.MSG)];user.DispatchMessageW.argtypes=[C.POINTER(W.MSG)];user.DispatchMessageW.restype=LRESULT
            logging.info('Windows shutdown listener ready');msg=W.MSG()
            while user.GetMessageW(C.byref(msg),None,0,0)>0:user.TranslateMessage(C.byref(msg));user.DispatchMessageW(C.byref(msg))
        except Exception:logging.exception('Windows shutdown listener unavailable; SQLite transactions remain atomic')
    threading.Thread(target=listen,daemon=True,name='windows-session-end').start()

def main():
    global PORT
    logging.basicConfig(level=logging.INFO,filename=ROOT/'server.log',encoding='utf-8',format='%(asctime)s %(levelname)s %(message)s')
    try:
        recover_full_restore()
        manifest=verify_ui()
        if '--validate-update' in sys.argv:
            import updater
            require(updater.version()['build']==BUILD,'Номер версии сервера не совпадает')
            with closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as c:
                require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not c.execute('PRAGMA foreign_key_check').fetchall(),'База повреждена');require(model(raw_events(c))[2]==snapshot(c,'stock'),'Склад не совпадает с журналом');boot(c)
            a.checked_docs_root()
            probe=GracefulServer(('127.0.0.1',0),Handler);PORT=probe.server_port
            worker=threading.Thread(target=probe.serve_forever,daemon=True);worker.start()
            try:startup_probe(probe,manifest)
            finally:probe.shutdown();probe.server_close();worker.join()
            print('UPDATE VALIDATED',flush=True);return
        require(not (ROOT/'.updates'/'pending').exists(),'Подготовлено обновление. Запустите ContractsIP.exe')
        a.checked_docs_root()
        with connect() as c:
            require(c.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not c.execute('PRAGMA foreign_key_check').fetchall(),'База повреждена');require(model(raw_events(c))[2]==snapshot(c,'stock'),'Снимок склада не совпадает с журналом')
        # Bind first. Never open the browser if another process owns this port.
        try:server=GracefulServer(('127.0.0.1',PORT),Handler)
        except OSError as e:raise RuntimeError(f'PORT {PORT} is occupied or unavailable. Close the previous copy of this build. No browser opened. {e}') from e
        stop=threading.Event();done=threading.Event();server.lifecycle=BrowserLifecycle();server.lifecycle.set_background(launch_settings()['background']) if launch_settings()['background'] else None;install_shutdown_handlers(stop,done)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            count=startup_probe(server,manifest)
            if not any(p.name.startswith('База_'+today()) for p in BACKUPS.glob('База_*.sqlite3')):backup()
            url=f'http://127.0.0.1:{PORT}/'
            print(f'ContractsIP SQLite | {BUILD} | 28 original UI files verified | {count} contracts',flush=True)
            print('OPEN: '+url,flush=True);print('BROWSER CHECK: '+url+'__check',flush=True)
            logging.info('Startup verified %s; records=%s',BUILD,count)
            cloud_stop=threading.Event();server.cloud_wake=threading.Event();cloud_thread=threading.Thread(target=cloud_worker,args=(cloud_stop,server.cloud_wake),name='cloud-verify',daemon=True);cloud_thread.start()
            auto_stop=threading.Event();server.auto_wake=threading.Event();auto_thread=threading.Thread(target=auto_backup_worker,args=(auto_stop,server.cloud_wake,server.auto_wake),name='automatic-backup',daemon=True);auto_thread.start()
            if '--no-browser' not in sys.argv:webbrowser.open(url)
            while thread.is_alive() and not stop.wait(.25):
                if server.lifecycle.expire():
                    logging.info('Automatic shutdown: no browser tabs remain');break
        except KeyboardInterrupt:pass
        finally:
            if 'auto_stop' in locals():auto_stop.set();server.auto_wake.set();auto_thread.join()
            if 'cloud_stop' in locals():cloud_stop.set();server.cloud_wake.set();cloud_thread.join()
            server.shutdown();server.server_close()
            # Drain all request threads before the final snapshot; no network calls on shutdown.
            if 'auto_stop' in locals():
                try:auto_backup_tick(force=True)
                except Exception:logging.exception('Shutdown backup failed')
            done.set();logging.info("Graceful shutdown complete")
            if getattr(server,'update_restart',False):
                subprocess.Popen([str(ROOT/'ContractsIP.exe')],cwd=ROOT,creationflags=0x08000000)
    except Exception:
        logging.exception('Startup failed');raise
if __name__=='__main__':main()
