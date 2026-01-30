# -*- coding: utf-8 -*-

"""A Python module for dealing with NUT (Network UPS Tools) servers.

* PyNUTError: Base class for custom exceptions.
* PyNUTClient: Allows connecting to and communicating with PyNUT
  servers.

Copyright (C) 2013 george2

Modifications by mezz64 - 2016
Mofifications by Special K - 2026

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import logging

from telnetlib3 import Telnet

__version__ = '2.2.0'
__all__ = ['PyNUTError', 'PyNUTClient']


_LOGGER = logging.getLogger(__name__)


class PyNUTError(Exception):
    """Base class for custom exceptions."""
    pass


class PyNUTClient(object):
    """Access NUT (Network UPS Tools) servers."""

    def __init__(self, host="127.0.0.1", port=3493, login=None, password=None,
                 timeout=5, persistent=True):
        """Class initialization method.

        host        : Host to connect (defaults to 127.0.0.1).
        port        : Port where NUT listens for connections (defaults to 3493)
        login       : Login used to connect to NUT server (defaults to None
                        for no authentication).
        password    : Password used for authentication (defaults to None).
        timeout     : Timeout used to wait for network response (defaults
                        to 5 seconds).
        persistent  : Boolean, when true connection will be made in init method
                        and be held open, when false connection is open/closed
                        when calling each method
        """

        _LOGGER.debug("NUT Class initialization, Host/Port: %s:%s,"
                      " Login: %s/%s", host, port, login, password)

        self._host = host
        self._port = port
        self._login = login
        self._password = password
        self._timeout = timeout
        self._persistent = persistent
        self._srv_handler = None

        if self._persistent:
            self._connect()

    def __del__(self):
        # Try to disconnect cleanly when class is deleted.
        _LOGGER.debug("NUT Class deleted, trying to disconnect.")
        self._disconnect()

    def __enter__(self):
        return self

    def __exit__(self, exc_t, exc_v, trace):
        self.__del__()

    def _disconnect(self):
        """ Disconnects from the defined server."""
        if self._srv_handler:
            try:
                self._write("LOGOUT\n")
                self._srv_handler.close()
            except (OSError, AttributeError):
                # The socket is already disconnected.
                pass

    def _connect(self):
        """Connects to the defined server.

        If login/pass was specified, the class tries to authenticate.
        An error is raised if something goes wrong.
        """
        try:
            self._srv_handler = Telnet(self._host, self._port, timeout=self._timeout)

            if self._login is not None:
                self._write("USERNAME %s\n" % self._login)
                result = self._read_until("\n")
                if not result == "OK\n":
                    raise PyNUTError(result.replace("\n", ""))

            if self._password is not None:
                self._write("PASSWORD %s\n" % self._password)
                result = self._read_until("\n")
                if not result == "OK\n":
                    raise PyNUTError(result.replace("\n", ""))
        except OSError:
            raise PyNUTError("Socket error.")

    def _read_until(self, string):
        """ Wrapper for _srv_handler read_until method."""
        try:
            return self._srv_handler.read_until(string.encode('ascii'),
                                                self._timeout).decode()
        except (EOFError, BrokenPipeError):
            _LOGGER.error("NUT2 problem reading from server.")

    def _write(self, string):
        """ Wrapper for _srv_handler write method."""
        try:
            return self._srv_handler.write(string.encode('ascii'))
        except (EOFError, BrokenPipeError):
            _LOGGER.error("NUT2 problem writing to server.")

    def description(self, ups):
        """Returns the description for a given UPS."""
        _LOGGER.debug("NUT2 requesting description from server %s", self._host)

        if not self._persistent:
            self._connect()

        self._write("GET UPSDESC %s\n" % ups)
        result = self._read_until("\n")

        if not self._persistent:
            self._disconnect()

        try:
            return result.split('"')[1].strip()
        except IndexError:
            raise PyNUTError(result.replace("\n", ""))

    def list_ups(self):
        """Returns the list of available UPS from the NUT server.

        The result is a dictionary containing 'key->val' pairs of
        'UPSName' and 'UPS Description'.
        """
        _LOGGER.debug("NUT2 requesting list_ups from server %s", self._host)

        if not self._persistent:
            self._connect()

        self._write("LIST UPS\n")
        result = self._read_until("\n")
        if result != "BEGIN LIST UPS\n":
            raise PyNUTError(result.replace("\n", ""))

        result = self._read_until("END LIST UPS\n")

        ups_dict = {}
        for line in result.split("\n"):
            if line.startswith("UPS"):
                ups, desc = line[len("UPS "):-len('"')].split('"')[:2]
                ups_dict[ups.strip()] = desc.strip()

        if not self._persistent:
            self._disconnect()

        return ups_dict

    def list_vars(self, ups):
        """Get all available vars from the specified UPS.

        The result is a dictionary containing 'key->val' pairs of all
        available vars.
        """
        _LOGGER.debug("NUT2 requesting list_vars from server %s", self._host)

        if not self._persistent:
            self._connect()

        self._write("LIST VAR %s\n" % ups)
        result = self._read_until("\n")
        if result != "BEGIN LIST VAR %s\n" % ups:
            raise PyNUTError(result.replace("\n", ""))

        result = self._read_until("END LIST VAR %s\n" % ups)
        offset = len("VAR %s " % ups)
        end_offset = 0 - (len("END LIST VAR %s\n" % ups) + 1)

        ups_vars = {}
        for current in result[:end_offset].split("\n"):
            var, data = current[offset:].split('"')[:2]
            ups_vars[var.strip()] = data

        if not self._persistent:
            self._disconnect()

        return ups_vars

    def list_commands(self, ups):
        """Get all available commands for the specified UPS.

        The result is a dict object with command name as key and a description
        of the command as value.
        """
        _LOGGER.debug("NUT2 requesting list_commands from server %s",
                      self._host)

        if not self._persistent:
            self._connect()

        self._write("LIST CMD %s\n" % ups)
        result = self._read_until("\n")
        if result != "BEGIN LIST CMD %s\n" % ups:
            raise PyNUTError(result.replace("\n", ""))

        result = self._read_until("END LIST CMD %s\n" % ups)
        offset = len("CMD %s " % ups)
        end_offset = 0 - (len("END LIST CMD %s\n" % ups) + 1)

        commands = {}
        for current in result[:end_offset].split("\n"):
            command = current[offset:].split('"')[0].strip()

            # For each var we try to get the available description
            try:
                self._write("GET CMDDESC %s %s\n" % (ups, command))
                temp = self._read_until("\n")
                if temp.startswith("CMDDESC"):
                    desc_offset = len("CMDDESC %s %s " % (ups, command))
                    commands[command] = temp[desc_offset:-1].split('"')[1]
                else:
                    commands[command] = command
            except IndexError:
                commands[command] = command

        if not self._persistent:
            self._disconnect()

        return commands

    def list_clients(self, ups=None):
        """Returns the list of connected clients from the NUT server.

        The result is a dictionary containing 'key->val' pairs of
        'UPSName' and a list of clients.
        """
        _LOGGER.debug("NUT2 requesting list_clients from server %s",
                      self._host)

        if not self._persistent:
            self._connect()

        if ups and (ups not in self.list_ups()):
            raise PyNUTError("%s is not a valid UPS" % ups)

        if ups:
            self._write("LIST CLIENTS %s\n" % ups)
        else:
            self._write("LIST CLIENTS\n")
        result = self._read_until("\n")
        if result != "BEGIN LIST CLIENTS\n":
            raise PyNUTError(result.replace("\n", ""))

        result = self._read_until("END LIST CLIENTS\n")

        clients = {}
        for line in result.split("\n"):
            if line.startswith("CLIENT"):
                host, ups = line[len("CLIENT "):].split(' ')[:2]
                if ups not in clients:
                    clients[ups] = []
                clients[ups].append(host)

        if not self._persistent:
            self._disconnect()

        return clients

    def list_rw_vars(self, ups):
        """Get a list of all writable vars from the selected UPS.

        The result is presented as a dictionary containing 'key->val'
        pairs.
        """
        _LOGGER.debug("NUT2 requesting list_rw_vars from server %s",
                      self._host)

        if not self._persistent:
            self._connect()

        self._write("LIST RW %s\n" % ups)
        result = self._read_until("\n")
        if result != "BEGIN LIST RW %s\n" % ups:
            raise PyNUTError(result.replace("\n", ""))

        result = self._read_until("END LIST RW %s\n" % ups)
        offset = len("VAR %s" % ups)
        end_offset = 0 - (len("END LIST RW %s\n" % ups) + 1)

        rw_vars = {}
        for current in result[:end_offset].split("\n"):
            var, data = current[offset:].split('"')[:2]
            rw_vars[var.strip()] = data

        if not self._persistent:
            self._disconnect()

        return rw_vars

    def list_enum(self, ups, var):
        """Get a list of valid values for an enum variable.

        The result is presented as a list.
        """
        _LOGGER.debug("NUT2 requesting list_enum from server %s",
                      self._host)

        if not self._persistent:
            self._connect()

        self._write("LIST ENUM %s %s\n" % (ups, var))
        result = self._read_until("\n")
        if result != "BEGIN LIST ENUM %s %s\n" % (ups, var):
            raise PyNUTError(result.replace("\n", ""))

        result = self._read_until("END LIST ENUM %s %s\n" % (ups, var))
        offset = len("ENUM %s %s" % (ups, var))
        end_offset = 0 - (len("END LIST ENUM %s %s\n" % (ups, var)) + 1)

        if not self._persistent:
            self._disconnect()

        try:
            return [c[offset:].split('"')[1].strip()
                    for c in result[:end_offset].split("\n")]
        except IndexError:
            raise PyNUTError(result.replace("\n", ""))

    def list_range(self, ups, var):
        """Get a list of valid values for an range variable.

        The result is presented as a list.
        """
        _LOGGER.debug("NUT2 requesting list_range from server %s",
                      self._host)

        if not self._persistent:
            self._connect()

        self._write("LIST RANGE %s %s\n" % (ups, var))
        result = self._read_until("\n")
        if result != "BEGIN LIST RANGE %s %s\n" % (ups, var):
            raise PyNUTError(result.replace("\n", ""))

        result = self._read_until("END LIST RANGE %s %s\n" % (ups, var))
        offset = len("RANGE %s %s" % (ups, var))
        end_offset = 0 - (len("END LIST RANGE %s %s\n" % (ups, var)) + 1)

        if not self._persistent:
            self._disconnect()

        try:
            return [c[offset:].split('"')[1].strip()
                    for c in result[:end_offset].split("\n")]
        except IndexError:
            raise PyNUTError(result.replace("\n", ""))

    def set_var(self, ups, var, value):
        """Set a variable to the specified value on selected UPS.

        The variable must be a writable value (cf list_rw_vars) and you
        must have the proper rights to set it (maybe login/password).
        """
        _LOGGER.debug("NUT2 setting set_var '%s' on '%s' to '%s'",
                      var, self._host, value)

        if not self._persistent:
            self._connect()

        self._write("SET VAR %s %s %s\n" % (ups, var, value))
        result = self._read_until("\n")

        if result != "OK\n":
            raise PyNUTError(result.replace("\n", ""))

        if not self._persistent:
            self._disconnect()

    def get_var(self, ups, var):
        """Get the value of a variable."""
        _LOGGER.debug("NUT2 requesting get_var '%s' on '%s'.",
                      var, self._host)

        if not self._persistent:
            self._connect()

        self._write("GET VAR %s %s\n" % (ups, var))
        result = self._read_until("\n")

        if not self._persistent:
            self._disconnect()

        try:
            # result = 'VAR %s %s "%s"\n' % (ups, var, value)
            return result.split('"')[1].strip()
        except IndexError:
            raise PyNUTError(result.replace("\n", ""))

    # Alias for convenience
    def get(self, ups, var):
        """Get the value of a variable (alias for get_var)."""
        return self.get_var(ups, var)

    def var_description(self, ups, var):
        """Get a variable's description."""
        _LOGGER.debug("NUT2 requesting var_description '%s' on '%s'.",
                      var, self._host)

        if not self._persistent:
            self._connect()

        self._write("GET DESC %s %s\n" % (ups, var))
        result = self._read_until("\n")

        if not self._persistent:
            self._disconnect()

        try:
            # result = 'DESC %s %s "%s"\n' % (ups, var, description)
            return result.split('"')[1].strip()
        except IndexError:
            raise PyNUTError(result.replace("\n", ""))

    def var_type(self, ups, var):
        """Get a variable's type."""
        _LOGGER.debug("NUT2 requesting var_type '%s' on '%s'.",
                      var, self._host)

        if not self._persistent:
            self._connect()

        self._write("GET TYPE %s %s\n" % (ups, var))
        result = self._read_until("\n")

        if not self._persistent:
            self._disconnect()

        try:
            # result = 'TYPE %s %s %s\n' % (ups, var, type)
            type_ = ' '.join(result.split(' ')[3:]).strip()
            # Ensure the response was valid.
            assert len(type_) > 0
            assert result.startswith("TYPE")
            return type_
        except AssertionError:
            raise PyNUTError(result.replace("\n", ""))

    def command_description(self, ups, command):
        """Get a command's description."""
        _LOGGER.debug("NUT2 requesting command_description '%s' on '%s'.",
                      command, self._host)

        if not self._persistent:
            self._connect()

        self._write("GET CMDDESC %s %s\n" % (ups, command))
        result = self._read_until("\n")

        if not self._persistent:
            self._disconnect()

        try:
            # result = 'CMDDESC %s %s "%s"' % (ups, command, description)
            return result.split('"')[1].strip()
        except IndexError:
            raise PyNUTError(result.replace("\n", ""))

    def run_command(self, ups, command):
        """Send a command to the specified UPS."""
        _LOGGER.debug("NUT2 run_command called '%s' on '%s'.",
                      command, self._host)

        if not self._persistent:
            self._connect()

        self._write("INSTCMD %s %s\n" % (ups, command))
        result = self._read_until("\n")

        if result != "OK\n":
            raise PyNUTError(result.replace("\n", ""))

        if not self._persistent:
            self._disconnect()

    def fsd(self, ups):
        """Send MASTER and FSD commands."""
        _LOGGER.debug("NUT2 MASTER called on '%s'.", self._host)

        if not self._persistent:
            self._connect()

        self._write("MASTER %s\n" % ups)
        result = self._read_until("\n")
        if result != "OK MASTER-GRANTED\n":
            raise PyNUTError(("Master level function are not available", ""))

        _LOGGER.debug("FSD called...")
        self._write("FSD %s\n" % ups)
        result = self._read_until("\n")
        if result != "OK FSD-SET\n":
            raise PyNUTError(result.replace("\n", ""))

        if not self._persistent:
            self._disconnect()

    def num_logins(self, ups):
        """Send GET NUMLOGINS command to get the number of users logged
        into a given UPS.
        """
        _LOGGER.debug("NUT2 requesting num_logins called on '%s'", self._host)

        if not self._persistent:
            self._connect()

        self._write("GET NUMLOGINS %s\n" % ups)
        result = self._read_until("\n")

        if not self._persistent:
            self._disconnect()

        try:
            # result = "NUMLOGINS %s %s\n" % (ups, int(numlogins))
            return int(result.split(' ')[2].strip())
        except (ValueError, IndexError):
            raise PyNUTError(result.replace("\n", ""))

    def help(self):
        """Send HELP command."""
        _LOGGER.debug("NUT2 HELP called on '%s'", self._host)

        if not self._persistent:
            self._connect()

        self._write("HELP\n")

        if not self._persistent:
            self._disconnect()

        return self._read_until("\n")

    def ver(self):
        """Send VER command."""
        _LOGGER.debug("NUT2 VER called on '%s'", self._host)

        if not self._persistent:
            self._connect()

        self._write("VER\n")

        if not self._persistent:
            self._disconnect()

        return self._read_until("\n")
