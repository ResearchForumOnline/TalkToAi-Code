"""Bounded real adapter training candidates. No downloads or live-model promotion."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

MAX_BASE_BYTES=512*1024*1024
MAX_DATA_BYTES=4*1024*1024
LIMITS={'max_active_jobs':1,'max_seconds':1800,'max_log_bytes':1024*1024}
DEPENDENCIES=('torch','transformers','peft','safetensors')


def stamp():return datetime.now(timezone.utc).isoformat()
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def atomic_json(path,data):
    path=Path(path);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
    try:
        for attempt in range(8):
            try:os.replace(temporary,path);break
            except PermissionError:
                if attempt==7:raise
                time.sleep(.01*(attempt+1))
    finally:
        if temporary.exists():temporary.unlink()
def read_json(path):
    for attempt in range(8):
        try:return json.loads(Path(path).read_text(encoding='utf-8'))
        except PermissionError:
            if attempt==7:raise
            time.sleep(.01*(attempt+1))
def integer(value,low,high,name):
    if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:raise ValueError(f'{name} must be {low}-{high}')
    return value

def inspected_file(value,suffix=None):
    path=Path(value).expanduser()
    if not path.is_absolute() or path.is_symlink():raise ValueError('Supply an absolute, non-symlink local file path')
    path=path.resolve()
    forbidden={'.env','id_rsa','id_ed25519','credentials.json','tokens.json'}
    if path.name.lower() in forbidden or path.name.lower().startswith('.env.') or any(part.lower() in ('.ssh','.aws','.gnupg') for part in path.parts):raise ValueError('Private credential paths are not training inputs')
    if not path.is_file() or (suffix and path.suffix.lower()!=suffix):raise ValueError('Required local file is missing or has the wrong type')
    return path


def preflight(request):
    if not isinstance(request,dict):raise ValueError('Training request must be an object')
    backend=request.get('backend','fixture')
    if backend=='fixture':
        return {'backend':'fixture','steps':integer(request.get('steps',250),1,2000,'steps'),
                'seed':integer(request.get('seed',20260927),0,2**31-1,'seed')}
    if backend!='lora':raise ValueError('Backend must be fixture or lora')
    if getattr(sys,'frozen',False):raise ValueError('HF LoRA requires a separate Python training environment; this packaged app supports the CPU fixture')
    missing=[name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]
    if missing:raise ValueError('Optional local training environment is not installed: '+', '.join(missing)+'. No packages or models were downloaded.')
    base=Path(request.get('base_model_path','')).expanduser()
    if not base.is_absolute() or base.is_symlink() or not base.is_dir():raise ValueError('Supply an absolute local Hugging Face model directory')
    base=base.resolve();config_path=inspected_file(str(base/'config.json'),'.json')
    config=read_json(config_path)
    if any(config.get(key,0) for key in ('num_experts','num_local_experts','n_routed_experts')):raise ValueError('This bounded CPU backend does not support expert/MoE bases')
    for keys,maximum in ((('num_hidden_layers','n_layer'),32),(('hidden_size','n_embd'),2048),(('vocab_size',),100000)):
        for key in keys:
            if key in config:integer(config[key],1,maximum,key)
    weights=sorted(base.glob('*.safetensors'))
    if not weights or any(p.is_symlink() for p in weights):raise ValueError('Local Safetensors base weights are required; pickle weights are not loaded')
    total=sum(p.stat().st_size for p in weights)
    if total>MAX_BASE_BYTES:raise ValueError('CPU training base exceeds 512 MiB limit; this host is not a 30B training machine')
    dataset=inspected_file(str(request.get('dataset_path','')),'.jsonl')
    if dataset.stat().st_size>MAX_DATA_BYTES:raise ValueError('Dataset exceeds 4 MiB limit')
    rows=load_text_rows(dataset)
    rate=request.get('learning_rate',0.001)
    if isinstance(rate,bool) or not isinstance(rate,(int,float)) or not math.isfinite(rate) or not 0<rate<=0.01:raise ValueError('learning_rate must be finite and between 0 and 0.01')
    return {'backend':'lora','base_model_path':str(base),'dataset_path':str(dataset),'dataset_sha256':digest(dataset),
            'base_files':{p.name:digest(p) for p in [config_path]+weights},'dataset_rows':len(rows),
            'steps':integer(request.get('steps',10),1,100,'steps'),'rank':integer(request.get('rank',4),1,8,'rank'),
            'max_length':integer(request.get('max_length',128),16,256,'max_length'),'learning_rate':float(rate)}


def load_text_rows(path):
    rows=[]
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():continue
            value=json.loads(line)
            if not isinstance(value,dict) or set(value)!={'text'} or not isinstance(value['text'],str) or not value['text'].strip() or len(value['text'])>4000:
                raise ValueError('Each reviewed JSONL row must contain only nonempty text of at most 4000 characters')
            rows.append(value['text'])
            if len(rows)>2000:raise ValueError('Dataset exceeds 2000 rows')
    if len(rows)<10:raise ValueError('At least 10 reviewed text rows are required for a held-out split')
    return rows


def check_cancel(folder):
    if (folder/'cancel.request').exists():raise InterruptedError('Training cancelled; candidate was not promoted')
def progress(folder,step,total):atomic_json(folder/'progress.json',{'step':step,'total_steps':total})
def hashed_json(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def train_fixture(folder,config):
    """Gradient descent on a rank-one matrix adapter; deliberately not an LLM."""
    rng=random.Random(config['seed']);base=[[0.0]*4 for _ in range(3)]
    a=[rng.gauss(0,.1) for _ in range(4)];b=[0.0]*3
    teacher_a=[.6,-.3,.8,.2];teacher_b=[.5,-.7,1.2]
    def samples(count):
        return [[rng.gauss(0,1) for _ in range(4)] for _ in range(count)]
    train=samples(48);heldout=samples(24)
    def predict(x,aa=a,bb=b):
        z=sum(v*w for v,w in zip(x,aa))
        return [sum(x[j]*base[i][j] for j in range(4))+bb[i]*z for i in range(3)]
    def target(x):return [v*sum(x[j]*teacher_a[j] for j in range(4)) for v in teacher_b]
    def loss(rows,aa=a,bb=b):
        return sum((actual-wanted)**2 for x in rows for actual,wanted in zip(predict(x,aa,bb),target(x)))/(len(rows)*3)
    base_hash=hashed_json(base);before=hashed_json({'a':a,'b':b})
    train_before=loss(train);heldout_before=loss(heldout)
    for step in range(config['steps']):
        check_cancel(folder);da=[0.0]*4;db=[0.0]*3
        for x in train:
            z=sum(x[j]*a[j] for j in range(4));pred=predict(x);wanted=target(x)
            g=[2*(pred[i]-wanted[i])/(len(train)*3) for i in range(3)]
            for i in range(3):db[i]+=g[i]*z
            for j in range(4):da[j]+=sum(g[i]*b[i] for i in range(3))*x[j]
        for j in range(4):a[j]-=.05*da[j]
        for i in range(3):b[i]-=.05*db[i]
        if not all(math.isfinite(v) for v in a+b):raise ValueError('Nonfinite training update')
        if (step+1)%10==0 or step+1==config['steps']:progress(folder,step+1,config['steps'])
    candidate=folder/'candidate';candidate.mkdir()
    adapter={'format':'openzero.synthetic-lora.v1','model_kind':'synthetic_linear_fixture_not_llm','lora_a':a,'lora_b':b,'base_sha256':base_hash}
    atomic_json(candidate/'adapter.json',adapter)
    loaded=read_json(candidate/'adapter.json')
    reload_equal=all(predict(x)==predict(x,loaded['lora_a'],loaded['lora_b']) for x in heldout)
    result={'model_kind':'synthetic_linear_fixture_not_llm','steps':config['steps'],'train_examples':48,'heldout_examples':24,
            'base_parameters':12,'trainable_adapter_parameters':7,'base_unchanged':base_hash==hashed_json(base),
            'adapter_changed':before!=hashed_json({'a':a,'b':b}),'train_loss_before':train_before,'train_loss_after':loss(train),
            'heldout_loss_before':heldout_before,'heldout_loss_after':loss(heldout),'reload_predictions_equal':reload_equal,
            'quality_improved':loss(heldout)<heldout_before,'promoted':False}
    if not result['base_unchanged'] or not reload_equal:raise ValueError('Candidate integrity/reload failed')
    return result


def train_lora(folder,config):
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['TOKENIZERS_PARALLELISM']='false'
    import torch
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
    from peft import LoraConfig, get_peft_model
    torch.set_num_threads(min(2,os.cpu_count() or 1));torch.manual_seed(20260927)
    base=Path(config['base_model_path']);dataset=Path(config['dataset_path'])
    if digest(dataset)!=config['dataset_sha256'] or any(digest(base/name)!=value for name,value in config['base_files'].items()):raise ValueError('Approved base/dataset changed before training')
    rows=load_text_rows(dataset);holdout_count=max(2,len(rows)//5);training=rows[:-holdout_count];heldout=rows[-holdout_count:]
    tokenizer=AutoTokenizer.from_pretrained(str(base),local_files_only=True,trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:raise ValueError('Tokenizer needs a local pad or EOS token')
        tokenizer.pad_token=tokenizer.eos_token
    architecture=AutoConfig.from_pretrained(str(base),local_files_only=True,trust_remote_code=False)
    with torch.device('meta'):
        skeleton=AutoModelForCausalLM.from_config(architecture,trust_remote_code=False)
    parameter_count=sum(p.numel() for p in skeleton.parameters());del skeleton
    if parameter_count>125000000:raise ValueError('Model exceeds bounded CPU backend limit before allocation')
    model=AutoModelForCausalLM.from_pretrained(str(base),local_files_only=True,trust_remote_code=False,use_safetensors=True,torch_dtype=torch.float32).to('cpu')
    if sum(p.numel() for p in model.parameters())>125000000:raise ValueError('Model exceeds bounded CPU backend limit of 125 million parameters')
    model=get_peft_model(model,LoraConfig(r=config['rank'],lora_alpha=config['rank']*2,lora_dropout=0.0,bias='none',task_type='CAUSAL_LM'))
    def adapter_hash():
        value=hashlib.sha256()
        for name,tensor in model.named_parameters():
            if tensor.requires_grad:value.update(name.encode());value.update(tensor.detach().cpu().contiguous().numpy().tobytes())
        return value.hexdigest()
    adapter_before=adapter_hash()
    def encoded(text):
        values=tokenizer(text,return_tensors='pt',truncation=True,max_length=config['max_length'])
        if values['input_ids'].shape[1]<2:raise ValueError('Training text must yield at least two tokens')
        return {**values,'labels':values['input_ids'].clone()}
    def evaluate():
        model.eval();values=[]
        with torch.no_grad():
            for text in heldout[:32]:
                check_cancel(folder);values.append(float(model(**encoded(text)).loss))
        return sum(values)/len(values)
    heldout_before=evaluate();optimizer=torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),lr=config['learning_rate'])
    losses=[]
    for step in range(config['steps']):
        check_cancel(folder);model.train();optimizer.zero_grad();loss=model(**encoded(training[step%len(training)])).loss
        if not torch.isfinite(loss):raise ValueError('Nonfinite model loss')
        loss.backward();torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.0);optimizer.step()
        losses.append(float(loss.detach()));progress(folder,step+1,config['steps'])
    heldout_after=evaluate();candidate=folder/'candidate';candidate.mkdir();model.save_pretrained(str(candidate),safe_serialization=True)
    base_unchanged=all(digest(base/name)==value for name,value in config['base_files'].items())
    if not base_unchanged:raise ValueError('Base artifacts changed during training')
    return {'model_kind':'huggingface_peft_lora','steps':config['steps'],'train_examples':len(training),'heldout_examples':min(32,len(heldout)),
            'trainable_adapter_parameters':sum(p.numel() for p in model.parameters() if p.requires_grad),'base_unchanged':base_unchanged,'adapter_changed':adapter_before!=adapter_hash(),
            'train_loss_first':losses[0],'train_loss_last':losses[-1],'heldout_loss_before':heldout_before,'heldout_loss_after':heldout_after,
            'quality_improved':heldout_after<heldout_before,'promoted':False,'dataset_sha256':config['dataset_sha256'],
            'base_files':config['base_files'],'reload_predictions_equal':None,
            'limitation':'Held-out loss is not assistant capability or safety certification. This adapter has not been imported into Ollama.'}


def worker(folder):
    folder=Path(folder);request=read_json(folder/'request.json');started=time.monotonic()
    try:
        check_cancel(folder)
        result=(train_fixture if request['backend']=='fixture' else train_lora)(folder,request)
        check_cancel(folder)
        result['elapsed_seconds']=round(time.monotonic()-started,3)
        result['artifacts']=[{'name':p.name,'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted((folder/'candidate').iterdir()) if p.is_file()]
        atomic_json(folder/'result.json',result)
        return 0
    except InterruptedError as error:atomic_json(folder/'error.json',{'cancelled':True,'error':str(error)});return 2
    except Exception as error:atomic_json(folder/'error.json',{'error':type(error).__name__+': '+str(error)[:600]});return 1


def acquire_slot(root):
    handle=(Path(root)/'.training.lock').open('a+b')
    if handle.tell()==0:handle.write(b'0');handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (OSError,IOError):
        handle.close();raise RuntimeError('Another training job owns this workbench; cancel or wait for it')
    return handle


class Workbench:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self._guard=threading.Lock();self._active=None
    def status(self):
        missing=[name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]
        return {'status':'ready','backends':{'fixture':{'available':True,'description':'Real tiny low-rank weight training on synthetic data; not an LLM'},
                'lora':{'available':not missing and not getattr(sys,'frozen',False),'missing_dependencies':missing,'limits':{'max_base_bytes':MAX_BASE_BYTES,'max_steps':100,'max_length':256,'max_parameters':125000000}}},
                'limits':LIMITS,'jobs':self.jobs()}
    def folder(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid training job id')
        path=self.root/identifier
        if path.is_symlink() or not path.is_dir():raise FileNotFoundError('Training job not found')
        return path
    def get(self,identifier):
        folder=self.folder(identifier);job=read_json(folder/'job.json')
        for name in ('progress','result'):
            if (folder/(name+'.json')).is_file():job[name]=read_json(folder/(name+'.json'))
        if (folder/'error.json').is_file():
            error=read_json(folder/'error.json');job['error']=error.get('error')
            if job['status'] in ('queued','running'):job['status']='cancelled' if error.get('cancelled') else 'failed'
        elif job.get('result') is not None and job['status'] in ('queued','running'):job['status']='completed'
        return job
    def jobs(self):
        items=[]
        for folder in sorted(self.root.iterdir(),key=lambda p:p.name,reverse=True):
            if re.fullmatch('[0-9a-f]{32}',folder.name) and not folder.is_symlink() and (folder/'job.json').is_file():
                try:items.append(self.get(folder.name))
                except (OSError,ValueError):continue
        return sorted(items,key=lambda item:item['created_at'],reverse=True)[:50]
    def start(self,request):
        config=preflight(request)
        with self._guard:
            if self._active is not None:raise RuntimeError('A training job is already active; cancel or wait for it')
            slot=acquire_slot(self.root)
            identifier=uuid.uuid4().hex;folder=self.root/identifier;folder.mkdir()
            job={'id':identifier,'backend':config['backend'],'status':'queued','created_at':stamp(),'progress':{'step':0,'total_steps':config['steps']},'result':None,'error':None}
            atomic_json(folder/'request.json',config);atomic_json(folder/'job.json',job);self._active=identifier
            threading.Thread(target=self._run,args=(identifier,slot),name='Training candidate '+identifier[:8],daemon=True).start()
            return dict(job)
    def _run(self,identifier,slot):
        folder=self.folder(identifier);job=read_json(folder/'job.json');process=None
        try:
            job.update(status='running',started_at=stamp());atomic_json(folder/'job.json',job)
            if getattr(sys,'frozen',False):
                # The installed desktop EXE is not a Python interpreter. Its
                # bounded stdlib fixture runs in this background thread.
                result=worker(folder)
                job['status']='completed' if result==0 else 'cancelled' if result==2 else 'failed'
                return
            with (folder/'worker.log').open('wb') as log:
                flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
                options={'pass_fds':(slot.fileno(),)} if os.name!='nt' else {}
                process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'worker','--job-dir',str(folder)],stdout=log,stderr=subprocess.STDOUT,creationflags=flags,**options)
                started=time.monotonic();cancelled=False
                while process.poll() is None:
                    if (folder/'cancel.request').exists() or time.monotonic()-started>LIMITS['max_seconds'] or (folder/'worker.log').stat().st_size>LIMITS['max_log_bytes']:
                        cancelled=True;process.terminate()
                        try:process.wait(timeout=3)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
                        break
                    time.sleep(.1)
                if cancelled or process.returncode==2:job['status']='cancelled'
                elif process.returncode==0 and (folder/'result.json').is_file():job['status']='completed'
                else:job.update(status='failed',error='Training worker failed; inspect its bounded job log')
        except Exception as error:job.update(status='failed',error=type(error).__name__+': '+str(error)[:300])
        finally:
            if process is not None and process.poll() is None:process.kill();process.wait(timeout=3)
            try:
                job['finished_at']=stamp();atomic_json(folder/'job.json',job)
            finally:
                slot.close()
                with self._guard:self._active=None
    def cancel(self,identifier):
        folder=self.folder(identifier);job=self.get(identifier)
        if job['status'] in ('queued','running'):(folder/'cancel.request').touch(exist_ok=True)
        return self.get(identifier)


def register_training_routes(app,authorized,work_root):
    """Root app supplies its existing authenticated-owner decision callback."""
    from flask import jsonify,request,send_file
    service=Workbench(work_root)
    def permitted():
        if not authorized():return jsonify({'error':'Owner authentication required'}),403
        return None
    def handle(function):
        denied=permitted()
        if denied:return denied
        try:return function()
        except FileNotFoundError as error:return jsonify({'error':str(error)}),404
        except (ValueError,TypeError) as error:return jsonify({'error':str(error)}),400
        except RuntimeError as error:return jsonify({'error':str(error)}),409
    def status():return handle(lambda:jsonify(service.status()))
    def jobs():
        return handle(lambda:(jsonify({'job':service.start(request.get_json(silent=True) or {})}),202) if request.method=='POST' else jsonify({'jobs':service.jobs()}))
    def detail(job_id):return handle(lambda:jsonify({'job':service.get(job_id)}))
    def cancel(job_id):return handle(lambda:jsonify({'job':service.cancel(job_id)}))
    def artifact(job_id,name):
        def export():
            job=service.get(job_id)
            if job['status']!='completed':raise ValueError('Only completed candidates can be exported')
            allowed={item['name']:item for item in (job.get('result') or {}).get('artifacts',[])}
            if name not in allowed:raise FileNotFoundError('Candidate artifact not found')
            path=service.folder(job_id)/'candidate'/name
            if path.is_symlink() or digest(path)!=allowed[name]['sha256']:raise ValueError('Candidate artifact changed after evaluation')
            return send_file(path,as_attachment=True,download_name=name)
        return handle(export)
    app.add_url_rule('/api/training/status','training_status',status,methods=['GET'])
    app.add_url_rule('/api/training/jobs','training_jobs',jobs,methods=['GET','POST'])
    app.add_url_rule('/api/training/jobs/<job_id>','training_detail',detail,methods=['GET'])
    app.add_url_rule('/api/training/jobs/<job_id>/cancel','training_cancel',cancel,methods=['POST'])
    app.add_url_rule('/api/training/jobs/<job_id>/artifacts/<name>','training_artifact',artifact,methods=['GET'])
    return service


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('status','fixture','lora','worker'))
    parser.add_argument('--root',default=str(Path.home()/'.talktoai-training'));parser.add_argument('--job-dir');parser.add_argument('--request')
    parser.add_argument('--steps',type=int,default=250);args=parser.parse_args()
    if args.action=='worker':return worker(args.job_dir)
    service=Workbench(args.root)
    if args.action=='status':print(json.dumps(service.status(),indent=2));return 0
    request=read_json(args.request) if args.action=='lora' and args.request else {'backend':args.action,'steps':args.steps}
    job=service.start(request);print(json.dumps({'job':job}))
    try:
        while True:
            current=service.get(job['id'])
            if current['status'] not in ('queued','running'):break
            time.sleep(.1)
    except KeyboardInterrupt:
        service.cancel(job['id']);print('Cancellation requested',file=sys.stderr)
        deadline=time.monotonic()+5
        while service.get(job['id'])['status'] in ('queued','running') and time.monotonic()<deadline:time.sleep(.1)
        return 130
    print(json.dumps(current,indent=2));return 0 if current['status']=='completed' else 1


if __name__=='__main__':raise SystemExit(main())
