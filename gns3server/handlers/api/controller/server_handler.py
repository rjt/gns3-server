# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 GNS3 Technologies Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
from aiohttp.web import HTTPForbidden

from ....web.route import Route
from ....config import Config
from ....modules.project_manager import ProjectManager
from ....schemas.server import SERVER_CREATE_SCHEMA, SERVER_OBJECT_SCHEMA
from ....controller import Controller
from ....controller.server import Server


import logging
log = logging.getLogger(__name__)


class ServerHandler:
    """API entry points for server management."""

    @classmethod
    @Route.post(
        r"/servers",
        description="Register a server",
        status_codes={
            201: "Server added"
        },
        input=SERVER_CREATE_SCHEMA,
        output=SERVER_OBJECT_SCHEMA)
    def create(request, response):

        server = Server(request.json.pop("server_id"), **request.json)
        Controller.instance().addServer(server)

        response.set_status(201)
        response.json(server)

    @classmethod
    @Route.post(
        r"/servers/shutdown",
        description="Shutdown the local server",
        status_codes={
            201: "Server is shutting down",
            403: "Server shutdown refused"
        })
    def shutdown(request, response):

        config = Config.instance()
        if config.get_section_config("Server").getboolean("local", False) is False:
            raise HTTPForbidden(text="You can only stop a local server")

        # close all the projects first
        pm = ProjectManager.instance()
        projects = pm.projects

        tasks = []
        for project in projects:
            tasks.append(asyncio.async(project.close()))

        if tasks:
            done, _ = yield from asyncio.wait(tasks)
            for future in done:
                try:
                    future.result()
                except Exception as e:
                    log.error("Could not close project {}".format(e), exc_info=1)
                    continue

        # then shutdown the server itself
        from gns3server.web.web_server import WebServer
        server = WebServer.instance()
        asyncio.async(server.shutdown_server())
        response.set_status(201)
