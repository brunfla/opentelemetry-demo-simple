
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
