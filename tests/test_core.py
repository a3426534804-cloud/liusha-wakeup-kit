from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from liusha.policy import SilentPolicy, visible_idle_decision
from liusha.usage import ledger, normalize, ResultUsage


class PolicyTests(unittest.TestCase):
    def setUp(self):self.p=SilentPolicy()
    def decide(self,**kw):return self.p.decide(now=10000,activity=10000-36*60,**kw)
    def test_ready(self):self.assertEqual(self.decide(),'ready')
    def test_busy(self):self.assertEqual(self.decide(session_busy=True),'native_chat_owns_session')
    def test_disabled(self):self.assertEqual(self.decide(same_activity=True,disabled=True),'paused_after_failure')
    def test_new_real_activity_can_reset_failure(self):self.assertEqual(self.decide(same_activity=False,disabled=True),'ready')
    def test_config(self):self.assertEqual(self.decide(config_changed=True),'configuration_changed_wait_for_chat')
    def test_expired(self):self.assertEqual(self.p.decide(now=10000,activity=7000),'expired_wait_for_chat')
    def test_refresh_anchor(self):self.assertEqual(self.decide(last_refresh=9900,same_activity=True),'not_due')
    def test_bad_probe(self):self.assertTrue(self.p.after_probe({'cache_read_input_tokens':0})[1])
    def test_rebuild_probe(self):self.assertTrue(self.p.after_probe({'cache_read_input_tokens':100,'cache_creation_input_tokens':10000})[1])
    def test_success_probe(self):self.assertEqual(self.p.after_probe({'cache_read_input_tokens':64000,'cache_creation_input_tokens':300}),('refreshed',False))
    def test_no_activity(self):self.assertEqual(self.p.decide(now=10000,activity=0),'no_verified_activity')
    def test_night_is_quiet(self):self.assertEqual(visible_idle_decision(hour=3,minute=0,user_started_day=True,message_age=3100,usage_age=3100,same_session=True),'outside_window')
    def test_dont_wake_user_before_real_activity(self):self.assertEqual(visible_idle_decision(hour=9,minute=10,user_started_day=False,message_age=3100,usage_age=3100,same_session=True),'wait_for_user_today')
    def test_bubble_window(self):self.assertEqual(visible_idle_decision(hour=15,minute=0,user_started_day=True,message_age=3100,usage_age=3100,same_session=True),'ready')
    def test_no_expensive_cold_bubble(self):self.assertEqual(visible_idle_decision(hour=15,minute=0,user_started_day=True,message_age=3600,usage_age=3600,same_session=True),'cache_too_old')


class LedgerTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'usage.jsonl'
    def tearDown(self):self.tmp.cleanup()
    def write(self,rows):self.path.write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
    def test_weighted_read_ratio(self):
        row=normalize({'cache_read_input_tokens':80,'cache_creation_input_tokens':10,'input_tokens':10})
        self.assertEqual(row['cache_read_ratio'],.8)
    def test_partial_is_not_free(self):
        row=normalize({'usage_partial':True,'total_cost_usd':None,'output_tokens':0})
        self.assertIsNone(row['output_tokens']);self.assertIsNone(row['cost_usd'])
    def test_cli_cost_is_difference_but_tokens_are_per_turn(self):
        acc=ResultUsage();first=acc.record({'total_cost_usd':1,'usage':{'input_tokens':10}},at='t')
        second=acc.record({'total_cost_usd':1.25,'usage':{'input_tokens':8}},at='t')
        self.assertEqual(first['total_cost_usd'],1);self.assertEqual(second['total_cost_usd'],.25);self.assertEqual(second['input_tokens'],8)
    def test_date_before_pagination(self):
        rows=[{'timestamp':'2026-01-03T00:00:00Z','source':'chat'} for _ in range(90)]
        rows += [{'timestamp':'2026-01-01T20:00:00Z','source':'bubble'} for _ in range(20)]
        self.write(rows)
        result=ledger(self.path,period='today',source='bubble',now=datetime(2026,1,2,1,tzinfo=timezone.utc))
        self.assertEqual(result['total'],20);self.assertEqual(len(result['items']),15)
    def test_old_logs_are_not_limited_to_60_rows(self):
        self.write([{'timestamp':'2026-01-01T00:00:00Z'} for _ in range(80)])
        result=ledger(self.path,period='all',offset=60)
        self.assertEqual(result['total'],80);self.assertEqual(len(result['items']),15)
    def test_invalid_lines_and_privacy(self):
        self.write([{'timestamp':'2026-01-01T00:00:00Z','text':'PRIVATE','session_id':'PRIVATE','token':'PRIVATE'},{}])
        with self.path.open('a') as out:out.write('broken json\n')
        result=ledger(self.path,period='all');self.assertEqual(result['dropped_rows'],2);self.assertNotIn('PRIVATE',json.dumps(result))
    def test_missing_file(self):self.assertEqual(ledger(self.path,period='all')['total'],0)


if __name__=='__main__':unittest.main()
