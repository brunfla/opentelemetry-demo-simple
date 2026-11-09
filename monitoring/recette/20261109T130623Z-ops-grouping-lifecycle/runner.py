import copy, hashlib, json, os, pathlib, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from urllib import request
import yaml

ROOT=pathlib.Path('/home/bruno/code/opentelemetry-demo-simple')
LAB=ROOT.parent/'incident-workflow-lab'
RUN=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUT=ROOT/'monitoring'/'recette'/(RUN+'-ops-grouping-lifecycle')
OUT.mkdir(parents=True)
(OUT/'notifications').mkdir()
AM='http://127.0.0.1:19094'
DB=['docker','exec','-i','incident-workflow-lab-postgres-1','psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','n8n']
def now():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def save(name,data):
 p=OUT/name
 p.write_text(data if isinstance(data,str) else json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def sql(query,db='incident_lab'):
 return subprocess.check_output(DB+['-d',db],input=query,text=True).strip()
def query(query_text,db='incident_lab'):return json.loads(sql(query_text,db))
def http(url,body=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['X-API-Key']=token
 req=request.Request(url,data=body,headers=headers)
 start=now()
 with request.urlopen(req,timeout=20) as response:
  data=response.read();status=response.status
 return {'started_at':start,'finished_at':now(),'http_status':status,'body':json.loads(data) if data else None}
def wait_for(fn,timeout=90):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  result=fn()
  if result:return result
  time.sleep(1)
 raise AssertionError('Timeout waiting for expected evidence')
SNAPSHOT="""
WITH g AS (SELECT * FROM incident.alertmanager_groups WHERE source='otel-demo' AND receiver='n8n-incidents' AND group_labels->>'alertname'='OpsGroupingLifecycleCheck'),
e AS (SELECT x.* FROM incident.alertmanager_episodes x JOIN g USING(group_id)),
o AS (SELECT x.* FROM incident.investigation_outbox x WHERE incident_id IN (SELECT incident_id FROM e))
SELECT jsonb_build_object(
'observed_at',clock_timestamp(),
'groups',coalesce((SELECT jsonb_agg(jsonb_build_object('group_id',group_id,'source',source,'receiver',receiver,'group_key',group_key,'group_labels',group_labels)) FROM g),'[]'::jsonb),
'episodes',coalesce((SELECT jsonb_agg(jsonb_build_object('episode_id',episode_id,'group_id',group_id,'state',state,'disposition',disposition,'incident_id',incident_id,'resolved_at',resolved_at)) FROM e),'[]'::jsonb),
'members',coalesce((SELECT jsonb_agg(jsonb_build_object('alert_key',alert_key,'episode_id',episode_id,'labels',payload->'labels','status',payload->>'status','startsAt',payload->>'startsAt','endsAt',payload->>'endsAt')) FROM incident.alertmanager_members WHERE episode_id IN (SELECT episode_id FROM e)),'[]'::jsonb),
'investigations',coalesce((SELECT jsonb_agg(jsonb_build_object('incident_id',incident_id,'investigation_id',investigation_id,'status',status)) FROM incident.tickets WHERE incident_id IN (SELECT incident_id FROM e)),'[]'::jsonb),
'requests',coalesce((SELECT jsonb_agg(jsonb_build_object('request_id',request_id,'incident_id',incident_id,'state',state,'test_label',context #>> '{alert,labels,test}','first_dispatch_at',first_dispatch_at,'job_id',job_id,'lease_id',lease_id,'failures',failures,'context_digest',context_digest,'initial_context_revision',context #> '{incident_context,revision}')) FROM o),'[]'::jsonb),
'contexts',coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.revision) FROM incident.context_revisions c WHERE incident_id IN (SELECT incident_id FROM e)),'[]'::jsonb),
'episode_receipts',coalesce((SELECT jsonb_agg(to_jsonb(er) ORDER BY er.receipt_id) FROM incident.alertmanager_episode_receipts er WHERE episode_id IN (SELECT episode_id FROM e)),'[]'::jsonb),
'receipts',coalesce((SELECT jsonb_agg(jsonb_build_object('receipt_id',r.receipt_id,'received_at',r.received_at,'execution_id',r.execution_id,'status',r.payload->>'status','payload',r.payload) ORDER BY r.received_at) FROM incident.receipts r JOIN incident.alertmanager_notifications n USING(receipt_id) WHERE n.group_id IN (SELECT group_id FROM g)),'[]'::jsonb));
"""
JOB_CODE="""import sys,os,json,sqlite3,pathlib
wanted=json.load(sys.stdin)
p=pathlib.Path(os.environ.get('WORKER_DATA','/work/jobs'))/'jobs.sqlite3'
con=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
rows=con.execute('SELECT id,request_id,data FROM jobs').fetchall()
selected=[]
for job_id,rid,data in rows:
 item=json.loads(data)
 if rid in wanted['requests'] or item.get('incident_id') in wanted['investigations']:
  selected.append({'job_id':job_id,'request_id':rid,'status':item.get('status'),'finished':item.get('finished')})
print(json.dumps({'database':str(p),'jobs':selected}))
"""
def snapshot():
 s=query(SNAPSHOT)
 wanted={'requests':[o['request_id'] for o in s['requests']],'investigations':[i['incident_id'] for i in s['investigations']]}
 s['worker']=json.loads(subprocess.check_output(['docker','exec','-i','incident-workflow-lab-agent-worker-1','python','-c',JOB_CODE],input=json.dumps(wanted),text=True))
 s['counts']={k:len(s[k]) for k in ['groups','episodes','members','investigations','requests','receipts']}
 s['counts']['agent_jobs']=len(s['worker']['jobs'])
 return s

def identities(s):
 return {key:sorted(x[field] for x in s[key]) for key,field in [('groups','group_id'),('episodes','episode_id'),('investigations','investigation_id'),('requests','request_id')]}
def member_states(s):return {m['labels']['pod']:m['status'] for m in s['members']}
def state_map(alerts):return {a['labels']['pod']:a['status'] for a in alerts}
def check(s,expected,state,receipts):
 assert s['counts']==dict(groups=1,episodes=1,members=len(expected),investigations=1,requests=1,receipts=receipts,agent_jobs=0),s['counts']
 assert member_states(s)==expected,member_states(s)
 assert s['episodes'][0]['state']==state,s['episodes']
 assert (s['episodes'][0]['resolved_at'] is not None)==(state=='RESOLVED')
 assert all(m['labels']['test']=='true' and m['labels']['service_name']=='quote' for m in s['members'])
 assert all(o['test_label']=='true' and o['state']=='PENDING' and o['first_dispatch_at'] is None and o['job_id'] is None and o['lease_id'] is None for o in s['requests'])
 assert len(s['episode_receipts'])==receipts
 for r in s['receipts']:
  p=r['payload']
  assert p['groupKey']==s['groups'][0]['group_key'] and p['receiver']=='n8n-incidents' and p['version']=='4' and p['truncatedAlerts']==0
  assert all(a['labels']['test']=='true' for a in p['alerts'])
 latest=s['contexts'][-1]['document']['alertmanager_group']
 assert latest['member_count']==len(expected) and latest['state']==state
 assert state_map(latest['members'])==expected
 assert latest['episode_id']==s['episodes'][0]['episode_id']
def check_history(previous,current):
 assert identities(previous)==identities(current)
 # Initial request is immutable; evolving context is appended separately.
 assert previous['requests']==current['requests']
 assert current['contexts'][:len(previous['contexts'])]==previous['contexts']
 assert current['contexts'][-1]['revision']>previous['contexts'][-1]['revision']
 assert current['receipts'][:len(previous['receipts'])]==previous['receipts']
 old={m['labels']['pod']:m for m in previous['members']}
 new={m['labels']['pod']:m for m in current['members']}
 for pod,m in old.items():
  assert new[pod]['alert_key']==m['alert_key'] and new[pod]['startsAt']==m['startsAt']
  if m['status']=='resolved':assert new[pod]==m

def inject(stage,alerts):
 save(stage+'-injected-alerts.json',alerts)
 response=http(AM+'/api/v2/alerts',json.dumps(alerts).encode())
 save(stage+'-injection-response.json',response)
 assert response['http_status']==200
 return response

def observe(stage,expected,state,receipt_count,previous=None):
 def matching():
  s=snapshot()
  if len(s['receipts'])>=receipt_count:return s
  return None
 s=wait_for(matching,80)
 save(stage+'-snapshot.json',s)
 for idx,r in enumerate(s['receipts'],1):
  save(f'notifications/{idx:02d}-webhook.json',r['payload'])
  save(f'notifications/{idx:02d}-reception.json',{k:v for k,v in r.items() if k!='payload'})
 check(s,expected,state,receipt_count)
 if previous is not None:check_history(previous,s)
 print('STAGE_PASS',stage,json.dumps({'counts':s['counts'],'member_states':member_states(s),'episode':state,'context_revision':s['contexts'][-1]['revision']}),flush=True)
 return s

save('runner.py',pathlib.Path(__file__).read_text())
status=http(AM+'/api/v2/status')['body']; config=yaml.safe_load(status['config']['original'])
route=config['route']; child=next(r for r in route['routes'] if r['receiver']=='n8n-incidents')
group_by=child.get('group_by',route['group_by'])
assert group_by==['cluster','namespace','alertname','service_name'] and 'pod' not in group_by
assert child['group_wait']=='10s' and child['group_interval']=='30s'
assert child['matchers']==['alertname!="Watchdog"','namespace="otel-demo"']
assert next(r for r in config['receivers'] if r['name']=='n8n-incidents')['webhook_configs'][0]['send_resolved']
save('loaded-alertmanager.yaml',yaml.safe_dump(config,sort_keys=False))
save('effective-route.json',{'observed_at':now(),'version':status['versionInfo'],'route':child,'inherited_group_by':group_by,'root_route':route})
gate=sql("SELECT pg_get_functiondef('incident.claim_investigation()'::regprocedure);")
assert "coalesce(o.context #>> '{alert,labels,test}', '') <> 'true'" in gate
save('dispatcher-function.sql',gate+'\n')
workflow=query("""SELECT json_build_object('active',active,'version',"activeVersionId",'claim_query',(SELECT n->'parameters'->>'query' FROM jsonb_array_elements(nodes::jsonb) n WHERE n->>'name'='Claim persisted request'),'schedule',(SELECT n->'parameters' FROM jsonb_array_elements(nodes::jsonb) n WHERE n->>'type'='n8n-nodes-base.scheduleTrigger')) FROM workflow_entity WHERE id='labOtelDispatch001';""",'n8n')
assert workflow['active'] and 'incident.claim_investigation()' in workflow['claim_query']
save('dispatcher-workflow.json',workflow)
save('snapshot-query.sql',SNAPSHOT);save('worker-query.py',JOB_CODE)
baseline=snapshot();save('00-baseline.json',baseline)
assert baseline['counts']==dict(groups=0,episodes=0,members=0,investigations=0,requests=0,receipts=0,agent_jobs=0),'Existing test group: inspect before another run'
labels=dict(alertname='OpsGroupingLifecycleCheck',cluster='otel-demo',namespace='otel-demo',service_name='quote',environment='local',test='true')
def new_alert(pod):
 return dict(labels=dict(labels,pod=pod),startsAt=now(),endsAt=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat().replace('+00:00','Z'),annotations={'summary':'Synthetic group member lifecycle test; model dispatch excluded','recipe_run':RUN})
A=new_alert('grouping-lifecycle-a');B=new_alert('grouping-lifecycle-b');C=None
stage_name='01-ab-active';injected=False;finished=False;stages=[]
started_at=now()
try:
 injected=True
 inject(stage_name,[A,B])
 s1=observe(stage_name,{'grouping-lifecycle-a':'firing','grouping-lifecycle-b':'firing'},'OPEN',1);stages.append(s1)
 assert state_map(s1['receipts'][-1]['payload']['alerts'])==member_states(s1)
 stage_name='02-a-resolved-b-active'
 A['endsAt']=now()
 inject(stage_name,[A,B])
 s2=observe(stage_name,{'grouping-lifecycle-a':'resolved','grouping-lifecycle-b':'firing'},'OPEN',2,s1);stages.append(s2)
 mixed=s2['receipts'][-1]['payload']
 assert mixed['status']=='firing' and state_map(mixed['alerts'])==member_states(s2)
 stage_name='03-c-added'
 C=new_alert('grouping-lifecycle-c')
 # A has already resolved. Submit only B and C; retain original startsAt for B.
 inject(stage_name,[B,C])
 s3=observe(stage_name,{'grouping-lifecycle-a':'resolved','grouping-lifecycle-b':'firing','grouping-lifecycle-c':'firing'},'OPEN',3,s2);stages.append(s3)
 added=s3['receipts'][-1]['payload'];assert added['status']=='firing'
 assert state_map(added['alerts'])['grouping-lifecycle-c']=='firing'
 assert s3['contexts'][-1]['document']['alertmanager_group']['member_count']==3
 stage_name='04-all-resolved'
 B['endsAt']=now();C['endsAt']=now()
 inject(stage_name,[B,C])
 s4=observe(stage_name,{'grouping-lifecycle-a':'resolved','grouping-lifecycle-b':'resolved','grouping-lifecycle-c':'resolved'},'RESOLVED',4,s3);stages.append(s4)
 resolved=s4['receipts'][-1]['payload'];assert resolved['status']=='resolved' and all(a['status']=='resolved' for a in resolved['alerts'])
 assert all(identities(s)==identities(s1) for s in stages)
 assert s4['investigations'][0]['status']=='INVESTIGATION_PENDING'
 ticks=query("SELECT coalesce(jsonb_agg(jsonb_build_object('id',id,'started_at',\"startedAt\",'stopped_at',\"stoppedAt\",'status',status) ORDER BY \"startedAt\"),'[]'::jsonb) FROM execution_entity WHERE \"workflowId\"='labOtelDispatch001' AND \"startedAt\">='"+started_at+"'::timestamptz;",'n8n')
 save('dispatcher-executions.json',ticks)
 assert len([t for t in ticks if t['status']=='success'])>=2,ticks
 summary=[{'step':i+1,'received_at':s['receipts'][-1]['received_at'],'counts':s['counts'],'states':member_states(s),'episode_state':s['episodes'][0]['state'],'context_revision':s['contexts'][-1]['revision']} for i,s in enumerate(stages)]
 save('result.json',{'result':'PASS','started_at':started_at,'finished_at':now(),'identities':identities(s4),'stages':summary,'dispatcher_executions':len(ticks),'initial_request_unchanged':True,'archived_receipts_and_contexts_preserved':True,'running_agent_update_tested':False})
 lines=['Recette OpsGroupingLifecycleCheck — PASS',f'Run UTC : {RUN}',f'Début : {started_at}',f'Fin : {now()}','',
 'Chaîne réelle : Alertmanager → webhook n8n → registre SQL du laboratoire.',
 'Route n8n-incidents ; group_by chargé = cluster, namespace, alertname, service_name ; pod exclu.',
 'Trois cibles synthétiques : grouping-lifecycle-a/b/c ; service_name=quote, environment=local, test=true.',
 'Chaque étape attend sa notification réelle et sa persistance avant la suivante. Aucun groupKey fabriqué, aucun POST direct au webhook, aucune écriture SQL directe.',
 '', 'Étape | Membres connus | États A/B/C | Épisode | INV/demande | Jobs | Révision contexte | Réception UTC',
 *[f"{i+1} | {len(s['members'])} | {','.join(member_states(s).get('grouping-lifecycle-'+p,'absent') for p in ['a','b','c'])} | {s['episodes'][0]['state']} | 1/1 | 0 | {s['contexts'][-1]['revision']} | {s['receipts'][-1]['received_at']}" for i,s in enumerate(stages)],
 '', 'Identifiants stables :',json.dumps(identities(s4),ensure_ascii=False,indent=2),'',
 'Étape 2 : notification de groupe firing contenant A resolved et B firing. Épisode toujours OPEN ; resolved_at nul.',
 'Étape 3 : C apparaît dans une nouvelle révision de contexte avec trois membres connus ; A reste résolue. Aucun nouveau request_id ; contexte initial de la demande et context_digest inchangés.',
 'Étape 4 : notification resolved reçue, trois membres connus résolus, épisode RESOLVED selon la source. Les quatre réceptions et toutes les anciennes révisions de contexte sont conservées à l’identique. Les identités des membres précédents et leurs startsAt restent stables.',
 f'{len(ticks)} passages du dispatcher observés pendant la recette. Aucun job correspondant dans SQLite ; demande PENDING, first_dispatch_at/job_id/lease_id nuls à chaque étape.',
 'Les alertes synthétiques sont toutes résolues ; les archives et la demande sont conservées. Aucun changement de configuration ou de code applicatif.',
 'Les webhooks sont les valeurs JSON intégrales archivées par le récepteur. JSONB ne conserve pas les octets HTTP originaux ni leur mise en forme.',
 'Cette recette ne valide pas la prise en compte des mises à jour par un agent en cours d’exécution, ni la qualification/résolution d’un incident métier.']
 save('report.txt','\n'.join(lines)+'\n')
 finished=True
 print('FINAL_PASS',OUT,flush=True)
except BaseException as exc:
 save('failure.json',{'stage':stage_name,'observed_at':now(),'error_type':type(exc).__name__,'error':str(exc)})
 raise
finally:
 if injected and not finished:
  remaining=[a for a in [A,B,C] if a is not None]
  for a in remaining:a['endsAt']=now()
  inject('cleanup-after-failure',remaining)
  print('CLEANUP: resolution sent for synthetic alerts after failed check',flush=True)
 files=sorted(f for f in OUT.rglob('*') if f.is_file() and f.name!='SHA256SUMS')
 save('SHA256SUMS',''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+str(f.relative_to(OUT))+'\n' for f in files))
