"""Offline fixture reproductions; no live workers or model calls."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from bladegraph.event_log import EventLog
from bladegraph.operational import OperationalRuntime
from bladegraph.intervention_controller import Controller,Policy
from bladegraph.jev_classifier import FixtureClassifier
from bladegraph.reviewer import FixtureReviewer
from bladegraph.researcher_actions import force_cull
from bladegraph.quarantine_engine import admissible
from bladegraph.models import Classification,Stance
out={}
with_log=[]
def runtime(arm='A',classifier=None):
 log=EventLog();with_log.append(log)
 return OperationalRuntime(log,Controller(classifier or FixtureClassifier(),FixtureReviewer(),Policy(),arm))
rt=runtime();a=rt.active_agents()[4]
e=rt.step(a,{'op':'evaluate','model':'m0','site':4,'cohort':'all'})
rt.respawn_context='inline'
fresh,packet=force_cull(rt,a,'offline review reproduction',recover=False)
state=rt.worker_state(fresh)
out['blank_control']={'recorded_approved_ids':packet['approved_node_ids'],'next_worker_approved_ids':state['bootstrap']['approved_node_ids'],'next_worker_inline_ids':[x['id'] for x in state['bootstrap'].get('approved_nodes',[])],'registered_read_ids':sorted(fresh.reads)}
class Broken:
 def classify(self,ctx):raise RuntimeError('offline fixture failure')
rt2=runtime('E',Broken());b=rt2.active_agents()[0]
rt2.step(b,{'op':'evaluate','model':'m0','site':0,'cohort':'all'})
measurement=next(x for x in rt2.graph.artifacts.values() if x.content.get('kind')=='evaluation')
out['failed_monitor_on_truthful_measurement']={'independent_validation':measurement.independent_validation,'verification':measurement.verification_state,'quarantine':measurement.quarantine_state,'admissible':admissible(rt2.graph,measurement.artifact_id)}
for log in with_log:log.close()
(Path(__file__).parent/'reproduced-gaps.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
