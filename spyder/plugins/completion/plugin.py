
# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Backend plugin to manage multiple code completion and introspection clients.
"""

# Standard library imports
import logging
import os
import os.path as osp
import functools

# Third-party imports
from qtpy.QtCore import QObject, Slot, QTimer

# Local imports
from spyder.config.base import get_conf_path, running_under_pytest
from spyder.config.lsp import PYTHON_CONFIG
from spyder.config.main import CONF
from spyder.api.completion import SpyderCompletionPlugin
from spyder.utils.misc import select_port, getcwd_or_home
from spyder.plugins.languageserver.plugin import LanguageServerPlugin
from spyder.plugins.fallback.plugin import FallbackPlugin
from spyder.plugins.languageserver import LSPRequestTypes
# from spyder.plugins.languageserver.confpage import LanguageServerConfigPage


logger = logging.getLogger(__name__)


class CompletionPlugin(SpyderCompletionPlugin):
    STOPPED = 'stopped'
    RUNNING = 'running'

    def __init__(self, parent):
        SpyderCompletionPlugin.__init__(self, parent)
        self.clients = {}
        self.requests = {}
        self.started = False
        self.first_completion = False
        self.req_id = 0
        self.completion_first_time = 500
        self.waiting_time = 1000

        self.plugin_priority = {
            LSPRequestTypes.DOCUMENT_COMPLETION: 'lsp',
            LSPRequestTypes.DOCUMENT_SIGNATURE: 'lsp',
            'all': 'lsp'
        }

        lsp_client = LanguageServerPlugin(self.main)
        fallback = FallbackPlugin(self.main)
        self.register_completion_plugin(lsp_client)
        self.register_completion_plugin(fallback)

    def register_completion_plugin(self, plugin):
        logger.debug("Completion plugin: Registering {0}".format(
            plugin.COMPLETION_CLIENT_NAME))
        plugin_name = plugin.COMPLETION_CLIENT_NAME
        self.clients[plugin_name] = {
            'plugin': plugin,
            'status': self.STOPPED
        }
        plugin.sig_response_ready.connect(self.recieve_response)
        plugin.sig_plugin_ready.connect(self.client_available)

    @Slot(str, int, dict)
    def recieve_response(self, completion_source, req_id, resp):
        logger.debug("Completion plugin: Request {0} Got response "
                     "from {1}".format(req_id, completion_source))
        request_responses = self.requests[req_id]
        req_type = request_responses['req_type']
        request_responses['sources'][completion_source] = resp
        corresponding_source = self.plugin_priority.get(req_type, 'lsp')
        if corresponding_source == completion_source:
            response_instance = request_responses['response_instance']
            self.gather_and_send(response_instance, req_type, req_id)

    @Slot(str)
    def client_available(self, client_name):
        client_info = self.clients[client_name]
        client_info['status'] = self.RUNNING

    def gather_and_send(self, response_instance, req_type, req_id):
        logger.debug('Gather responses for {0}'.format(req_type))
        responses = []
        req_id_responses = self.requests[req_id]['sources']
        if req_type == LSPRequestTypes.DOCUMENT_COMPLETION:
            principal_source = self.plugin_priority[req_type]
            responses = req_id_responses[principal_source]['params']
            available_completions = {x['insertText'] for x in responses}
            priority_level = 1
            for source in req_id_responses:
                logger.debug(source)
                if source == principal_source:
                    continue
                source_responses = req_id_responses[source]['params']
                for response in source_responses:
                    if response['insertText'] not in available_completions:
                        response['sortText'] = (
                            'z' + 'z' * priority_level + response['sortText'])
                        responses.append(response)
                priority_level += 1
            responses = {'params': responses}
        else:
            principal_source = self.plugin_priority['all']
            responses = req_id_responses[principal_source]
        response_instance.handle_response(req_type, responses)

    def send_request(self, language, req_type, req, req_id=None):
        req_id = self.req_id
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                self.requests[req_id] = {
                    'req_type': req_type,
                    'response_instance': req['response_instance'],
                    'language': language,
                    'sources': {}
                }
                client_info['plugin'].send_request(
                    language, req_type, req, req_id)
        if req['requires_response']:
            self.req_id += 1

    def send_notification(self, language, notification_type, notification):
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                client_info['plugin'].send_notification(
                    language, notification_type, notification)

    def broadcast_notification(self, req_type, req):
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                client_info['plugin'].broadcast_notification(
                    req_type, req)

    def project_path_update(self, project_path, update_kind='addition'):
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                client_info['plugin'].project_path_update(
                    project_path, update_kind
                )

    def register_file(self, language, filename, codeeditor):
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                client_info['plugin'].register_file(
                    language, filename, codeeditor
                )

    def start(self):
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.STOPPED:
                client_info['plugin'].start()

    def shutdown(self):
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                client_info['plugin'].shutdown()

    def start_client(self, language):
        started = False
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                started |= client_info['plugin'].start_client(language)
        return started

    def stop_client(self, language):
        for client_name in self.clients:
            client_info = self.clients[client_name]
            if client_info['status'] == self.RUNNING:
                client_info['plugin'].stop_client(language)

    def __getattr__(self, name):
        if name in self.clients:
            return self.clients[name]['plugin']
        else:
            return super().__getattr__(name)
