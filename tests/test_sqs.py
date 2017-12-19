# Copyright 2017 Capital One Services, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import absolute_import, division, print_function, unicode_literals

from .common import BaseTest, functional
from botocore.exceptions import ClientError
import json, time



class TestSqsAction(BaseTest):

    def cleanup_queue(self, client, name, url):
        if url in client.list_queues(QueueNamePrefix=name).get('QueueUrls', []):
            client.delete_queue(QueueUrl=url)

    def create_queue(self, client, name):
        queue_url = client.create_queue(QueueName=name)['QueueUrl']

        # Sometimes SQS has a delay before queue shows up
        counter = 0
        while True:
            try:
                queue = client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['All'])
            except ClientError:
                time.sleep(5)
                counter += 1
                if counter > 12:
                    self.fail('SQS queue was not created')
                continue
            break
            
        self.addCleanup(self.cleanup_queue, client, name, queue_url)
        return queue_url

    @functional
    def test_sqs_delete(self):
        session_factory = self.replay_flight_data('test_sqs_delete')
        client = session_factory().client('sqs')
        queue_url = self.create_queue(client, 'test-sqs-delete')

        p = self.load_policy({
            'name': 'sqs-delete',
            'resource': 'sqs',
            'filters': [{'QueueUrl': queue_url}],
            'actions': [
                {'type': 'delete'}]},
            session_factory=session_factory)
        resources = p.run()
        self.assertEqual(len(resources), 1)
        self.assertRaises(
            ClientError,
            client.purge_queue, QueueUrl=queue_url)


    @functional
    def test_sqs_set_encryption(self):
        session_factory = self.replay_flight_data('test_sqs_set_encryption')
        client_sqs = session_factory().client('sqs')
        queue_url = self.create_queue(client_sqs, 'test-sqs-set-encryption')

        client_kms = session_factory().client('kms')
        key_id = client_kms.create_key(Description='West SQS encryption key')['KeyMetadata']['KeyId']
        client_kms.create_alias(
            AliasName='alias/new-key-test-sqs',
            TargetKeyId=key_id)
        self.addCleanup(client_kms.disable_key, KeyId=key_id)

        p = self.load_policy({
            'name': 'sqs-delete',
            'resource': 'sqs',
            'filters': [{'QueueUrl': queue_url}],
            'actions': [
                {'type': 'set-encryption',
                 'key': 'new-key-test-sqs'}]},
            session_factory=session_factory)
        resources = p.run()

        check_master_key = client_sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['All'])['Attributes']['KmsMasterKeyId']
        self.assertEqual(check_master_key, 'c4816d44-73c3-4eed-a7cc-d52a74fa3294')


    @functional
    def test_sqs_remove_matched(self):
        session_factory = self.replay_flight_data('test_sqs_remove_matched')
        client = session_factory().client('sqs')
        queue_url = self.create_queue(client, 'test-sqs-remove-matched-1')

        client.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={'Policy':json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "SpecificAllow",
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": "arn:aws:iam::123456789012:root"
                        },
                        "Action": [
                            "sqs:Subscribe"
                        ]
                    },
                    {
                        "Sid": "Public",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": [
                            "sqs:GetqueueAttributes"
                        ]
                    }
                ]
            })}
        )

        p = self.load_policy({
            'name': 'sqs-rm-matched',
            'resource': 'sqs',
            'filters': [
                {'QueueUrl': queue_url},
                {'type': 'cross-account',
                 'whitelist': ["123456789012"]}
            ],
            'actions': [
                {'type': 'remove-statements',
                 'statement_ids': 'matched'}]
            },
            session_factory=session_factory)
        resources = p.run()

        self.assertEqual([r['QueueUrl'] for r in resources], [queue_url])

        data = json.loads(client.get_queue_attributes(QueueUrl=resources[0]['QueueUrl'], AttributeNames=['Policy'])['Attributes']['Policy'])
        self.assertEqual(
            [s['Sid'] for s in data.get('Statement', ())],
            ['SpecificAllow'])


    @functional
    def test_sqs_remove_named(self):
        session_factory = self.replay_flight_data('test_sqs_remove_named')
        client = session_factory().client('sqs')
        queue_url = self.create_queue(client, 'test-sqs-remove-named')

        client.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={'Policy':json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "SpecificAllow",
                        "Effect": "Allow",
                        "Principal": {
                            "AWS": "arn:aws:iam::644160558196:root"
                        },
                        "Action": ["sqs:Subscribe"]
                    },
                    {
                        "Sid": "RemoveMe",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": ["sqs:GetqueueAttributes"]
                    }
                ]
            })}
        )

        p = self.load_policy({
            'name': 'sqs-rm-named',
            'resource': 'sqs',
            'filters': [{'QueueUrl': queue_url}],
            'actions': [
                {'type': 'remove-statements',
                 'statement_ids': ['RemoveMe']}]
            },
            session_factory=session_factory)

        resources = p.run()
        self.assertEqual(len(resources), 1)

        data = json.loads(client.get_queue_attributes(QueueUrl=resources[0]['QueueUrl'], AttributeNames=['Policy'])['Attributes']['Policy'])
        self.assertTrue('RemoveMe' not in [s['Sid'] for s in data.get('Statement', ())])

    def test_sqs_remove_all(self):
        factory = self.replay_flight_data('test_sqs_remove_named_all')
        queue_url = 'https://queue.amazonaws.com/644160558196/test-sqs-remove-named'
        p = self.load_policy({
            'name': 'sqs-rm-all',
            'resource': 'sqs',
            'filters': [{'QueueUrl': queue_url}],
            'actions': [
                {'type': 'remove-statements',
                 'statement_ids': ['RemoveMe']}]
            }, session_factory=factory)

        resources = p.run()
        self.assertEqual(len(resources), 1)
        client = factory().client('sqs')
        d2 = client.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['All'])['Attributes']
        self.assertNotIn('Policy', d2)
        

    @functional
    def test_sqs_mark_for_op(self):
        session_factory = self.replay_flight_data('test_sqs_mark_for_op')
        client = session_factory().client('sqs')
        queue_url = self.create_queue(client, 'test-sqs-mark-for-op')

        p = self.load_policy({
            'name': 'sqs-mark-for-op',
            'resource': 'sqs',
            'filters': [{'QueueUrl': queue_url}],
            'actions': [
                {'type': 'mark-for-op',
                 'tag': 'tag-for-op',
                 'op': 'delete',
                 'days': 1}]
        },
            session_factory=session_factory)

        resources = p.run()
        self.assertEqual(len(resources), 1)
        tags_after_run = client.list_queue_tags(
            QueueUrl=queue_url).get('Tags', {})
        self.assertTrue("tag-for-op" in tags_after_run)

    @functional
    def test_sqs_tag(self):
        session_factory = self.replay_flight_data('test_sqs_tags')
        client = session_factory().client('sqs')
        queue_url = self.create_queue(client, 'test-sqs-tag')

        p = self.load_policy({
            'name': 'sqs-mark-for-op',
            'resource': 'sqs',
            'filters': [{'QueueUrl': queue_url}],
            'actions': [
                {'type': 'tag',
                 'key': 'tag-this-queue',
                 'value': 'This queue has been tagged'}]},
            session_factory=session_factory)

        resources = p.run()
        self.assertEqual(len(resources), 1)
        tags_after_run = client.list_queue_tags(
            QueueUrl=queue_url).get('Tags', {})
        self.assertTrue("tag-this-queue" in tags_after_run)

    @functional
    def test_sqs_remove_tag(self):
        session_factory = self.replay_flight_data('test_sqs_remove_tag')
        client = session_factory().client('sqs')
        queue_url = self.create_queue(client, 'test-sqs-remove-tag')
        client.tag_queue(
            QueueUrl=queue_url,
            Tags={
                'remove-this-tag': 'tag to be removed'
            })

        p = self.load_policy({
            'name': 'sqs-mark-for-op',
            'resource': 'sqs',
            'filters': [{'QueueUrl': queue_url}],
            'actions': [
                {'type': 'remove-tag',
                 'tags': ['remove-this-tag']}]},
            session_factory=session_factory)

        resources = p.run()
        self.assertEqual(len(resources), 1)
        tags_after_run = client.list_queue_tags(
            QueueUrl=queue_url).get('Tags', {})
        self.assertTrue("remove-this-tag" not in tags_after_run)

    @functional
    def test_sqs_marked_for_op(self):
        session_factory = self.replay_flight_data('test_sqs_marked_for_op')
        client = session_factory().client('sqs')
        queue_url = self.create_queue(client, 'test-sqs-marked-for-op')
        client.tag_queue(
            QueueUrl=queue_url,
            Tags={
                'tag-for-op': 'Resource does not meet policy: delete@2017/11/01'
            })

        p = self.load_policy({
            'name': 'sqs-marked-for-op',
            'resource': 'sqs',
            'filters': [
                {'type': 'marked-for-op', 'tag': 'tag-for-op',
                 'op': 'delete'}]},
            session_factory=session_factory)

        resources = p.run()
        self.assertEqual(len(resources), 1)
