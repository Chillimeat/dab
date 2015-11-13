"""

This code was originally extracted from pysmb and was adapted for asyncio.

Copyright (C) 2001-2015 Michael Teo <miketeo (a) miketeo.net>
Copyright (C) 2015-     Delve Labs inc. <info@delvelabs.ca>

This software is provided 'as-is', without any express or implied warranty.
In no event will the author be held liable for any damages arising from the
use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

1. The origin of this software must not be misrepresented; you must not
   claim that you wrote the original software. If you use this software
   in a product, an acknowledgment in the product documentation would be
   appreciated but is not required.

2. Altered source versions must be plainly marked as such, and must not be
   misrepresented as being the original software.

3. This notice cannot be removed or altered from any source distribution.

"""
import re
import random
import select
import struct
import string
import time
import socket


class NetBIOS:

    TYPE_SERVER = 0x20
    HEADER_STRUCT_FORMAT = '>HHHHHH'
    HEADER_STRUCT_SIZE = struct.calcsize(HEADER_STRUCT_FORMAT)


    def __init__(self, broadcast=True, listen_port=0):
        """
        Instantiate a NetBIOS instance, and creates a IPv4 UDP socket to listen/send NBNS packets.

        :param boolean broadcast: A boolean flag to indicate if we should setup the listening UDP port in broadcast mode
        :param integer listen_port: Specifies the UDP port number to bind to for listening. If zero, OS will automatically select a free port number.
        """
        self.broadcast = broadcast
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.broadcast:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if listen_port:
            self.sock.bind(('', listen_port))

    def close(self):
        """
        Close the underlying and free resources.

        The NetBIOS instance should not be used to perform any operations after this method returns.

        :return: None
        """
        self.sock.close()
        self.sock = None

    def _write(self, data, ip, port):
        assert self.sock, 'Socket is already closed'
        self.sock.sendto(data, (ip, port))

    def _encode_name(self, name, type, scope=None):
        #
        # Contributed by Jason Anderson
        #
        """
        Perform first and second level encoding of name as specified in RFC 1001 (Section 4)
        """
        if name == '*':
            name = name + '\0' * 15
        elif len(name) > 15:
            name = name[:15] + chr(type)
        else:
            name = name.ljust(15) + chr(type)

        def _do_first_level_encoding(m):
            s = ord(m.group(0))
            return string.ascii_uppercase[s >> 4] + string.ascii_uppercase[s & 0x0f]

        encoded_name = chr(len(name) * 2) + re.sub('.', _do_first_level_encoding, name)
        if scope:
            encoded_scope = ''
            for s in string.split(scope, '.'):
                encoded_scope = encoded_scope + chr(len(s)) + s
            return bytes(encoded_name + encoded_scope + '\0', 'ascii')
        else:
            return bytes(encoded_name + '\0', 'ascii')

    def _prepare_net_name_query(self, trn_id, is_broadcast=True):
        #
        # Contributed by Jason Anderson
        #
        header = struct.pack(self.HEADER_STRUCT_FORMAT,
                             trn_id, (is_broadcast and 0x0010) or 0x0000, 1, 0, 0, 0)
        payload = self._encode_name('*', 0) + b'\x00\x21\x00\x01'

        return header + payload

    def query_ip_for_name(self, ip, port=137, timeout=30):
        """
        Send a query to the machine with *ip* and hopes that the machine will reply back with its name.

        The implementation of this function is contributed by Jason Anderson.

        :param string ip: If the NBNSProtocol instance was instianted with broadcast=True, then this parameter can be an empty string. We will leave it to the OS to determine an appropriate broadcast address.
                          If the NBNSProtocol instance was instianted with broadcast=False, then you should provide a target IP to send the query.
        :param integer port: The NetBIOS-NS port (IANA standard defines this port to be 137). You should not touch this parameter unless you know what you are doing.
        :param integer/float timeout: Number of seconds to wait for a reply, after which the method will return None
        :return: A list of string containing the names of the machine at *ip*. On timeout, returns None.
        """
        assert self.sock, 'Socket is already closed'

        trn_id = random.randint(1, 0xFFFF)
        data = self._prepare_net_name_query(trn_id, False)
        self._write(data, ip, port)
        ret = self._poll_for_query_packet(trn_id, timeout)
        if ret:
            return list(map(lambda s: s[0], filter(lambda s: s[1] == self.TYPE_SERVER, ret)))
        else:
            return None

    def _poll_for_query_packet(self, wait_trn_id, timeout):
        #
        # Contributed by Jason Anderson
        #
        end_time = time.time() + timeout
        while True:
            try:
                _current = time.time()
                if _current >= end_time:
                    return None

                ready, _, _ = select.select([self.sock.fileno()], [], [], end_time - _current)
                if not ready:
                    return None

                data, _ = self.sock.recvfrom(0xFFFF)
                if len(data) == 0:
                    raise NotConnectedError

                trn_id, ret = self._decode_ip_query_packet(data)

                if trn_id == wait_trn_id:
                    return ret
            except select.error as ex:
                if type(ex) is tuple:
                    if ex[0] != errno.EINTR and ex[0] != errno.EAGAIN:
                        raise ex
                else:
                    raise ex

    def _decode_ip_query_packet(self, data):
        if len(data) < self.HEADER_STRUCT_SIZE:
            raise Exception

        trn_id, code, question_count, answer_count, authority_count, additional_count = struct.unpack(self.HEADER_STRUCT_FORMAT, data[:self.HEADER_STRUCT_SIZE])

        is_response = bool((code >> 15) & 0x01)
        opcode = (code >> 11) & 0x0F
        flags = (code >> 4) & 0x7F
        rcode = code & 0x0F
        numnames = data[self.HEADER_STRUCT_SIZE + 44]

        if numnames > 0:
            ret = []
            offset = self.HEADER_STRUCT_SIZE + 45

            for i in range(0, numnames):
                mynme = data[offset:offset + 15]
                mynme = mynme.strip()
                ret.append((str(mynme, 'ascii'), data[offset+15]))
                offset += 18

            return trn_id, ret
        else:
            return trn_id, None


