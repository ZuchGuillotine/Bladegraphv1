"""Read-only verification of retained research evidence. No provider calls."""
import sys, json, hashlib, sqlite3
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from bladegraph.event_log import digest
from bladegraph.graph_projector import Graph
from audit_workflow_boundaries import audit_run

def subset(expected, actual):
    if isinstance(expected,dict):return all(k in actual and subset(v,actual[k]) for k,v in expected.items())
    return expected==actual

rows=[]
for p in sorted((Path(__file__).parent/'evidence').glob('*/*/events.jsonl')):
    folder=p.parent
    checks=json.loads((folder/'checksums.json').read_text())
    for name,expected in checks.items():assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==expected,(folder.name,name)
    events=[json.loads(line) for line in p.read_text().splitlines()]
    previous='0'*64
    for seq,e in enumerate(events,1):
        body={k:v for k,v in e.items() if k!='hash'}
        assert e['sequence']==seq and e['previous_hash']==previous and digest(body)==e['hash']
        previous=e['hash']
    db=sqlite3.connect(f'file:{folder/"events.sqlite"}?mode=ro',uri=True)
    sql=[{**json.loads(body),'hash':h} for body,h in db.execute('select body,hash from events order by seq')];db.close()
    assert events==sql
    manifest=json.loads((folder/'manifest.json').read_text());snapshot=json.loads((folder/'researcher-snapshot.json').read_text())
    assert previous==manifest['event_log_hash']
    assert digest(manifest['config'])==manifest['config_hash']
    for name,h in manifest['source_hashes'].items():assert digest(snapshot['sources'][name])==h
    frozen=json.loads((folder/'frozen-config.json').read_text())
    assert digest(snapshot['runner_source'])==frozen['runner_hash']
    graph=Graph.replay(events).snapshot();saved_graph=json.loads((folder/'graph.json').read_text())
    graph_match=subset(saved_graph,graph)
    # Additive projection fields do not invalidate older saved schemas.
    boundary=audit_run(folder,[])
    metrics=json.loads((folder/'metrics.json').read_text())
    submissions=[e['payload'] for e in events if e['event_type']=='study.submitted']
    culls=[e for e in events if e['event_type']=='agent.state' and e['payload']['state']=='CULLED']
    counts=Counter(e['event_type'] for e in events)
    result={'run':folder.name,'files_verified':len(checks),'events_verified':len(events),'graph_matches_saved_fields':graph_match,'boundary':boundary,'config':manifest['config'],'submission':submissions,'worker_requests':counts['worker.request'],'monitor_failures':counts['monitor.failed'],'reviewer_calls':metrics.get('reviewer_calls'),'review_failures':counts['review.failed'],'culls':[{'who':e['agent_id']+':g'+str(e['agent_generation']),'cycle':e['payload']['tick']} for e in culls],'settled_run_usd':metrics.get('total_api_usd'),'cumulative_ledger':json.loads((folder/'cost-ledger.json').read_text()),'recovery_reads':metrics.get('replacement_reads_of_own_previous_evidence'),'replacement_productivity':metrics.get('replacement_productivity'),'quarantined_verified':metrics.get('quarantined_verified_artifacts')}
    rows.append(result)
    print(folder.name, 'events',len(events),'exact requests',boundary['exact_paid_request_hashes_verified'],'replay',graph_match,'outcome',[(s['model'],s['actual_target_met']) for s in submissions],flush=True)
(Path(__file__).parent/'verified-evidence.json').write_text(json.dumps(rows,indent=2)+'\n')
print('TOTAL exact worker requests',sum(r['boundary']['exact_paid_request_hashes_verified'] for r in rows))
