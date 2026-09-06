"""Bounded original-catalog search across all currently available repo artifacts.

Run from repository root. Reads Git credential for GitHub only; strips it before
artifact redirects. Large ZIPs use range reads and no member above 16 MiB is
inflated. A preserved target is restored only on exact recorded SHA256 identity.
The emitted receipt explicitly lists every uninspected oversized member.
"""
import concurrent.futures, hashlib, io, json, subprocess, urllib.request, zipfile
from pathlib import Path
ROOT=Path.cwd(); OUT=ROOT/'data/audit/catalog_recovery_v3'; OUT.mkdir(exist_ok=True)
TARGETS={r['expected_sha256']:r['path'] for r in json.loads((ROOT/'data/audit/astra_price_level_v2/source_inventory.json').read_bytes())['entries'] if r['status']=='MISSING'}
BASE='https://api.github.com/repos/zxc28tarik/TOTAL-RASYO-HESAPLAYICI'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args): return None
class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        out=super().redirect_request(req,fp,code,msg,headers,newurl);out.remove_header('Authorization');return out

def api(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(BASE+path,headers={'Authorization':'Bearer '+TOKEN}),timeout=40))
class Ranges(io.RawIOBase):
    def __init__(self,url,size):self.url=url;self.size=size;self.pos=0;self.cache={}
    def seekable(self):return True
    def seek(self,n,whence=0):self.pos=n if whence==0 else self.pos+n if whence==1 else self.size+n;return self.pos
    def tell(self):return self.pos
    def read(self,n=-1):
        if n<0:n=self.size-self.pos
        n=min(n,self.size-self.pos)
        if not n:return b''
        key=(self.pos,n)
        if key not in self.cache:
            req=urllib.request.Request(self.url,headers={'Range':f'bytes={self.pos}-{self.pos+n-1}'})
            with urllib.request.urlopen(req,timeout=60) as resp:
                if resp.status!=206:raise ValueError('Range request not honored; no unbounded download')
                self.cache[key]=resp.read()
        self.pos+=n;return self.cache[key]

def scan_zip(z,prefix,row,depth=0):
    for info in z.infolist():
        if info.is_dir():continue
        label=prefix+info.filename; row['members'].append({'path':label,'bytes':info.file_size})
        if info.file_size>16*1024*1024:
            row['unhashed_members'].append(label);continue
        raw=z.read(info);digest=hashlib.sha256(raw).hexdigest();row['hashed_members']+=1
        if digest in TARGETS:
            dest=ROOT/TARGETS[digest];dest.write_bytes(raw);row['matches'].append({'path':TARGETS[digest],'member':label,'sha256':digest})
        if depth<2 and zipfile.is_zipfile(io.BytesIO(raw)):
            with zipfile.ZipFile(io.BytesIO(raw)) as nested:scan_zip(nested,label+'!/',row,depth+1)

def work(a):
    row={'artifact_id':a['id'],'name':a['name'],'bytes':a['size_in_bytes'],'run_id':a['workflow_run']['id'],'head_sha':a['workflow_run']['head_sha'],'members':[],'hashed_members':0,'unhashed_members':[],'matches':[]}
    try:
        req=urllib.request.Request(BASE+f"/actions/artifacts/{a['id']}/zip",headers={'Authorization':'Bearer '+TOKEN})
        if a['size_in_bytes']>32*1024*1024:
            try:urllib.request.build_opener(NoRedirect).open(req,timeout=40)
            except urllib.error.HTTPError as exc:
                if exc.code!=302:raise
                location=exc.headers['Location']
            with zipfile.ZipFile(Ranges(location,a['size_in_bytes'])) as z:scan_zip(z,'',row)
            row['method']='REMOTE_ZIP_CENTRAL_DIRECTORY_AND_BOUNDED_MEMBERS'
        else:
            raw=urllib.request.build_opener(SafeRedirect).open(req,timeout=60).read()
            row['artifact_sha256']=hashlib.sha256(raw).hexdigest()
            (ROOT/'private'/f"search-artifact-{a['id']}.zip").write_bytes(raw)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:scan_zip(z,'',row)
            row['method']='FULL_ZIP_BYTES_AND_BOUNDED_NESTED_MEMBERS'
        row['status']='MATCH' if row['matches'] else 'NO_MATCH_IN_INSPECTED_CONTENT'
    except Exception as exc:row['status']='SEARCH_ERROR';row['error']=type(exc).__name__+': '+str(exc).split('?')[0]
    print(a['id'],row['status'],len(row['members']),flush=True)
    return row
def main():
    global TOKEN
    (ROOT / "private").mkdir(exist_ok=True)
    cred=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,check=True)
    TOKEN=dict(x.split('=',1) for x in cred.stdout.splitlines() if '=' in x)['password']
    artifacts=[];page=1
    while True:
        payload=api(f'/actions/artifacts?per_page=100&page={page}');artifacts+=payload['artifacts']
        if len(artifacts)>=payload['total_count'] or not payload['artifacts']:break
        page+=1
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(work,artifacts))
    report={'contract':'CATALOG_ALL_AVAILABLE_ARTIFACT_CONTENT_SEARCH_V1','artifacts_total':len(artifacts),'member_hash_limit_bytes':16*1024*1024,'nested_zip_depth':2,'artifacts':rows}
    (OUT/'actions_artifact_contents.json').write_bytes((json.dumps(report,indent=2,sort_keys=True)+'\n').encode())
    print('FINISHED',len(rows),'matches',sum(len(r['matches']) for r in rows),flush=True)

if __name__ == "__main__":
    main()
