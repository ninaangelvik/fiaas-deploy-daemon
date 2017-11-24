#!/usr/bin/env python
# -*- coding: utf-8
from __future__ import absolute_import, unicode_literals

import json
from Queue import Queue

from mock import mock, Mock
import pytest
from k8s.base import WatchEvent
from k8s.client import NotFound
from requests import Response

from fiaas_deploy_daemon.deployer import DeployerEvent
from fiaas_deploy_daemon.crd import CrdWatcher
from fiaas_deploy_daemon.specs.models import AppSpec

ADD_EVENT = {
    "object": {
        "metadata": {
            "labels": {
                "fiaas/deployment_id": "deployment_id"
            },
            "name": "example",
            "namespace": "the-namespace"
        },
        "spec": {
            "application": "example",
            "config": {
                "version": 2,
                "host": "example.com",
                "namespace": "default"
            },
            "image": "example/app"
        }
    },
    "type": WatchEvent.ADDED,
}

MODIFIED_EVENT = {
    "object": ADD_EVENT["object"],
    "type": WatchEvent.MODIFIED,
}

DELETED_EVENT = {
    "object": ADD_EVENT["object"],
    "type": WatchEvent.DELETED,
}


class TestWatcher(object):

    @pytest.fixture
    def spec_factory(self):
        with mock.patch("fiaas_deploy_daemon.specs.factory.SpecFactory") as mockk:
            yield mockk

    @pytest.fixture
    def deploy_queue(self):
        return Queue()

    @pytest.fixture
    def watcher(self, spec_factory, deploy_queue):
        return CrdWatcher(spec_factory, deploy_queue)

    def test_creates_custom_resource_definition_if_not_exists_when_watching_it(self, get, post, watcher):
        get.side_effect = NotFound("Something")

        watcher._watch()

        calls = [
            mock.call("/apis/apiextensions.k8s.io/v1beta1/customresourcedefinitions/", {
                'metadata': {'namespace': 'default', 'name': 'applications.fiaas.schibsted.io'},
                'spec': {
                    'version': 'v1',
                    'group': 'fiaas.schibsted.io',
                    'names': {
                        'shortNames': ['app', 'fa'],
                        'kind': 'Application',
                        'plural': 'applications'
                    }
                }
            }),
            mock.call("/apis/apiextensions.k8s.io/v1beta1/customresourcedefinitions/", {
                'metadata': {'namespace': 'default', 'name': 'statuses.fiaas.schibsted.io'},
                'spec': {
                    'version': 'v1',
                    'group': 'fiaas.schibsted.io',
                    'names': {
                        'shortNames': ['status', 'fs'],
                        'kind': 'Status',
                        'plural': 'statuses'
                    }
                }
            })
        ]
        assert post.call_args_list == calls

    def test_is_able_to_watch_custom_resource_definition(self, get, watcher, deploy_queue):
        response = Response()
        get.return_value = response
        get.side_effect = None

        response.iter_content = Mock(return_value=[json.dumps(ADD_EVENT)])
        response.status_code = Mock(return_value=200)

        assert deploy_queue.qsize() == 0
        watcher._watch()
        assert deploy_queue.qsize() == 1

    @pytest.mark.parametrize("event,deployer_event_type", [
        (ADD_EVENT, "UPDATE"),
        (MODIFIED_EVENT, "UPDATE"),
        (DELETED_EVENT, "DELETE"),
    ])
    def test_deploy(self, get, watcher, deploy_queue, spec_factory, event, deployer_event_type):
        response = Response()
        response.iter_content = Mock(return_value=[json.dumps(event)])
        response.status_code = Mock(return_value=200)
        get.return_value = response
        get.side_effect = None

        app_spec = mock.create_autospec(AppSpec, instance=True, set_spec=True)
        spec_factory.return_value = app_spec

        watcher._watch()

        spec = event["object"]["spec"]
        deployment_id = (event["object"]["metadata"]["labels"]["fiaas/deployment_id"]
                         if deployer_event_type != "DELETE" else None)
        app_config = spec["config"]
        spec_factory.assert_called_once_with(name=spec["application"], image=spec["image"], app_config=app_config,
                                             teams=[], tags=[],
                                             deployment_id=deployment_id,
                                             namespace=event["object"]["metadata"]["namespace"])

        assert deploy_queue.qsize() == 1
        deployer_event = deploy_queue.get_nowait()
        assert deployer_event == DeployerEvent(deployer_event_type, app_spec)
        assert deploy_queue.empty()