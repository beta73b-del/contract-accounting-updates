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
PORT=int(os.environ.get("CONTRACTSIP_PORT", "8877")); BUILD="sqlite-original-ui-20261126-build-022"; NONCE=secrets.token_hex(32); INSTANCE=secrets.token_hex(16); LOCK=threading.RLock()

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
    return dict(backupAt=local_at,backupPath=str(last) if last else '',backupWarning=warning,cloudStatus=status,cloudAt=cloud_at,cloudPath=cloud_path,cloudVerified=settings.get('cloudVerified') is True,cloudVerifyAt=settings.get('cloudVerifyAt',''),cloudLastVerifiedAt=settings.get('cloudLastVerifiedAt',''),cloudLastVerifiedPath=settings.get('cloudLastVerifiedPath',''),cloudServerPath=settings.get('cloudServerPath',''),cloudChecksum=settings.get('cloudChecksum',''),cloudSize=settings.get('cloudSize',0),cloudPending=bool(settings.get('cloudPending')),cloudCheckError=settings.get('cloudCheckError',''))
def boot(c):
    settings=snapshot(c,'settings',{});return dict(owner=snapshot(c,'owner'),records=records(c),stock=snapshot(c,'stock'),finance=snapshot(c,'finance'),notes=snapshot(c,'notes'),cloudFolder=settings.get('cloudFolder',''),defaultCloudFolder='',cloudRemote=settings.get('cloudRemote',''),dbPath=str(DB),nonce=NONCE,version=int(BUILD.rsplit('-',1)[1]),launch=launch_settings(),browsers=detect_browsers(),port=PORT,uiState=settings.get('uiState',{}),**meta(c))
def update_meta(c,**values):
    settings=snapshot(c,'settings',{});settings.update(values);put(c,'settings',settings);return settings
def changed(c):return dict(records=records(c),stock=snapshot(c,'stock'),finance=snapshot(c,'finance'),**meta(c))
def cloud_time():return datetime.now(MSK).strftime('%d.%m.%Y %H:%M:%S')
def cloud_remote(value):
    if not value:return ''
    require(re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_ -]{0,79}:[^:\\\x00-\x1f]*',value) is not None,'Укажите настроенный remote rclone: например gdrive:ChatGPT/Контракты_ИП')
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
    if not exe:raise ValueError('rclone не найден в PATH. Установите rclone и настройте Google Drive.')
    if stop.is_set():raise InterruptedError('Завершение работы')
    # No shell, no credentials in database, no raw stderr (may contain sensitive paths/tokens).
    with subprocess.Popen([exe,*args,'--contimeout','10s','--timeout','30s','--retries','1','--low-level-retries','1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=0x08000000 if sys.platform=='win32' else 0) as proc:
        deadline=time.monotonic()+120
        while True:
            try:out,_=proc.communicate(timeout=.5);break
            except subprocess.TimeoutExpired:
                if stop.is_set() or time.monotonic()>=deadline:
                    proc.kill();proc.communicate();raise InterruptedError('Завершение работы') if stop.is_set() else ValueError('Истекло время ожидания rclone')
        require(proc.returncode==0,'rclone: операция '+args[0]+' не выполнена (код '+str(proc.returncode)+'). Проверьте интернет и авторизацию Google Drive.')
        return out.decode('utf-8-sig')

def verify_cloud_file(job,stop,run=rclone_run):
    remote=cloud_remote(job['remote']);name=remote.split(':',1)[0]
    remotes=run(['listremotes','--long'],stop)
    require(any(re.fullmatch(re.escape(name)+r':\s+drive(?:\s+.*)?',line.strip()) for line in remotes.splitlines()),'Указанный remote не является настроенным Google Drive (drive)')
    path=Path(job['path']);require(path.is_file(),'Локальный архив не найден; создайте новую копию')
    size,md5=cloud_hash(path);require((size,md5)==(job['size'],job['md5']),'Локальный архив изменён; создайте новую копию')
    target=remote+'ContractsIP-SQLite-backups/'+path.name
    if not job.get('verifyOnly'):run(['copyto',str(path),target,'--immutable','--checksum'],stop)
    info=json.loads(run(['lsjson',target,'--stat','--hash','--hash-type','MD5'],stop))
    require(isinstance(info,dict) and info.get('IsDir') is False and info.get('Name')==path.name,'Файл на сервере Google Drive не найден')
    require(type(info.get('Size')) is int and info['Size']==size,'Размер облачного архива не совпадает')
    hashes=info.get('Hashes');actual=hashes.get('MD5','') if isinstance(hashes,dict) else ''
    require(isinstance(actual,str) and actual.lower()==md5,'Контрольная сумма MD5 облачного архива отсутствует или не совпадает')
    require(cloud_hash(path)==(size,md5),'Локальный архив изменился при загрузке')
    return target

def sync_copy(c):
    settings=snapshot(c,'settings',{});raw=settings.get('cloudFolder','');remote=cloud_remote(settings.get('cloudRemote',''))
    if not raw and not remote:return update_meta(c,cloudStatus='Облачная папка не настроена',cloudVerified=False,cloudPending=None)
    if remote:
        # Direct rclone transport: do not race Drive Desktop writing the same remote file.
        dest=BACKUPS/'cloud-outbox';dest.mkdir(parents=True,exist_ok=True)
    else:
        p=Path(raw).resolve();require(p.is_dir(),'Папка синхронизации не найдена');require(all(not p.is_relative_to(x.resolve()) and not x.resolve().is_relative_to(p) for x in (ROOT,DOCS)),'Выберите отдельную папку синхронизации, вне приложения и документов');dest=p/'ContractsIP-SQLite-backups';dest.mkdir(exist_ok=True)
    out=dest/('ContractsIP-'+datetime.now(MSK).strftime('%Y%m%d-%H%M%S-%f')+'.zip');tmp=out.with_suffix('.tmp');backup_file=backup()
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
            z.write(backup_file,'contracts.sqlite3')
            for f in DOCS.rglob('*'):
                if f.is_file() and f.name.casefold() not in ('desktop.ini','thumbs.db','ehthumbs.db'):z.write(f,'documents/'+f.relative_to(DOCS).as_posix())
        with zipfile.ZipFile(tmp) as z:require(z.testzip() is None,'Ошибка проверки облачной копии')
        os.replace(tmp,out)
    finally:tmp.unlink(missing_ok=True)
    size,md5=cloud_hash(out);job=dict(id=uid(),remote=remote,path=str(out),size=size,md5=md5) if remote else None
    return update_meta(c,cloudStatus='Копия сохранена; ожидается загрузка и проверка на сервере Google Drive' if remote else 'Копия сохранена в локальной папке синхронизации; для проверки сервера настройте rclone',cloudAt=cloud_time(),cloudPath=str(out),cloudVerified=False,cloudVerifyAt='',cloudServerPath='',cloudChecksum=md5,cloudSize=size,cloudPending=job,cloudCheckError='')

def queue_cloud_check(c):
    cfg=snapshot(c,'settings',{});remote=cloud_remote(cfg.get('cloudRemote',''));require(remote,'Настройте remote rclone в настройках')
    path=cfg.get('cloudPath','');md5=cfg.get('cloudChecksum','');size=cfg.get('cloudSize');require(path and re.fullmatch('[0-9a-f]{32}',md5) and type(size) is int and size>0,'Сначала создайте облачную копию')
    job=dict(id=uid(),remote=remote,path=path,size=size,md5=md5,verifyOnly=bool(cfg.get('cloudVerified') or cfg.get('cloudServerPath')))
    update_meta(c,cloudPending=job,cloudVerified=False,cloudStatus='Ожидается проверка на сервере Google Drive',cloudCheckError='')

def cloud_attempt(stop,run=rclone_run):
    with LOCK,connect() as c:job=snapshot(c,'settings',{}).get('cloudPending')
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
        else:update_meta(c,cloudVerified=True,cloudVerifyAt=at,cloudLastVerifiedAt=at,cloudLastVerifiedPath=target,cloudServerPath=target,cloudStatus='Копия подтверждена на сервере Google Drive (размер и MD5 совпадают)',cloudCheckError='',cloudPending=None)
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
CALCULATOR_SERVED_HASH='f07b35264ab4160d8ce354dde5df1714836c3454d3045e1ebc8e4efa093d98b9'
CALCULATOR_REPLACEMENTS=[(b'new Date().getFullYear()', b"Number(new Intl.DateTimeFormat('en',{timeZone:'Europe/Moscow',year:'numeric'}).format(new Date()))"), (b"new Date().toLocaleDateString('ru-RU')", b"new Date().toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})")]
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
                return self.js(geo_search(b) if path.endswith('/search') else geo_timezone(b))
            with LOCK:
                if getattr(self.server,'update_restart',False):return self.error('Выполняется обновление',503)
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
                if path=='/api/shutdown':require(b.get('confirmed') is True,'Подтвердите завершение');self.js(dict(ok=True,message='Завершение программы'));threading.Thread(target=self.server.shutdown,daemon=True).start();return
                # Acquire exclusive write transaction before reading a revision. Context manager rolls back all errors.
                with connect() as c:
                    # Read-only auxiliary and backup operations must not hold a write transaction while copying DB.
                    if path=='/api/transfer-package':result=a.transfer_package(c,b)
                    elif path=='/api/backup':
                        p=backup();update_meta(c,backupAt=datetime.now(MSK).strftime('%d.%m.%Y %H:%M:%S'),backupWarning='');result=meta(c)
                    elif path=='/api/sync':
                        try:sync_copy(c)
                        except Exception as e:update_meta(c,cloudStatus='ОШИБКА синхронизации: '+str(e),cloudVerified=False,cloudPending=None)
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
                            owner=txt(b.get('owner'),160,True);cloud=txt(b.get('cloudFolder'),2000);remote=txt(b.get('cloudRemote'),2000);cloud_remote(remote);require(not cloud or Path(cloud).is_absolute(),'Укажите полный путь к папке синхронизации');put(c,'owner',owner);old=snapshot(c,'settings',{});reset=dict(cloudAt='',cloudPath='',cloudVerified=False,cloudPending=None,cloudVerifyAt='',cloudLastVerifiedAt='',cloudLastVerifiedPath='',cloudServerPath='',cloudChecksum='',cloudSize=0,cloudCheckError='') if (cloud,remote)!=(old.get('cloudFolder',''),old.get('cloudRemote','')) else {};settings=update_meta(c,cloudFolder=cloud,cloudRemote=remote,cloudStatus='Настройки сохранены; выполните синхронизацию' if reset else old.get('cloudStatus','Облако не настроено'),**reset);result=dict(owner=owner,cloudFolder=cloud,cloudRemote=remote,**meta(c))
                        else:return self.error('Неизвестный маршрут',404)
                if path in ('/api/sync','/api/cloud/check','/api/settings') and hasattr(self.server,'cloud_wake'):self.server.cloud_wake.set()
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
            if '--no-browser' not in sys.argv:webbrowser.open(url)
            while thread.is_alive() and not stop.wait(.25):
                if server.lifecycle.expire():
                    logging.info('Automatic shutdown: no browser tabs remain');break
        except KeyboardInterrupt:pass
        finally:
            if 'cloud_stop' in locals():cloud_stop.set();server.cloud_wake.set();cloud_thread.join()
            server.shutdown();server.server_close();done.set();logging.info("Graceful shutdown complete")
            if getattr(server,'update_restart',False):
                subprocess.Popen([str(ROOT/'ContractsIP.exe')],cwd=ROOT,creationflags=0x08000000)
    except Exception:
        logging.exception('Startup failed');raise
if __name__=='__main__':main()
