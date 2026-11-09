CREATE OR REPLACE FUNCTION incident.claim_investigation()
 RETURNS TABLE(request_id text, lease_id uuid, context_json text, context_digest text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'incident', 'pg_temp'
AS $function$
DECLARE r incident.investigation_outbox%ROWTYPE;
BEGIN
    -- Expired jobs remain visible and require explicit operator review.
    FOR r IN SELECT o.* FROM incident.investigation_outbox o
        WHERE o.state IN ('PENDING', 'DISPATCHED')
        AND o.first_dispatch_at < clock_timestamp() - interval '25 minutes'
        FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE incident.investigation_outbox o SET state = 'FAILED', last_error = 'Investigation dispatch deadline exceeded',
            lease_id = NULL, lease_until = NULL WHERE o.request_id = r.request_id;
        UPDATE incident.tickets SET status = 'ESCALATED', reason = 'Investigation dispatch deadline exceeded'
            WHERE incident_id = r.incident_id;
    END LOOP;
    SELECT o.* INTO r FROM incident.investigation_outbox o
        WHERE o.state IN ('PENDING', 'DISPATCHED') AND o.next_attempt_at <= clock_timestamp()
        AND (o.lease_until IS NULL OR o.lease_until < clock_timestamp())
        -- Existing webhook smoke checks must never consume provider credits.
        AND coalesce(o.context #>> '{alert,labels,test}', '') <> 'true'
        ORDER BY (o.state = 'DISPATCHED') DESC, o.created_at
        LIMIT 1 FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN RETURN; END IF;
    UPDATE incident.investigation_outbox o SET lease_id = gen_random_uuid(),
        lease_until = clock_timestamp() + interval '60 seconds',
        first_dispatch_at = coalesce(o.first_dispatch_at, clock_timestamp())
        WHERE o.request_id = r.request_id
        RETURNING o.request_id, o.lease_id, o.context::text, o.context_digest
        INTO request_id, lease_id, context_json, context_digest;
    RETURN NEXT;
END $function$
