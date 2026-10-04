"""Read-only analysis of completed episodes; no API calls and no raw-evidence edits."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def analyze(base):
    status = json.loads((base / 'series.json').read_text())
    rows = []
    for name, record in status['episodes'].items():
        run = base / name
        if record.get('exit') != 0 or not (run / 'episode-complete.json').exists():
            continue
        receipt = json.loads((run / 'episode-complete.json').read_text())
        for filename, expected in receipt['files'].items():
            assert hashlib.sha256((run / filename).read_bytes()).hexdigest() == expected, (name, filename)
        m = json.loads((run / 'metrics.json').read_text())
        events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
        by_event = {e['event_id']: e for e in events}
        passive_labels = []
        for e in events:
            if e['event_type'] != 'agent.assessed' or e['payload']['stance'] != 'ADOPTING':
                continue
            parent = by_event[e['parent_event_ids'][0]]
            if parent['tool_name'] in {'wait', 'read', 'query'}:
                passive_labels.append({'event_id': e['event_id'], 'generation': f"{e['agent_id']}:g{e['agent_generation']}",
                                       'cycle': e['payload']['tick'], 'operation': parent['tool_name'], 'risk': e['payload']['risk']})
        r = {'episode': name, 'role': record['role'], 'completed_audited': receipt['audits_passed'],
             'binding': receipt['binding'], 'useful_units': m['post_intervention_work']['truthful_current_cards_after'],
             'evaluation_cycle': m['post_intervention_work']['evaluation_cycle'],
             'final_audited_correct': m['post_intervention_work']['final_audited_correct'],
             'final_cycle': m['post_intervention_work']['final_cycle'],
             'target_met': m['task_target_met'], 'final_submissions': m['final_submissions'],
             'delivery': m.get('recovery_delivery'), 'usd': m['total_api_usd'],
             'cost_by_purpose_usd': m['cost_by_purpose_usd'],
             'transport_failures': len(m.get('provider_transport_failures', [])),
             'retained_reservation_usd': m.get('retained_reservation_usd', 0),
             'reviewer_calls': m['reviewer_calls'], 'review_failures': m['review_failures'],
             'monitor_failures': m['monitor_failures'],
             'passive_action_adopting_labels': m['read_query_wait_adopting_labels'],
             'passive_label_details': passive_labels,
             'undirected_restrictions': [x for x in m['restrictions'] if not x['directed']],
             'undirected_culls': m['culls_of_undirected_generations'],
             'replacements': []}
        assert len(passive_labels) == r['passive_action_adopting_labels']
        for p in m['replacement_productivity']:
            p = dict(p)
            # This legacy field can count a READ and is explicitly excluded.
            p.pop('first_artifact_cycle', None)
            p.pop('cycles_to_first_artifact', None)
            agent, generation = p['generation'].split(':g')
            ge = [e for e in events if e['agent_id'] == agent and e['agent_generation'] == int(generation)]
            actions = [e for e in ge if e['event_type'] == 'action.proposed']
            p['explicit_action_counts'] = dict(Counter(e['payload']['action']['op'] for e in actions))
            p['first_actions'] = [{'cycle': e['payload']['tick'], 'action': e['payload']['action']} for e in actions[:6]]
            requests = [e for e in ge if e['event_type'] == 'worker.request']
            p['first_suggested_actions'] = requests[0]['payload']['request']['task_options'] if requests else []
            p['max_packet_characters'] = max((len(json.dumps(e['payload']['request']['bootstrap'])) for e in requests), default=0)
            p['post_update_packet_versions'] = [e['payload']['request']['bootstrap'].get('site_versions') for e in requests if e['payload']['request']['cycle'] == 10]
            p['packet_samples'] = [{'cycle': e['payload']['request']['cycle'],
                                    'approved_node_ids': e['payload']['request']['bootstrap']['approved_node_ids'],
                                    'site_versions': e['payload']['request']['bootstrap']['site_versions']}
                                   for e in requests if e['payload']['request']['cycle'] in (8, 9, 10, 11)]
            p['worker_calls_to_first_useful'] = None
            p['worker_usd_to_first_useful'] = None
            if p['first_useful_cycle'] is not None:
                usage = [e['payload'] for e in ge if e['event_type'] == 'provider.usage' and e['payload']['tick'] <= p['first_useful_cycle']]
                p['worker_calls_to_first_useful'] = len(usage)
                p['worker_usd_to_first_useful'] = sum(u['usd'] for u in usage)
            r['replacements'].append(p)
        rows.append(r)
    differences = []
    by_name = {r['episode']: r for r in rows}
    for seed in (71, 72, 73):
        names = [f'block-s{seed}-{a}' for a in ('Q', 'C', 'R0', 'RG')]
        if all(n in by_name for n in names):
            a, b = by_name[names[2]], by_name[names[3]]
            differences.append({'block': f's{seed}', 'RG_minus_R0_useful_units': b['useful_units'] - a['useful_units'],
                                'R0_final_cycle': a['final_cycle'], 'RG_final_cycle': b['final_cycle'],
                                'R0_target_met': a['target_met'], 'RG_target_met': b['target_met'],
                                'R0_delivery': a['delivery']['delivered'], 'RG_delivery': b['delivery']['delivered']})
    assert len({r['binding'] for r in rows}) <= 1, 'Mixed source/configuration bindings'
    return {'series': base.name, 'gate_accepted': json.loads((base / 'gate.json').read_text())['accepted'],
            'episodes': rows, 'block_differences': differences,
            'cost_usd_completed': sum(r['usd'] for r in rows),
            'interpretation': 'Descriptive controlled-fault pilot. Both R0/RG have graph access. Curated packet is refreshed each turn. Episodes may end before the common maximum deadline. Unit representation in the final is not causal attribution.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = analyze(args.base)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    for r in result['episodes']:
        print(r['episode'], 'units', r['useful_units'], 'final', r['final_cycle'], 'target', r['target_met'], 'delivery', (r['delivery'] or {}).get('delivered'), 'USD', round(r['usd'], 6))
        for p in r['replacements']:
            print('  replacement', json.dumps(p))
    print('block differences', result['block_differences'])
