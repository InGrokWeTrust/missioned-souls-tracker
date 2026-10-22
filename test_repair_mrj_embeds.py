"""Offline tests: thumbnail repair must only PATCH verified existing messages."""
import copy
import unittest
from unittest.mock import Mock, patch

import repair_mrj_embeds as repair
from recover_mrj_once import IDS

GUILD="1509324392267505855"
CHANNEL="1509325093023514655"
WEBHOOK_ID="123456789012345678"
WEBHOOK="https://discord.com/api/webhooks/"+WEBHOOK_ID+"/TEST.secret_A"
MESSAGE_BASE= "190000000000000000"
MESSAGE_IDS=[str(int(MESSAGE_BASE)+i) for i in range(5)]
LINKS="\n".join(
    f"{id} https://discord.com/channels/{GUILD}/{CHANNEL}/{message}"
    for id,message in zip(IDS,MESSAGE_IDS)
)


def raw_message(id, index, *, restored=False):
    msg={
        "id":MESSAGE_IDS[index], "webhook_id":WEBHOOK_ID, "channel_id":CHANNEL,
        "embeds":[{
            "title":f"Missioned Souls video {id}",
            "url":f"https://youtu.be/{id}",
            "description":repair.RECOVERY_MARKER,
            "color":0x1e88e5,
            "timestamp":"2026-10-22T18:10:50Z",
            "fields":[
                {"name":"Channel","value":"The Mr. J Show","inline":True},
                {"name":"Posted","value":"<t:1792692650:t> (<t:1792692650:R>)","inline":True},
            ],
        }],
    }
    if restored:
        msg["embeds"][0]["image"]={"url":f"https://i.ytimg.com/vi/{id}/hqdefault.jpg"}
        msg["embeds"][0]["fields"] += [
            {"name":"Views","value":"123","inline":True},
            {"name":"Likes","value":"4","inline":True},
            {"name":"Comment","value":"1","inline":True},
        ]
    return msg


def yt_item(id):
    return {"id":id,"snippet":{
        "channelId":"UCTR_PSXLloLzLMBVxnt_kLQ",
        "thumbnails":{"high":{"url":f"https://i.ytimg.com/vi/{id}/hqdefault.jpg"}}
    },"status":{"privacyStatus":"public"},
        "statistics":{"viewCount":"123","likeCount":"4","commentCount":"1"}}


def fake_session(*, mismatched_id=None, restored=False):
    session=Mock()
    originals={id:raw_message(id,i,restored=restored)
               for i,id in enumerate(IDS)}
    def get(url,**kwargs):
        response=Mock()
        response.raise_for_status.return_value=None
        if url.endswith("/videos"):
            response.json.return_value={"items":[yt_item(id) for id in IDS]}
        else:
            for id, mid in zip(IDS,MESSAGE_IDS):
                if url.endswith("/messages/"+mid):
                    m=copy.deepcopy(originals[id])
                    if id==mismatched_id:
                        m["embeds"][0]["url"]="https://youtu.be/unrelated"
                    response.json.return_value=m
                    return response
            raise AssertionError("Unexpected GET URL")
        return response
    def patch_request(url, **kwargs):
        response=Mock()
        response.raise_for_status.return_value=None
        for id,mid in zip(IDS,MESSAGE_IDS):
            if url.endswith("/messages/"+mid):
                msg=copy.deepcopy(originals[id])
                msg["embeds"]=kwargs["json"]["embeds"]
                response.json.return_value=msg
                return response
        raise AssertionError("Unexpected PATCH URL")
    session.get.side_effect=get
    session.patch.side_effect=patch_request
    return session


class MrJThumbnailRepairTests(unittest.TestCase):
    def test_exact_five_unique_links_required(self):
        self.assertEqual(set(repair.parse_message_links(LINKS)[1]),set(IDS))
        invalid=[
            LINKS.splitlines()[0],
            LINKS+"\n"+LINKS.splitlines()[0],
            LINKS.replace(IDS[1],IDS[0]),
            LINKS.replace(CHANNEL,"1509325093023519999",1),
            LINKS.replace("discord.com","example.com",1),
            LINKS.replace("190000000000000000","190000000000000001",1),
        ]
        for bad in invalid:
            with self.subTest(raw=bad[:90]),self.assertRaises(ValueError):
                repair.parse_message_links(bad)

    def test_dry_run_never_writes_or_posts(self):
        client=fake_session()
        sent=repair.repair(links=LINKS,webhook_url=WEBHOOK,
                           youtube_api_key="fake",apply=False,session=client)
        self.assertEqual(sent,[])
        self.assertEqual(client.get.call_count,6)
        client.post.assert_not_called()
        client.patch.assert_not_called()
        client.delete.assert_not_called()

    def test_apply_edits_five_existing_messages_only(self):
        client=fake_session()
        result=repair.repair(links=LINKS,webhook_url=WEBHOOK,
                             youtube_api_key="fake",apply=True,session=client)
        self.assertEqual(result,list(IDS))
        self.assertEqual(client.patch.call_count,5)
        client.post.assert_not_called()
        client.delete.assert_not_called()
        for id,call in zip(IDS,client.patch.call_args_list):
            self.assertIn("/messages/",call.args[0])
            embed=call.kwargs["json"]["embeds"][0]
            self.assertEqual(embed["url"],"https://youtu.be/"+id)
            self.assertEqual(embed["image"]["url"],
                             f"https://i.ytimg.com/vi/{id}/hqdefault.jpg")
            self.assertEqual([field["name"] for field in embed["fields"]],
                             ["Channel","Views","Posted","Likes","Comment"])
            self.assertEqual(embed["description"],repair.RECOVERY_MARKER)

    def test_all_five_preflighted_before_first_write(self):
        client=fake_session(mismatched_id=IDS[-1])
        with self.assertRaisesRegex(RuntimeError,"not the exact Mr J recovery"):
            repair.repair(links=LINKS,webhook_url=WEBHOOK,
                          youtube_api_key="fake",apply=True,session=client)
        client.patch.assert_not_called()
        client.post.assert_not_called()

    def test_repaired_messages_skipped_idempotently(self):
        client=fake_session(restored=True)
        self.assertEqual(
            repair.repair(links=LINKS,webhook_url=WEBHOOK,
                          youtube_api_key="fake",apply=True,session=client),[])
        client.patch.assert_not_called()

    def test_wrong_webhook_rejected(self):
        for bad in ("http://discord.com/api/webhooks/"+WEBHOOK_ID+"/token",
                    "https://attacker.example/api/webhooks/"+WEBHOOK_ID+"/token",
                    WEBHOOK+"/unexpected"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                repair.safe_webhook(bad)

    def test_wrong_webhook_origin_fails_before_patch(self):
        client=fake_session()
        original=client.get.side_effect
        def changed(url,**kwargs):
            result=original(url,**kwargs)
            if "/messages/" in url:
                result.json.return_value["webhook_id"]="999999999999999999"
            return result
        client.get.side_effect=changed
        with self.assertRaisesRegex(RuntimeError,"another webhook"):
            repair.repair(links=LINKS,webhook_url=WEBHOOK,
                          youtube_api_key="fake",apply=True,session=client)
        client.patch.assert_not_called()

    def test_missing_youtube_thumbnail_is_fail_closed(self):
        session=fake_session()
        original=session.get.side_effect
        def missing(url,**kwargs):
            result=original(url,**kwargs)
            if url.endswith("/videos"):
                result.json.return_value["items"][-1]["snippet"]["thumbnails"]={}
            return result
        session.get.side_effect=missing
        with self.assertRaisesRegex(RuntimeError,"thumbnail is missing"):
            repair.repair(links=LINKS,webhook_url=WEBHOOK,
                          youtube_api_key="fake",apply=True,session=session)
        session.patch.assert_not_called()


if __name__=="__main__":
    unittest.main()
