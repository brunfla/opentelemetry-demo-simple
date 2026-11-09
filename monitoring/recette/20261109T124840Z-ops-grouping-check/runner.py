import copy, hashlib, json, os, pathlib, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from urllib import request
import yaml

ROOT=pathlib.Path('/home/bruno/code/opentelemetry-demo-simple')
LAB=ROOT.parent/'incident-workflow-lab'
RUN=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUT=ROOT/'monitoring'/'recette'/(RUN+'-ops-grouping-check')
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
WITH g AS (SELECT * FROM incident.alertmanager_groups WHERE source='otel-demo' AND receiver='n8n-incidents' AND group_labels->>'alertname'='OpsGroupingCheck'),
e AS (SELECT x.* FROM incident.alertmanager_episodes x JOIN g USING(group_id)),
o AS (SELECT x.* FROM incident.investigation_outbox x WHERE incident_id IN (SELECT incident_id FROM e))
SELECT jsonb_build_object(
'observed_at',clock_timestamp(),
'groups',coalesce((SELECT jsonb_agg(jsonb_build_object('group_id',group_id,'source',source,'receiver',receiver,'group_key',group_key,'group_labels',group_labels)) FROM g),'[]'::jsonb),
'episodes',coalesce((SELECT jsonb_agg(jsonb_build_object('episode_id',episode_id,'group_id',group_id,'state',state,'disposition',disposition,'incident_id',incident_id)) FROM e),'[]'::jsonb),
'members',coalesce((SELECT jsonb_agg(jsonb_build_object('alert_key',alert_key,'episode_id',episode_id,'labels',payload->'labels','status',payload->>'status','startsAt',payload->>'startsAt')) FROM incident.alertmanager_members WHERE episode_id IN (SELECT episode_id FROM e)),'[]'::jsonb),
'investigations',coalesce((SELECT jsonb_agg(jsonb_build_object('incident_id',incident_id,'investigation_id',investigation_id,'status',status)) FROM incident.tickets WHERE incident_id IN (SELECT incident_id FROM e)),'[]'::jsonb),
'requests',coalesce((SELECT jsonb_agg(jsonb_build_object('request_id',request_id,'incident_id',incident_id,'state',state,'test_label',context #>> '{alert,labels,test}','first_dispatch_at',first_dispatch_at,'job_id',job_id,'lease_id',lease_id,'failures',failures)) FROM o),'[]'::jsonb),
'receipts',coalesce((SELECT jsonb_agg(jsonb_build_object('receipt_id',r.receipt_id,'received_at',r.received_at,'execution_id',r.execution_id,'status',r.payload->>'status') ORDER BY r.received_at) FROM incident.receipts r JOIN incident.alertmanager_notifications n USING(receipt_id) WHERE n.group_id IN (SELECT group_id FROM g)),'[]'::jsonb));
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
 return {key:sorted(x[field] for x in s[key]) for key,field in [('groups','group_id'),('episodes','episode_id'),('members','alert_key'),('investigations','investigation_id'),('requests','request_id')]}
def check(s,receipts):
 assert s['counts']==dict(groups=1,episodes=1,members=2,investigations=1,requests=1,receipts=receipts,agent_jobs=0),s['counts']
 assert all(m['labels']['test']=='true' for m in s['members'])
 assert {m['labels']['pod'] for m in s['members']}=={'grouping-test-a','grouping-test-b'}
 assert all(o['test_label']=='true' and o['state']=='PENDING' and o['first_dispatch_at'] is None and o['job_id'] is None and o['lease_id'] is None for o in s['requests'])

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
workflow=query('''SELECT json_build_object('active',active,'version',"activeVersionId",'claim_query',(SELECT n->'parameters'->>'query' FROM jsonb_array_elements(nodes::jsonb) n WHERE n->>'name'='Claim persisted request'),'schedule',(SELECT n->'parameters' FROM jsonb_array_elements(nodes::jsonb) n WHERE n->>'type'='n8n-nodes-base.scheduleTrigger')) FROM workflow_entity WHERE id='labOtelDispatch001';''','n8n')
assert workflow['active'] and 'incident.claim_investigation()' in workflow['claim_query']
save('dispatcher-workflow.json',workflow)
save('snapshot-query.sql',SNAPSHOT)
save('worker-query.py',JOB_CODE)
baseline=snapshot();save('00-before-injection.json',baseline)
assert baseline['counts']==dict(groups=0,episodes=0,members=0,investigations=0,requests=0,receipts=0,agent_jobs=0),'Existing OpsGroupingCheck objects; inspect before running again'
labels=dict(alertname='OpsGroupingCheck',cluster='otel-demo',namespace='otel-demo',service_name='quote',environment='local',test='true')
starts=now();ends=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat().replace('+00:00','Z')
alerts=[dict(labels=dict(labels,pod=pod),startsAt=starts,endsAt=ends,annotations={'summary':'Synthetic grouping transport check; model dispatch excluded','recipe_run':RUN}) for pod in ['grouping-test-a','grouping-test-b']]
save('injected-alerts.json',alerts)
injected=False
try:
 injected=True
 injection=http(AM+'/api/v2/alerts',json.dumps(alerts).encode());save('injection-response.json',injection)
 assert injection['http_status']==200
 print('INJECTED',starts,'two alerts, test=true',flush=True)
 def receipt():
  return query("SELECT coalesce(jsonb_agg(jsonb_build_object('receipt_id',r.receipt_id,'received_at',r.received_at,'execution_id',r.execution_id,'payload',r.payload) ORDER BY r.received_at),'[]'::jsonb) FROM incident.receipts r JOIN incident.alertmanager_notifications n USING(receipt_id) JOIN incident.alertmanager_groups g USING(group_id) WHERE g.group_labels->>'alertname'='OpsGroupingCheck';")
 capture=wait_for(receipt)
 assert len(capture)==1,len(capture)
 incoming=capture[0];payload=incoming['payload']
 assert payload['version']=='4' and payload['status']=='firing' and payload['receiver']=='n8n-incidents' and payload['truncatedAlerts']==0
 assert len(payload['alerts'])==2 and all(a['labels']['test']=='true' for a in payload['alerts'])
 assert payload['groupLabels']=={k:labels[k] for k in group_by}
 save('notifications/01-alertmanager-webhook.json',payload)
 save('notifications/01-reception.json',{k:v for k,v in incoming.items() if k!='payload'})
 before=snapshot();check(before,1);save('01-before-replay.json',before)
 assert before['groups'][0]['group_key']==payload['groupKey']
 print('RECEIVED',json.dumps(before['counts']),'groupKey from actual receipt',flush=True)
 # The receiver archives JSONB values, not the original HTTP bytes. Re-send the
 # full recorded object without altering any value, order-sensitive array or key.
 body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
 (OUT/'notifications'/'02-replay-body.json').write_bytes(body)
 sys.path.insert(0,str(LAB/'scripts'))
 from bootstrap import read_env
 token=read_env(LAB/'.env')['WEBHOOK_TOKEN']
 replay=http('http://localhost:5678/webhook/otel-alerts',body,token);del token
 save('replay-response.json',replay)
 assert replay['http_status']==202 and replay['body']['persisted'] is True
 after=snapshot();check(after,2);save('02-after-replay.json',after)
 assert identities(before)==identities(after)
 captured_after=receipt();assert len(captured_after)==2
 assert all(x['payload']==payload for x in captured_after)
 save('notifications/02-replay-reception.json',{k:v for k,v in captured_after[1].items() if k!='payload'})
 save('payload-equality.json',{'json_values_identical':True,'raw_original_http_bytes_available':False,'replay_body_sha256':hashlib.sha256(body).hexdigest(),'initial_receipt_id':incoming['receipt_id'],'replay_receipt_id':captured_after[1]['receipt_id']})
 print('REPLAY_PASS',json.dumps(after['counts']),'HTTP 202; all identities unchanged',flush=True)
 # Observe the active 30-second dispatcher for at least two ticks after replay.
 observation_started=now();time.sleep(65)
 observed=snapshot();check(observed,2);assert identities(before)==identities(observed);save('03-after-dispatch-observation.json',observed)
 ticks=query("SELECT coalesce(jsonb_agg(jsonb_build_object('id',id,'started_at',\"startedAt\",'stopped_at',\"stoppedAt\",'status',status) ORDER BY \"startedAt\"),'[]'::jsonb) FROM execution_entity WHERE \"workflowId\"='labOtelDispatch001' AND \"startedAt\">='"+observation_started+"'::timestamptz;",'n8n')
 save('dispatcher-executions.json',ticks)
 assert len(ticks)>=2 and all(t['status']=='success' for t in ticks),ticks
 print('DISPATCH_PASS',len(ticks),'scheduled executions; zero associated worker jobs',flush=True)
 save('result.json',{'result':'PASS','run_id':RUN,'checked_at':now(),'counts_before_replay':before['counts'],'counts_after_replay':after['counts'],'counts_after_dispatch_observation':observed['counts'],'identities':identities(after),'dispatcher_executions_observed':len(ticks),'original_webhook_capture':'Complete JSON values from incident.receipts.payload; original HTTP byte formatting is not retained by JSONB.'})
finally:
 if injected:
  resolved=copy.deepcopy(alerts)
  for a in resolved:a['endsAt']=now()
  save('cleanup-alerts.json',resolved)
  cleanup=http(AM+'/api/v2/alerts',json.dumps(resolved).encode());save('cleanup-response.json',cleanup)
  print('CLEANUP: sent resolution for only the two synthetic alerts',flush=True)
if (OUT/'result.json').exists():
 def is_resolved():
  s=snapshot()
  return s if len(s['episodes'])==1 and s['episodes'][0]['state']=='RESOLVED' else None
 final=wait_for(is_resolved,75);save('04-after-cleanup.json',final);check(final,3)
 assert identities(before)==identities(final)
 cap=receipt();save('notifications/03-alertmanager-resolution.json',cap[-1]['payload'])
 save('notifications/03-resolution-reception.json',{k:v for k,v in cap[-1].items() if k!='payload'})
 lines=['Recette OpsGroupingCheck — PASS',f'Run UTC : {RUN}',f'Début injection : {starts}',f'Première réception SQL : {incoming["received_at"]}',f'Rejeu : {replay["started_at"]}, HTTP 202',f'Fin des vérifications : {now()}','',
 'Route réellement chargée : n8n-incidents ; group_by hérité = cluster, namespace, alertname, service_name.',
 'pod est exclu du grouping ; environment=local et test=true conservés sur les deux membres.',
 'Une seule requête POST à Alertmanager contient les deux alertes ; group_wait=10s.',
 'Aucun groupKey construit par le test : valeur issue de la notification réellement archivée.',
 '', 'Objet | Avant injection | Avant rejeu | Après rejeu | Après 65 s de dispatch',
 *[f'{key} | {baseline["counts"][key]} | {before["counts"][key]} | {after["counts"][key]} | {observed["counts"][key]}' for key in after['counts']],
 '', 'Identifiants stables :',json.dumps(identities(after),ensure_ascii=False,indent=2),'',
 f'{len(ticks)} passages réussis du dispatcher observés après rejeu. Demande PENDING, first_dispatch_at/job_id/lease_id nuls ; aucun job dans la base SQLite du worker pour cette demande ou investigation.',
 'Le JSON intégral reçu et rejoué est identique en valeurs. La base JSONB ne conserve pas les octets HTTP originaux ni leur mise en forme ; aucune égalité octet pour octet avec le corps émis n’est revendiquée.',
 '', 'Nettoyage : résolution explicite des deux alertes synthétiques via Alertmanager. Épisode RESOLVED ; mêmes identifiants et compteurs métier. Une troisième réception, de résolution, est archivée après la preuve de rejeu. L’investigation reste conservée ; aucune clôture métier déduite.',
 'Aucune route, règle, migration, permission ou archive existante modifiée. Aucun secret ni en-tête d’authentification dans ce dossier.',
 'Les autres cas du sprint (troncature, états mixtes, reprise HTTP, HA, redéploiement) ne sont pas couverts par cette recette.']
 save('report.txt','\n'.join(lines)+'\n')
 save('runner.py',pathlib.Path(__file__).read_text())
 print('EVIDENCE',OUT,flush=True)
 print('FINAL_PASS',json.dumps(final['counts']),flush=True)
