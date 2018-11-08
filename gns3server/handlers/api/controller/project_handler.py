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

import os
import sys
import aiohttp
import asyncio
import tempfile

from gns3server.web.route import Route
from gns3server.controller import Controller
from gns3server.controller.import_project import import_project
from gns3server.controller.export_project import export_project
from gns3server.config import Config


from gns3server.schemas.project import (
    PROJECT_OBJECT_SCHEMA,
    PROJECT_UPDATE_SCHEMA,
    PROJECT_LOAD_SCHEMA,
    PROJECT_CREATE_SCHEMA
)

import logging
log = logging.getLogger()


async def process_websocket(ws):
    """
    Process ping / pong and close message
    """
    try:
        await ws.receive()
    except aiohttp.WSServerHandshakeError:
        pass


class ProjectHandler:

    @Route.post(
        r"/projects",
        description="Create a new project on the server",
        status_codes={
            201: "Project created",
            409: "Project already created"
        },
        output=PROJECT_OBJECT_SCHEMA,
        input=PROJECT_CREATE_SCHEMA)
    async def create_project(request, response):

        controller = Controller.instance()
        project = await controller.add_project(**request.json)
        response.set_status(201)
        response.json(project)

    @Route.get(
        r"/projects",
        description="List projects",
        status_codes={
            200: "List of projects",
        })
    def list_projects(request, response):
        controller = Controller.instance()
        response.json([p for p in controller.projects.values()])

    @Route.get(
        r"/projects/{project_id}",
        description="Get a project",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            200: "Project information returned",
            404: "The project doesn't exist"
        })
    def get(request, response):
        controller = Controller.instance()
        project = controller.get_project(request.match_info["project_id"])
        response.json(project)

    @Route.put(
        r"/projects/{project_id}",
        status_codes={
            200: "Node updated",
            400: "Invalid request",
            404: "Instance doesn't exist"
        },
        description="Update a project instance",
        input=PROJECT_UPDATE_SCHEMA,
        output=PROJECT_OBJECT_SCHEMA)
    async def update(request, response):
        project = Controller.instance().get_project(request.match_info["project_id"])

        # Ignore these because we only use them when creating a project
        request.json.pop("project_id", None)

        await project.update(**request.json)
        response.set_status(200)
        response.json(project)

    @Route.delete(
        r"/projects/{project_id}",
        description="Delete a project from disk",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            204: "Changes have been written on disk",
            404: "The project doesn't exist"
        })
    async def delete(request, response):

        controller = Controller.instance()
        project = controller.get_project(request.match_info["project_id"])
        await project.delete()
        controller.remove_project(project)
        response.set_status(204)

    @Route.get(
        r"/projects/{project_id}/stats",
        description="Get a project statistics",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            200: "Project statistics returned",
            404: "The project doesn't exist"
        })
    def get(request, response):
        controller = Controller.instance()
        project = controller.get_project(request.match_info["project_id"])
        response.json(project.stats())

    @Route.post(
        r"/projects/{project_id}/close",
        description="Close a project",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            204: "The project has been closed",
            404: "The project doesn't exist"
        },
        output=PROJECT_OBJECT_SCHEMA)
    async def close(request, response):

        controller = Controller.instance()
        project = controller.get_project(request.match_info["project_id"])
        await project.close()
        response.set_status(201)
        response.json(project)

    @Route.post(
        r"/projects/{project_id}/open",
        description="Open a project",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            201: "The project has been opened",
            404: "The project doesn't exist"
        },
        output=PROJECT_OBJECT_SCHEMA)
    async def open(request, response):

        controller = Controller.instance()
        project = controller.get_project(request.match_info["project_id"])
        await project.open()
        response.set_status(201)
        response.json(project)

    @Route.post(
        r"/projects/load",
        description="Open a project (only local server)",
        parameters={
            "path": ".gns3 path",
        },
        status_codes={
            201: "The project has been opened",
            403: "The server is not the local server"
        },
        input=PROJECT_LOAD_SCHEMA,
        output=PROJECT_OBJECT_SCHEMA)
    async def load(request, response):

        controller = Controller.instance()
        config = Config.instance()
        if config.get_section_config("Server").getboolean("local", False) is False:
            log.error("Can't load the project the server is not started with --local")
            response.set_status(403)
            return
        project = await controller.load_project(request.json.get("path"),)
        response.set_status(201)
        response.json(project)

    @Route.get(
        r"/projects/{project_id}/notifications",
        description="Receive notifications about projects",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            200: "End of stream",
            404: "The project doesn't exist"
        })
    async def notification(request, response):

        controller = Controller.instance()
        project = controller.get_project(request.match_info["project_id"])

        response.content_type = "application/json"
        response.set_status(200)
        response.enable_chunked_encoding()

        await response.prepare(request)
        with controller.notification.project_queue(project) as queue:
            while True:
                try:
                    msg = await queue.get_json(5)
                    await response.write(("{}\n".format(msg)).encode("utf-8"))
                except asyncio.futures.CancelledError as e:
                    break

        if project.auto_close:
            # To avoid trouble with client connecting disconnecting we sleep few seconds before checking
            # if someone else is not connected
            await asyncio.sleep(5)
            if not controller.notification.project_has_listeners(project):
                await project.close()

    @Route.get(
        r"/projects/{project_id}/notifications/ws",
        description="Receive notifications about projects from a Websocket",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            200: "End of stream",
            404: "The project doesn't exist"
        })
    async def notification_ws(request, response):

        controller = Controller.instance()
        project = controller.get_project(request.match_info["project_id"])

        ws = aiohttp.web.WebSocketResponse()
        await ws.prepare(request)

        request.app['websockets'].add(ws)
        asyncio.ensure_future(process_websocket(ws))
        with controller.notification.project_queue(project) as queue:
            while True:
                try:
                    notification = await queue.get_json(5)
                    if ws.closed:
                        break
                    await ws.send_str(notification)
                except asyncio.futures.CancelledError:
                    break
                finally:
                    request.app['websockets'].discard(ws)

        if project.auto_close:
            # To avoid trouble with client connecting disconnecting we sleep few seconds before checking
            # if someone else is not connected
            await asyncio.sleep(5)
            if not controller.notification.project_has_listeners(project):
                await project.close()

        return ws

    @Route.get(
        r"/projects/{project_id}/export",
        description="Export a project as a portable archive",
        parameters={
            "project_id": "Project UUID",
        },
        raw=True,
        status_codes={
            200: "File returned",
            404: "The project doesn't exist"
        })
    async def export_project(request, response):

        controller = Controller.instance()
        project = await controller.get_loaded_project(request.match_info["project_id"])

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                stream = await export_project(project,
                                                   tmp_dir,
                                                   include_images=bool(int(request.query.get("include_images", "0"))))
                # We need to do that now because export could failed and raise an HTTP error
                # that why response start need to be the later possible
                response.content_type = 'application/gns3project'
                response.headers['CONTENT-DISPOSITION'] = 'attachment; filename="{}.gns3project"'.format(project.name)
                response.enable_chunked_encoding()
                await response.prepare(request)

                for data in stream:
                    await response.write(data)

                #await response.write_eof() #FIXME: shound't be needed anymore
        # Will be raise if you have no space left or permission issue on your temporary directory
        # RuntimeError: something was wrong during the zip process
        except (ValueError, OSError, RuntimeError) as e:
            raise aiohttp.web.HTTPNotFound(text="Cannot export project: {}".format(str(e)))

    @Route.post(
        r"/projects/{project_id}/import",
        description="Import a project from a portable archive",
        parameters={
            "project_id": "Project UUID",
        },
        raw=True,
        output=PROJECT_OBJECT_SCHEMA,
        status_codes={
            200: "Project imported",
            403: "Forbidden to import project"
        })
    async def import_project(request, response):

        controller = Controller.instance()

        if request.get("path"):
            config = Config.instance()
            if config.get_section_config("Server").getboolean("local", False) is False:
                response.set_status(403)
                return
        path = request.json.get("path")
        name = request.json.get("name")

        # We write the content to a temporary location and after we extract it all.
        # It could be more optimal to stream this but it is not implemented in Python.
        # Spooled means the file is temporary kept in memory until max_size is reached
        # Cannot use tempfile.SpooledTemporaryFile(max_size=10000) in Python 3.7 due
        # to a bug https://bugs.python.org/issue26175
        try:
            if sys.version_info >= (3, 7) and sys.version_info < (3, 8):
                with tempfile.TemporaryFile() as temp:
                    while True:
                        chunk = await request.content.read(1024)
                        if not chunk:
                            break
                        temp.write(chunk)
                    project = await import_project(controller, request.match_info["project_id"], temp, location=path, name=name)
            else:
                with tempfile.SpooledTemporaryFile(max_size=10000) as temp:
                    while True:
                        chunk = await request.content.read(1024)
                        if not chunk:
                            break
                        temp.write(chunk)
                    project = await import_project(controller, request.match_info["project_id"], temp, location=path, name=name)
        except OSError as e:
            raise aiohttp.web.HTTPInternalServerError(text="Could not import the project: {}".format(e))

        response.json(project)
        response.set_status(201)

    @Route.post(
        r"/projects/{project_id}/duplicate",
        description="Duplicate a project",
        parameters={
            "project_id": "Project UUID",
        },
        input=PROJECT_CREATE_SCHEMA,
        output=PROJECT_OBJECT_SCHEMA,
        status_codes={
            201: "Project duplicate",
            403: "The server is not the local server",
            404: "The project doesn't exist"
        })
    async def duplicate(request, response):

        controller = Controller.instance()
        project = await controller.get_loaded_project(request.match_info["project_id"])

        if request.json.get("path"):
            config = Config.instance()
            if config.get_section_config("Server").getboolean("local", False) is False:
                response.set_status(403)
                return
            location = request.json.get("path")
        else:
            location = None

        new_project = await project.duplicate(name=request.json.get("name"), location=location)

        response.json(new_project)
        response.set_status(201)

    @Route.get(
        r"/projects/{project_id}/files/{path:.+}",
        description="Get a file from a project. Beware you have warranty to be able to access only to file global to the project (for example README.txt)",
        parameters={
            "project_id": "Project UUID",
        },
        status_codes={
            200: "File returned",
            403: "Permission denied",
            404: "The file doesn't exist"
        })
    async def get_file(request, response):

        controller = Controller.instance()
        project = await controller.get_loaded_project(request.match_info["project_id"])
        path = request.match_info["path"]
        path = os.path.normpath(path).strip('/')

        # Raise error if user try to escape
        if path[0] == ".":
            raise aiohttp.web.HTTPForbidden()
        path = os.path.join(project.path, path)

        response.content_type = "application/octet-stream"
        response.set_status(200)
        response.enable_chunked_encoding()

        try:
            with open(path, "rb") as f:
                await response.prepare(request)
                while True:
                    data = f.read(4096)
                    if not data:
                        break
                    await response.write(data)

        except FileNotFoundError:
            raise aiohttp.web.HTTPNotFound()
        except PermissionError:
            raise aiohttp.web.HTTPForbidden()

    @Route.post(
        r"/projects/{project_id}/files/{path:.+}",
        description="Write a file to a project",
        parameters={
            "project_id": "Project UUID",
        },
        raw=True,
        status_codes={
            200: "File returned",
            403: "Permission denied",
            404: "The path doesn't exist"
        })
    async def write_file(request, response):

        controller = Controller.instance()
        project = await controller.get_loaded_project(request.match_info["project_id"])
        path = request.match_info["path"]
        path = os.path.normpath(path).strip("/")

        # Raise error if user try to escape
        if path[0] == ".":
            raise aiohttp.web.HTTPForbidden()
        path = os.path.join(project.path, path)

        response.set_status(200)

        try:
            with open(path, 'wb+') as f:
                while True:
                    try:
                        chunk = await request.content.read(1024)
                    except asyncio.TimeoutError:
                        raise aiohttp.web.HTTPRequestTimeout(text="Timeout when writing to file '{}'".format(path))
                    if not chunk:
                        break
                    f.write(chunk)
        except FileNotFoundError:
            raise aiohttp.web.HTTPNotFound()
        except PermissionError:
            raise aiohttp.web.HTTPForbidden()
        except OSError as e:
            raise aiohttp.web.HTTPConflict(text=str(e))
