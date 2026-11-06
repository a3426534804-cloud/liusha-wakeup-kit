"""Exercise extracted real functions with synthetic files; no model starts."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest


class ProductionTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_selected_day_reads_older_than_60(self):
        module=importlib.import_module('integrations.production.awakening_records')
        module.ROOT=self.root
        (self.root/'logs').mkdir()
        rows=[{'id':str(i),'timestamp':'2026-01-03T00:00:00Z'} for i in range(90)]
        rows += [{'id':'old-'+str(i),'timestamp':'2026-01-01T20:00:00Z'} for i in range(65)]
        (self.root/'logs/awakening.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
        result=module._read_awakening_records(day='2026-01-02',month='2026-01')
        self.assertEqual(len(result['items']),65)
        self.assertEqual(result['dates']['2026-01-02'],65)
    def test_production_ledger_page_excludes_identifiers_and_body(self):
        module=importlib.import_module('integrations.production.usage_ledger')
        module.CLAUDE_RUNTIME_USAGE_LOG=self.root/'usage.jsonl'
        module.CLAUDE_RUNTIME_SESSION_STATE=self.root/'session.json'
        module.DB_PATH=self.root/'relay.db'
        module.TRANSCRIPT_DIR=self.root/'transcripts'
        module.CLAUDE_RUNTIME_USAGE_LOG.write_text(json.dumps({'timestamp':'2026-01-01T00:00:00Z','claude_session_id':'sample-session','relay_message_id':1,'prompt':'PRIVATE','cache_read_input_tokens':100})+'\n',encoding='utf-8')
        result=module._usage_ledger(period='all')
        self.assertEqual(result['total'],1)
        for key in ('message_id','session_id','wake_id','prompt','text'):self.assertNotIn(key,result['items'][0])
