import sys,os,json,sqlite3,pathlib
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
