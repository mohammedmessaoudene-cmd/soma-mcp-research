-- Runs once in a NEW temporary database as the migration superuser.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA soma AUTHORIZATION soma_owner;
REVOKE ALL ON SCHEMA soma FROM PUBLIC;
SET ROLE soma_owner;
ALTER DEFAULT PRIVILEGES IN SCHEMA soma REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE soma.tenant_state (
 tenant_id text PRIMARY KEY,
 total bigint NOT NULL CHECK(total BETWEEN 0 AND 9007199254740991),
 cap bigint NOT NULL CHECK(cap BETWEEN total AND 9007199254740991),
 version bigint NOT NULL DEFAULT 0 CHECK(version BETWEEN 0 AND 9007199254740991),
 policy_version bigint NOT NULL DEFAULT 1 CHECK(policy_version BETWEEN 0 AND 9007199254740991),
 budget_limit bigint NOT NULL CHECK(budget_limit BETWEEN 0 AND 9007199254740991),
 spent bigint NOT NULL DEFAULT 0 CHECK(spent>=0),
 held bigint NOT NULL DEFAULT 0 CHECK(held>=0),
 unknown bigint NOT NULL DEFAULT 0 CHECK(unknown>=0),
 CHECK(spent::numeric+held+unknown<=budget_limit)
);
CREATE TABLE soma.accounts (
 tenant_id text REFERENCES soma.tenant_state, account_id text,
 balance bigint NOT NULL CHECK(balance BETWEEN 0 AND 9007199254740991),
 PRIMARY KEY(tenant_id,account_id)
);
CREATE TABLE soma.subjects (
 db_role name PRIMARY KEY, principal_id text NOT NULL,
 tenant_id text NOT NULL REFERENCES soma.tenant_state,
 enabled boolean NOT NULL DEFAULT true,
 read_state boolean NOT NULL DEFAULT true,
 mutate boolean NOT NULL DEFAULT true,
 read_receipt boolean NOT NULL DEFAULT true,
 UNIQUE(tenant_id,principal_id)
);
CREATE FUNCTION soma.immutable_identity() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,soma,pg_temp AS $$
BEGIN
 IF TG_OP='DELETE' OR (NEW.db_role,NEW.principal_id,NEW.tenant_id)
    IS DISTINCT FROM (OLD.db_role,OLD.principal_id,OLD.tenant_id) THEN
  RAISE EXCEPTION 'IDENTITY_IMMUTABLE';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable_identity BEFORE UPDATE OR DELETE ON soma.subjects
 FOR EACH ROW EXECUTE FUNCTION soma.immutable_identity();

CREATE TABLE soma.operations (
 tenant_id text, principal_id text, operation_id text CHECK(operation_id ~ '^[A-Za-z0-9_-]{1,64}$'),
 tool_version text NOT NULL CHECK(tool_version='r3-1'),
 account_id text NOT NULL, delta bigint NOT NULL CHECK(delta<>0 AND abs(delta::numeric)<=9007199254740991),
 expected_version bigint NOT NULL CHECK(expected_version BETWEEN 0 AND 9007199254740991),
 admitted_policy_version bigint NOT NULL, committed_version bigint NOT NULL,
 result_payload jsonb NOT NULL,
 PRIMARY KEY(tenant_id,principal_id,operation_id),
 FOREIGN KEY(tenant_id,principal_id) REFERENCES soma.subjects(tenant_id,principal_id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES soma.accounts,
 UNIQUE(tenant_id,committed_version)
);
CREATE TABLE soma.reservations (
 tenant_id text, principal_id text, operation_id text,
 upper_bound bigint NOT NULL CHECK(upper_bound=60),
 actual_cost bigint CHECK(actual_cost BETWEEN 0 AND upper_bound),
 state text NOT NULL CHECK(state IN ('HELD','UNKNOWN','SETTLED')),
 PRIMARY KEY(tenant_id,principal_id,operation_id),
 FOREIGN KEY(tenant_id,principal_id,operation_id) REFERENCES soma.operations,
 CHECK((state='SETTLED')=(actual_cost IS NOT NULL))
);
CREATE TABLE soma.outbox (
 event_id text PRIMARY KEY, tenant_id text, principal_id text, operation_id text,
 payload text NOT NULL, payload_hash text NOT NULL,
 delivery_state text NOT NULL CHECK(delivery_state IN ('READY','IN_FLIGHT','UNKNOWN','ACKED')),
 attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
 lease_until timestamptz,
 UNIQUE(tenant_id,principal_id,operation_id),
 FOREIGN KEY(tenant_id,principal_id,operation_id) REFERENCES soma.reservations
);

CREATE FUNCTION soma.feedback(t soma.tenant_state) RETURNS jsonb
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,soma,pg_temp AS $$
 SELECT jsonb_build_object('profile','r3-1','state_version',t.version,
 'policy_version',t.policy_version,'capacity_remaining',t.cap-t.total,
 'budget_remaining',t.budget_limit-t.spent-t.held-t.unknown,
 'unknown_exposure',t.unknown,'unknown_count',t.unknown/60,'observation','CURRENT_SNAPSHOT',
 'state_version_scope','BUSINESS_MUTATIONS_ONLY');
$$;

CREATE FUNCTION soma.read_state() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,soma,pg_temp AS $$
DECLARE s soma.subjects; t soma.tenant_state;
BEGIN
 SELECT * INTO s FROM soma.subjects WHERE db_role=session_user;
 IF NOT FOUND THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
 SELECT * INTO t FROM soma.tenant_state WHERE tenant_id=s.tenant_id FOR SHARE;
 SELECT * INTO s FROM soma.subjects WHERE db_role=session_user FOR SHARE;
 IF NOT s.enabled OR NOT s.read_state THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
 RETURN jsonb_build_object('status','OBSERVED','total',t.total,'capacity',t.cap,
  'version',t.version,'policy_version',t.policy_version,'spent',t.spent,
  'held',t.held,'unknown',t.unknown,'budget_limit',t.budget_limit,'feedback',soma.feedback(t));
END $$;

CREATE FUNCTION soma.adjust_allocation(op text, acc text, d bigint, ev bigint)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,soma,pg_temp AS $$
DECLARE s soma.subjects; t soma.tenant_state; old soma.operations;
 b bigint; nb bigint; receipt jsonb; eid text; body text;
BEGIN
 IF op IS NULL OR acc IS NULL OR d IS NULL OR ev IS NULL
 OR op !~ '^[A-Za-z0-9_-]{1,64}$' OR acc !~ '^[A-Za-z0-9_-]{1,64}$'
 OR d=0 OR abs(d::numeric)>9007199254740991 OR ev NOT BETWEEN 0 AND 9007199254740991
 THEN RAISE EXCEPTION 'INVALID_ARGUMENT'; END IF;
 SELECT * INTO s FROM soma.subjects WHERE db_role=session_user;
 IF NOT FOUND THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
 SELECT * INTO t FROM soma.tenant_state WHERE tenant_id=s.tenant_id FOR UPDATE;
 SELECT * INTO s FROM soma.subjects WHERE db_role=session_user FOR SHARE;
 IF NOT s.enabled THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
 SELECT * INTO old FROM soma.operations WHERE tenant_id=s.tenant_id
  AND principal_id=s.principal_id AND operation_id=op;
 IF FOUND THEN
  IF NOT s.read_receipt THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
  IF (old.account_id,old.delta,old.expected_version,old.tool_version)
    IS DISTINCT FROM (acc,d,ev,'r3-1'::text) THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF;
  RETURN old.result_payload || jsonb_build_object('replayed',true)
   || CASE WHEN s.read_state THEN jsonb_build_object('feedback',soma.feedback(t)) ELSE '{}'::jsonb END;
 END IF;
 IF NOT s.mutate THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
 IF t.version<>ev THEN RAISE EXCEPTION 'VERSION_CONFLICT'; END IF;
 IF t.version=9007199254740991 THEN RAISE EXCEPTION 'VERSION_EXHAUSTED'; END IF;
 SELECT balance INTO b FROM soma.accounts WHERE tenant_id=s.tenant_id AND account_id=acc FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
 IF d < -b OR d > t.cap-t.total THEN RAISE EXCEPTION 'CAPACITY_EXCEEDED'; END IF;
 nb:=b+d;
 IF nb<0 OR nb>t.cap-(t.total-b) THEN RAISE EXCEPTION 'CAPACITY_EXCEEDED'; END IF;
 IF 60>t.budget_limit-t.spent-t.held-t.unknown THEN RAISE EXCEPTION 'BUDGET_EXHAUSTED'; END IF;
 UPDATE soma.accounts SET balance=nb WHERE tenant_id=s.tenant_id AND account_id=acc;
 UPDATE soma.tenant_state SET total=total-b+nb,version=version+1,held=held+60
  WHERE tenant_id=s.tenant_id RETURNING * INTO t;
 eid:=s.tenant_id||':'||s.principal_id||':'||op;
 receipt:=jsonb_build_object('operation_id',op,'status','COMMITTED',
 'committed_version',t.version,'admitted_policy_version',t.policy_version,
 'delivery_status_at_commit','READY','event_id',eid);
 INSERT INTO soma.operations VALUES(s.tenant_id,s.principal_id,op,'r3-1',acc,d,ev,
  t.policy_version,t.version,receipt);
 INSERT INTO soma.reservations VALUES(s.tenant_id,s.principal_id,op,60,NULL,'HELD');
 body:=jsonb_build_object('event_id',eid,'account_id',acc,'delta',d,
  'committed_version',t.version)::text;
 INSERT INTO soma.outbox(event_id,tenant_id,principal_id,operation_id,payload,payload_hash,delivery_state)
 VALUES(eid,s.tenant_id,s.principal_id,op,body,encode(sha256(convert_to(body,'UTF8')),'hex'),'READY');
 RETURN (CASE WHEN s.read_receipt THEN receipt ELSE jsonb_build_object('operation_id',op,'status','COMMITTED') END)
  || jsonb_build_object('replayed',false)
  || CASE WHEN s.read_state THEN jsonb_build_object('feedback',soma.feedback(t)) ELSE '{}'::jsonb END;
END $$;

CREATE FUNCTION soma.operation_status(op text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,soma,pg_temp AS $$
DECLARE s soma.subjects; result jsonb;
BEGIN
 IF op IS NULL OR op !~ '^[A-Za-z0-9_-]{1,64}$' THEN RAISE EXCEPTION 'INVALID_ARGUMENT'; END IF;
 SELECT * INTO s FROM soma.subjects WHERE db_role=session_user FOR SHARE;
 IF NOT FOUND OR NOT s.enabled OR NOT s.read_receipt THEN RAISE EXCEPTION 'NOT_AUTHORIZED'; END IF;
 SELECT jsonb_build_object('status','COMMITTED','receipt',o.result_payload,
  'delivery_state',e.delivery_state,'reservation_state',r.state)
  || CASE WHEN s.read_state THEN jsonb_build_object('observation_version',t.version,'feedback',soma.feedback(t)) ELSE '{}'::jsonb END INTO result
 FROM soma.operations o JOIN soma.reservations r USING(tenant_id,principal_id,operation_id)
 JOIN soma.outbox e USING(tenant_id,principal_id,operation_id)
 JOIN soma.tenant_state t USING(tenant_id)
 WHERE o.tenant_id=s.tenant_id AND o.principal_id=s.principal_id AND o.operation_id=op;
 RETURN coalesce(result,jsonb_build_object('status','NOT_OBSERVED_OR_PENDING','operation_id',op,
  'next_action','RETRY_SAME_KEY_OR_QUERY'));
END $$;

CREATE FUNCTION soma.set_rights(who name,en boolean,rd boolean,mu boolean,rc boolean)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,soma,pg_temp AS $$
DECLARE tid text;
BEGIN
 SELECT tenant_id INTO tid FROM soma.subjects WHERE db_role=who;
 IF NOT FOUND THEN RAISE EXCEPTION 'UNKNOWN_SUBJECT'; END IF;
 PERFORM 1 FROM soma.tenant_state WHERE tenant_id=tid FOR UPDATE;
 PERFORM 1 FROM soma.subjects WHERE db_role=who FOR UPDATE;
 UPDATE soma.subjects SET enabled=en,read_state=rd,mutate=mu,read_receipt=rc WHERE db_role=who;
 UPDATE soma.tenant_state SET policy_version=policy_version+1 WHERE tenant_id=tid;
END $$;

CREATE FUNCTION soma.mark_unknown(eid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,soma,pg_temp AS $$
DECLARE e soma.outbox; r soma.reservations;
BEGIN
 SELECT * INTO e FROM soma.outbox WHERE event_id=eid;
 IF NOT FOUND THEN RAISE EXCEPTION 'UNKNOWN_EVENT'; END IF;
 PERFORM 1 FROM soma.tenant_state WHERE tenant_id=e.tenant_id FOR UPDATE;
 SELECT * INTO r FROM soma.reservations WHERE tenant_id=e.tenant_id AND principal_id=e.principal_id
  AND operation_id=e.operation_id FOR UPDATE;
 SELECT * INTO e FROM soma.outbox WHERE event_id=eid FOR UPDATE;
 IF e.delivery_state='ACKED' THEN RETURN jsonb_build_object('status','ACKED'); END IF;
 IF r.state='HELD' THEN
  UPDATE soma.tenant_state SET held=held-r.upper_bound,unknown=unknown+r.upper_bound WHERE tenant_id=e.tenant_id;
  UPDATE soma.reservations SET state='UNKNOWN' WHERE tenant_id=e.tenant_id AND principal_id=e.principal_id AND operation_id=e.operation_id;
 END IF;
 UPDATE soma.outbox SET delivery_state='UNKNOWN',lease_until=NULL WHERE event_id=eid;
 RETURN jsonb_build_object('status','UNKNOWN');
END $$;

CREATE FUNCTION soma.claim(eid text) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,soma,pg_temp AS $$
DECLARE e soma.outbox; r soma.reservations;
BEGIN
 SELECT * INTO e FROM soma.outbox WHERE event_id=eid;
 IF NOT FOUND THEN RAISE EXCEPTION 'UNKNOWN_EVENT'; END IF;
 PERFORM 1 FROM soma.tenant_state WHERE tenant_id=e.tenant_id FOR UPDATE;
 SELECT * INTO r FROM soma.reservations WHERE tenant_id=e.tenant_id AND principal_id=e.principal_id
  AND operation_id=e.operation_id FOR UPDATE;
 SELECT * INTO e FROM soma.outbox WHERE event_id=eid FOR UPDATE;
 IF e.delivery_state='ACKED' THEN RETURN jsonb_build_object('status','ACKED'); END IF;
 IF e.delivery_state='IN_FLIGHT' AND e.lease_until>clock_timestamp() THEN
  RETURN jsonb_build_object('status','BUSY'); END IF;
 IF e.delivery_state='IN_FLIGHT' THEN PERFORM soma.mark_unknown(eid); END IF;
 IF e.attempt_count>=3 THEN RETURN jsonb_build_object('status','RECONCILE_ONLY'); END IF;
 UPDATE soma.outbox SET delivery_state='IN_FLIGHT',attempt_count=attempt_count+1,
  lease_until=clock_timestamp()+interval '1 second' WHERE event_id=eid RETURNING * INTO e;
 RETURN jsonb_build_object('status','CLAIMED','event_id',eid,'payload',e.payload,
  'payload_hash',e.payload_hash,'attempt_count',e.attempt_count);
END $$;

CREATE FUNCTION soma.settle(eid text,ph text,cost bigint) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,soma,pg_temp AS $$
DECLARE e soma.outbox; r soma.reservations;
BEGIN
 SELECT * INTO e FROM soma.outbox WHERE event_id=eid;
 IF NOT FOUND THEN RAISE EXCEPTION 'UNKNOWN_EVENT'; END IF;
 PERFORM 1 FROM soma.tenant_state WHERE tenant_id=e.tenant_id FOR UPDATE;
 SELECT * INTO r FROM soma.reservations WHERE tenant_id=e.tenant_id AND principal_id=e.principal_id
  AND operation_id=e.operation_id FOR UPDATE;
 SELECT * INTO e FROM soma.outbox WHERE event_id=eid FOR UPDATE;
 IF ph IS NULL OR ph<>e.payload_hash OR cost IS NULL OR cost<0 OR cost>r.upper_bound
  OR (r.state='SETTLED' AND cost<>r.actual_cost) THEN
  IF r.state<>'SETTLED' THEN PERFORM soma.mark_unknown(eid); END IF;
  RETURN jsonb_build_object('status','RECONCILIATION_REQUIRED');
 END IF;
 IF r.state='SETTLED' THEN RETURN jsonb_build_object('status','ACKED','replayed',true); END IF;
 UPDATE soma.tenant_state SET spent=spent+cost,
  held=held-CASE WHEN r.state='HELD' THEN r.upper_bound ELSE 0 END,
  unknown=unknown-CASE WHEN r.state='UNKNOWN' THEN r.upper_bound ELSE 0 END WHERE tenant_id=e.tenant_id;
 UPDATE soma.reservations SET state='SETTLED',actual_cost=cost
 WHERE tenant_id=e.tenant_id AND principal_id=e.principal_id AND operation_id=e.operation_id;
 UPDATE soma.outbox SET delivery_state='ACKED',lease_until=NULL WHERE event_id=eid;
 RETURN jsonb_build_object('status','ACKED','replayed',false);
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA soma FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA soma FROM PUBLIC;
GRANT USAGE ON SCHEMA soma TO soma_alice,soma_peer,soma_bob,soma_worker;
GRANT EXECUTE ON FUNCTION soma.read_state(),soma.adjust_allocation(text,text,bigint,bigint),soma.operation_status(text)
 TO soma_alice,soma_peer,soma_bob;
GRANT EXECUTE ON FUNCTION soma.claim(text),soma.mark_unknown(text),soma.settle(text,text,bigint) TO soma_worker;
RESET ROLE;
