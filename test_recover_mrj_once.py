"""Offline validation for one-time Mr J recovery; never posts to Discord."""
import io
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import recover_mrj_once as task


class RecoverMrJTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{
            "MRJ_ONE_TIME_RECOVERY":"approved","GITHUB_REF":"refs/heads/main",
            "GITHUB_TOKEN":"fake","GITHUB_REPOSITORY":"test/repo",
            "YOUTUBE_API_KEY":"fake","DISCORD_WEBHOOK_URL":"https://example.invalid"
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_fixed_five_ids_and_channel(self):
        self.assertEqual(len(task.IDS),5)
        self.assertEqual(len(set(task.IDS)),5)
        self.assertIn("TZ7z0pZvn8w",task.IDS)

    def test_claim_existing_skips_without_discord(self):
        prior=HTTPError("https://api.github.com",422,"exists",{},None)
        with patch.object(task,"api",side_effect=prior):
            self.assertIsNone(task.claim_once(task.IDS[0]))

    def test_success_claim_happens_before_post_and_receipt(self):
        events=[]
        def claim(id):
            events.append("claim:"+id)
            return ("path","sha",{"video_id":id})
        def deliver(id,m):
            events.append("post:"+id)
            return True
        def mark(id,claim):
            events.append("receipt:"+id)
        with patch.object(task,"metadata",return_value={x:{"snippet":{}} for x in task.IDS}), \
             patch.object(task,"claim_once",side_effect=claim), \
             patch.object(task,"deliver",side_effect=deliver), \
             patch.object(task,"mark_delivered",side_effect=mark), \
             patch.object(task.time,"sleep") as sleep:
            task.recover()
        for i,id in enumerate(task.IDS):
            self.assertEqual(events[i*3:i*3+3],
                             ["claim:"+id,"post:"+id,"receipt:"+id])
        self.assertEqual(sleep.call_count,5)

    def test_all_preclaimed_is_idempotent_no_webhook(self):
        with patch.object(task,"metadata",return_value={x:{"snippet":{}} for x in task.IDS}), \
             patch.object(task,"claim_once",return_value=None), \
             patch.object(task,"deliver") as deliver:
            task.recover()
            deliver.assert_not_called()

    def test_failed_webhook_never_retries_claimed_delivery(self):
        with patch.object(task,"metadata",return_value={x:{"snippet":{}} for x in task.IDS}), \
             patch.object(task,"claim_once",return_value=("path","sha",{})) as claim, \
             patch.object(task,"deliver",return_value=False) as deliver, \
             patch.object(task,"mark_delivered") as mark:
            with self.assertRaisesRegex(RuntimeError,"no automatic retry"):
                task.recover()
            self.assertEqual(claim.call_count,1)
            self.assertEqual(deliver.call_count,1)
            mark.assert_not_called()

    def test_wrong_branch_or_missing_approval_fails_before_post(self):
        for k,v in (("MRJ_ONE_TIME_RECOVERY","false"),("GITHUB_REF","refs/heads/feature")):
            with self.subTest(var=k), patch.dict(os.environ,{k:v}), \
                 patch.object(task,"metadata") as yt, patch.object(task,"deliver") as post:
                with self.assertRaises(RuntimeError):
                    task.recover()
                yt.assert_not_called()
                post.assert_not_called()

    def test_metadata_fails_closed_if_any_video_missing(self):
        result={"items":[]}
        mock=Mock()
        mock.__enter__=Mock(return_value=io.BytesIO(json.dumps(result).encode()))
        mock.__exit__=Mock(return_value=None)
        with patch.object(task,"urlopen",return_value=mock):
            with self.assertRaisesRegex(RuntimeError,"All five"):
                task.metadata()

    def test_claim_only_one_id_and_not_group_replay(self):
        def api(method,path,payload):
            self.assertIn(task.IDS[0],path)
            self.assertNotIn(task.IDS[1],path)
            self.assertEqual(payload["branch"],"notification-history")
            record=json.loads(__import__("base64").b64decode(payload["content"]))
            self.assertEqual(record["status"],"claimed")
            return {"content":{"sha":"saved"}}
        with patch.object(task,"api",side_effect=api):
            claim=task.claim_once(task.IDS[0])
        self.assertEqual(claim[1],"saved")

if __name__=="__main__":
    unittest.main()
