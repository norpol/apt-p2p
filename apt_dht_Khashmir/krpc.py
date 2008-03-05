## Copyright 2002-2003 Andrew Loewenstern, All Rights Reserved
# see LICENSE.txt for license information

"""The KRPC communication protocol implementation.

@var KRPC_TIMEOUT: the number of seconds after which requests timeout
@var UDP_PACKET_LIMIT: the maximum number of bytes that can be sent in a
    UDP packet without fragmentation

@var KRPC_ERROR: the code for a generic error
@var KRPC_ERROR_SERVER_ERROR: the code for a server error
@var KRPC_ERROR_MALFORMED_PACKET: the code for a malformed packet error
@var KRPC_ERROR_METHOD_UNKNOWN: the code for a method unknown error
@var KRPC_ERROR_MALFORMED_REQUEST: the code for a malformed request error
@var KRPC_ERROR_INVALID_TOKEN: the code for an invalid token error
@var KRPC_ERROR_RESPONSE_TOO_LONG: the code for a response too long error

@var KRPC_ERROR_INTERNAL: the code for an internal error
@var KRPC_ERROR_RECEIVED_UNKNOWN: the code for an unknown message type error
@var KRPC_ERROR_TIMEOUT: the code for a timeout error
@var KRPC_ERROR_PROTOCOL_STOPPED: the code for a stopped protocol error

@var TID: the identifier for the transaction ID
@var REQ: the identifier for a request packet
@var RSP: the identifier for a response packet
@var TYP: the identifier for the type of packet
@var ARG: the identifier for the argument to the request
@var ERR: the identifier for an error packet

@group Remote node error codes: KRPC_ERROR, KRPC_ERROR_SERVER_ERROR,
    KRPC_ERROR_MALFORMED_PACKET, KRPC_ERROR_METHOD_UNKNOWN,
    KRPC_ERROR_MALFORMED_REQUEST, KRPC_ERROR_INVALID_TOKEN,
    KRPC_ERROR_RESPONSE_TOO_LONG
@group Local node error codes: KRPC_ERROR_INTERNAL, KRPC_ERROR_RECEIVED_UNKNOWN,
    KRPC_ERROR_TIMEOUT, KRPC_ERROR_PROTOCOL_STOPPED
@group Command identifiers: TID, REQ, RSP, TYP, ARG, ERR

"""

from bencode import bencode, bdecode
from time import asctime
from math import ceil

from twisted.internet.defer import Deferred
from twisted.internet import protocol, reactor
from twisted.python import log
from twisted.trial import unittest

from khash import newID

KRPC_TIMEOUT = 20
UDP_PACKET_LIMIT = 1472

# Remote node errors
KRPC_ERROR = 200
KRPC_ERROR_SERVER_ERROR = 201
KRPC_ERROR_MALFORMED_PACKET = 202
KRPC_ERROR_METHOD_UNKNOWN = 203
KRPC_ERROR_MALFORMED_REQUEST = 204
KRPC_ERROR_INVALID_TOKEN = 205
KRPC_ERROR_RESPONSE_TOO_LONG = 206

# Local errors
KRPC_ERROR_INTERNAL = 100
KRPC_ERROR_RECEIVED_UNKNOWN = 101
KRPC_ERROR_TIMEOUT = 102
KRPC_ERROR_PROTOCOL_STOPPED = 103

# commands
TID = 't'
REQ = 'q'
RSP = 'r'
TYP = 'y'
ARG = 'a'
ERR = 'e'

class KrpcError(Exception):
    """An error occurred in the KRPC protocol."""
    pass

def verifyMessage(msg):
    """Check received message for corruption and errors.
    
    @type msg: C{dictionary}
    @param msg: the dictionary of information received on the connection
    @raise KrpcError: if the message is corrupt
    """
    
    if type(msg) != dict:
        raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "not a dictionary")
    if TYP not in msg:
        raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "no message type")
    if msg[TYP] == REQ:
        if REQ not in msg:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "request type not specified")
        if type(msg[REQ]) != str:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "request type is not a string")
        if ARG not in msg:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "no arguments for request")
        if type(msg[ARG]) != dict:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "arguments for request are not in a dictionary")
    elif msg[TYP] == RSP:
        if RSP not in msg:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "response not specified")
        if type(msg[RSP]) != dict:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "response is not a dictionary")
    elif msg[TYP] == ERR:
        if ERR not in msg:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "error not specified")
        if type(msg[ERR]) != list:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "error is not a list")
        if len(msg[ERR]) != 2:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "error is not a 2-element list")
        if type(msg[ERR][0]) not in (int, long):
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "error number is not a number")
        if type(msg[ERR][1]) != str:
            raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "error string is not a string")
#    else:
#        raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "unknown message type")
    if TID not in msg:
        raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "no transaction ID specified")
    if type(msg[TID]) != str:
        raise KrpcError, (KRPC_ERROR_MALFORMED_PACKET, "transaction id is not a string")

class hostbroker(protocol.DatagramProtocol):
    """The factory for the KRPC protocol.
    
    @type server: L{khashmir.Khashmir}
    @ivar server: the main Khashmir program
    @type config: C{dictionary}
    @ivar config: the configuration parameters for the DHT
    @type connections: C{dictionary}
    @ivar connections: all the connections that have ever been made to the
        protocol, keys are IP address and port pairs, values are L{KRPC}
        protocols for the addresses
    @ivar protocol: the protocol to use to handle incoming connections
        (added externally)
    @type addr: (C{string}, C{int})
    @ivar addr: the IP address and port of this node
    """
    
    def __init__(self, server, config):
        """Initialize the factory.
        
        @type server: L{khashmir.Khashmir}
        @param server: the main DHT program
        @type config: C{dictionary}
        @param config: the configuration parameters for the DHT
        """
        self.server = server
        self.config = config
        # this should be changed to storage that drops old entries
        self.connections = {}
        
    def datagramReceived(self, datagram, addr):
        """Optionally create a new protocol object, and handle the new datagram.
        
        @type datagram: C{string}
        @param datagram: the data received from the transport.
        @type addr: (C{string}, C{int})
        @param addr: source IP address and port of datagram.
        """
        c = self.connectionForAddr(addr)
        c.datagramReceived(datagram, addr)
        #if c.idle():
        #    del self.connections[addr]

    def connectionForAddr(self, addr):
        """Get a protocol object for the source.
        
        @type addr: (C{string}, C{int})
        @param addr: source IP address and port of datagram.
        """
        # Don't connect to ourself
        if addr == self.addr:
            raise KrcpError
        
        # Create a new protocol object if necessary
        if not self.connections.has_key(addr):
            conn = self.protocol(addr, self.server, self.transport, self.config['SPEW'])
            self.connections[addr] = conn
        else:
            conn = self.connections[addr]
        return conn

    def makeConnection(self, transport):
        """Make a connection to a transport and save our address."""
        protocol.DatagramProtocol.makeConnection(self, transport)
        tup = transport.getHost()
        self.addr = (tup.host, tup.port)
        
    def stopProtocol(self):
        """Stop all the open connections."""
        for conn in self.connections.values():
            conn.stop()
        protocol.DatagramProtocol.stopProtocol(self)

class KRPC:
    """The KRPC protocol implementation.
    
    @ivar transport: the transport to use for the protocol
    @type factory: L{khashmir.Khashmir}
    @ivar factory: the main Khashmir program
    @type addr: (C{string}, C{int})
    @ivar addr: the IP address and port of the source node
    @type noisy: C{boolean}
    @ivar noisy: whether to log additional details of the protocol
    @type tids: C{dictionary}
    @ivar tids: the transaction IDs outstanding for requests, keys are the
        transaction ID of the request, values are the deferreds to call with
        the results
    @type stopped: C{boolean}
    @ivar stopped: whether the protocol has been stopped
    """
    
    def __init__(self, addr, server, transport, spew = False):
        """Initialize the protocol.
        
        @type addr: (C{string}, C{int})
        @param addr: the IP address and port of the source node
        @type server: L{khashmir.Khashmir}
        @param server: the main Khashmir program
        @param transport: the transport to use for the protocol
        @type spew: C{boolean}
        @param spew: whether to log additional details of the protocol
            (optional, defaults to False)
        """
        self.transport = transport
        self.factory = server
        self.addr = addr
        self.noisy = spew
        self.tids = {}
        self.stopped = False

    def datagramReceived(self, data, addr):
        """Process the new datagram.
        
        @type data: C{string}
        @param data: the data received from the transport.
        @type addr: (C{string}, C{int})
        @param addr: source IP address and port of datagram.
        """
        if self.stopped:
            if self.noisy:
                log.msg("stopped, dropping message from %r: %s" % (addr, data))

        # Bdecode the message
        try:
            msg = bdecode(data)
        except Exception, e:
            if self.noisy:
                log.msg("krpc bdecode error: ")
                log.err(e)
            return

        # Make sure the remote node isn't trying anything funny
        try:
            verifyMessage(msg)
        except Exception, e:
            log.msg("krpc message verification error: ")
            log.err(e)
            return

        if self.noisy:
            log.msg("%d received from %r: %s" % (self.factory.port, addr, msg))

        # Process it based on its type
        if msg[TYP]  == REQ:
            ilen = len(data)
            
            # Requests are handled by the factory
            f = getattr(self.factory ,"krpc_" + msg[REQ], None)
            msg[ARG]['_krpc_sender'] =  self.addr
            if f and callable(f):
                try:
                    ret = f(*(), **msg[ARG])
                except KrpcError, e:
                    log.msg('Got a Krpc error while running: krpc_%s' % msg[REQ])
                    log.err(e)
                    olen = self._sendResponse(addr, msg[TID], ERR, [e[0], e[1]])
                except TypeError, e:
                    log.msg('Got a malformed request for: krpc_%s' % msg[REQ])
                    log.err(e)
                    olen = self._sendResponse(addr, msg[TID], ERR,
                                              [KRPC_ERROR_MALFORMED_REQUEST, str(e)])
                except Exception, e:
                    log.msg('Got an unknown error while running: krpc_%s' % msg[REQ])
                    log.err(e)
                    olen = self._sendResponse(addr, msg[TID], ERR,
                                              [KRPC_ERROR_SERVER_ERROR, str(e)])
                else:
                    olen = self._sendResponse(addr, msg[TID], RSP, ret)
            else:
                # Request for unknown method
                log.msg("ERROR: don't know about method %s" % msg[REQ])
                olen = self._sendResponse(addr, msg[TID], ERR,
                                          [KRPC_ERROR_METHOD_UNKNOWN, "unknown method "+str(msg[REQ])])
            if self.noisy:
                log.msg("%s >>> %s - %s %s %s" % (addr, self.factory.node.port,
                                                  ilen, msg[REQ], olen))
        elif msg[TYP] == RSP:
            # Responses get processed by their TID's deferred
            if self.tids.has_key(msg[TID]):
                df = self.tids[msg[TID]]
                # 	callback
                del(self.tids[msg[TID]])
                df.callback({'rsp' : msg[RSP], '_krpc_sender': addr})
            else:
                # no tid, this transaction timed out already...
                if self.noisy:
                    log.msg('timeout: %r' % msg[RSP]['id'])
        elif msg[TYP] == ERR:
            # Errors get processed by their TID's deferred's errback
            if self.tids.has_key(msg[TID]):
                df = self.tids[msg[TID]]
                del(self.tids[msg[TID]])
                # callback
                df.errback(KrpcError(*msg[ERR]))
            else:
                # day late and dollar short, just log it
                log.msg("Got an error for an unknown request: %r" % (msg[ERR], ))
                pass
        else:
            # Received an unknown message type
            if self.noisy:
                log.msg("unknown message type: %r" % msg)
            if msg[TID] in self.tids:
                df = self.tids[msg[TID]]
                del(self.tids[msg[TID]])
                # callback
                df.errback(KrpcError(KRPC_ERROR_RECEIVED_UNKNOWN,
                                     "Received an unknown message type: %r" % msg[TYP]))
                
    def _sendResponse(self, addr, tid, msgType, response):
        """Helper function for sending responses to nodes.
        
        @type addr: (C{string}, C{int})
        @param addr: source IP address and port of datagram.
        @param tid: the transaction ID of the request
        @param msgType: the type of message to respond with
        @param response: the arguments for the response
        """
        if not response:
            response = {}
        
        try:
            # Create the response message
            msg = {TID : tid, TYP : msgType, msgType : response}
    
            if self.noisy:
                log.msg("%d responding to %r: %s" % (self.factory.port, addr, msg))
    
            out = bencode(msg)
            
            # Make sure its not too long
            if len(out) > UDP_PACKET_LIMIT:
                # Can we remove some values to shorten it?
                if 'values' in response:
                    # Save the original list of values
                    orig_values = response['values']
                    len_orig_values = len(bencode(orig_values))
                    
                    # Caclulate the maximum value length possible
                    max_len_values = len_orig_values - (len(out) - UDP_PACKET_LIMIT)
                    assert max_len_values > 0
                    
                    # Start with a calculation of how many values should be included
                    # (assumes all values are the same length)
                    per_value = (float(len_orig_values) - 2.0) / float(len(orig_values))
                    num_values = len(orig_values) - int(ceil(float(len(out) - UDP_PACKET_LIMIT) / per_value))
    
                    # Do a linear search for the actual maximum number possible
                    bencoded_values = len(bencode(orig_values[:num_values]))
                    while bencoded_values < max_len_values and num_values + 1 < len(orig_values):
                        bencoded_values += len(bencode(orig_values[num_values]))
                        num_values += 1
                    while bencoded_values > max_len_values and num_values > 0:
                        num_values -= 1
                        bencoded_values -= len(bencode(orig_values[num_values]))
                    assert num_values > 0
    
                    # Encode the result
                    response['values'] = orig_values[:num_values]
                    out = bencode(msg)
                    assert len(out) < UDP_PACKET_LIMIT
                    log.msg('Shortened a long packet from %d to %d values, new packet length: %d' % 
                            (len(orig_values), num_values, len(out)))
                else:
                    # Too long a response, send an error
                    log.msg('Could not send response, too long: %d bytes' % len(out))
                    msg = {TID : tid, TYP : ERR, ERR : [KRPC_ERROR_RESPONSE_TOO_LONG, "response was %d bytes" % len(out)]}
                    out = bencode(msg)

        except Exception, e:
            # Unknown error, send an error message
            msg = {TID : tid, TYP : ERR, ERR : [KRPC_ERROR_SERVER_ERROR, "unknown error sending response: %s" % str(e)]}
            out = bencode(msg)
                    
        self.transport.write(out, addr)
        return len(out)
    
    def sendRequest(self, method, args):
        """Send a request to the remote node.
        
        @type method: C{string}
        @param method: the methiod name to call on the remote node
        @param args: the arguments to send to the remote node's method
        """
        if self.stopped:
            raise KrpcError, (KRPC_ERROR_PROTOCOL_STOPPED, "cannot send, connection has been stopped")

        # Create the request message
        msg = {TID : newID(), TYP : REQ,  REQ : method, ARG : args}
        if self.noisy:
            log.msg("%d sending to %r: %s" % (self.factory.port, self.addr, msg))
        data = bencode(msg)
        
        # Create the deferred and save it with the TID
        d = Deferred()
        self.tids[msg[TID]] = d

        # Schedule a later timeout call
        def timeOut(tids = self.tids, id = msg[TID], method = method, addr = self.addr):
            """Call the deferred's errback if a timeout occurs."""
            if tids.has_key(id):
                df = tids[id]
                del(tids[id])
                df.errback(KrpcError(KRPC_ERROR_TIMEOUT, "timeout waiting for '%s' from %r" % (method, addr)))
        later = reactor.callLater(KRPC_TIMEOUT, timeOut)
        
        # Cancel the timeout call if a response is received
        def dropTimeOut(dict, later_call = later):
            """Cancel the timeout call when a response is received."""
            if later_call.active():
                later_call.cancel()
            return dict
        d.addBoth(dropTimeOut)
        
        self.transport.write(data, self.addr)
        return d
    
    def stop(self):
        """Timeout all pending requests."""
        for df in self.tids.values():
            df.errback(KrpcError(KRPC_ERROR_PROTOCOL_STOPPED, 'connection has been stopped while waiting for response'))
        self.tids = {}
        self.stopped = True

#{ For testing the KRPC protocol
def connectionForAddr(host, port):
    return host
    
class Receiver(protocol.Factory):
    protocol = KRPC
    def __init__(self):
        self.buf = []
    def krpc_store(self, msg, _krpc_sender):
        self.buf += [msg]
        return {}
    def krpc_echo(self, msg, _krpc_sender):
        return {'msg': msg}
    def krpc_values(self, length, num, _krpc_sender):
        return {'values': ['1'*length]*num}

def make(port):
    af = Receiver()
    a = hostbroker(af, {'SPEW': False})
    a.protocol = KRPC
    p = reactor.listenUDP(port, a)
    return af, a, p
    
class KRPCTests(unittest.TestCase):
    timeout = 2
    
    def setUp(self):
        self.af, self.a, self.ap = make(1180)
        self.bf, self.b, self.bp = make(1181)

    def tearDown(self):
        self.ap.stopListening()
        self.bp.stopListening()

    def bufEquals(self, result, value):
        self.failUnlessEqual(self.bf.buf, value)

    def testSimpleMessage(self):
        d = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('store', {'msg' : "This is a test."})
        d.addCallback(self.bufEquals, ["This is a test."])
        return d

    def testMessageBlast(self):
        for i in range(100):
            d = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('store', {'msg' : "This is a test."})
        d.addCallback(self.bufEquals, ["This is a test."] * 100)
        return d

    def testEcho(self):
        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is a test."})
        df.addCallback(self.gotMsg, "This is a test.")
        return df

    def gotMsg(self, dict, should_be):
        _krpc_sender = dict['_krpc_sender']
        msg = dict['rsp']
        self.failUnlessEqual(msg['msg'], should_be)

    def testManyEcho(self):
        for i in xrange(100):
            df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is a test."})
            df.addCallback(self.gotMsg, "This is a test.")
        return df

    def testMultiEcho(self):
        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is a test."})
        df.addCallback(self.gotMsg, "This is a test.")

        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is another test."})
        df.addCallback(self.gotMsg, "This is another test.")

        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is yet another test."})
        df.addCallback(self.gotMsg, "This is yet another test.")
        
        return df

    def testEchoReset(self):
        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is a test."})
        df.addCallback(self.gotMsg, "This is a test.")

        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is another test."})
        df.addCallback(self.gotMsg, "This is another test.")
        df.addCallback(self.echoReset)
        return df
    
    def echoReset(self, dict):
        del(self.a.connections[('127.0.0.1', 1181)])
        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is yet another test."})
        df.addCallback(self.gotMsg, "This is yet another test.")
        return df

    def testUnknownMeth(self):
        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('blahblah', {'msg' : "This is a test."})
        df.addBoth(self.gotErr, KRPC_ERROR_METHOD_UNKNOWN)
        return df

    def testMalformedRequest(self):
        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('echo', {'msg' : "This is a test.", 'foo': 'bar'})
        df.addBoth(self.gotErr, KRPC_ERROR_MALFORMED_REQUEST)
        return df

    def gotErr(self, err, should_be):
        self.failUnlessEqual(err.value[0], should_be)
        
    def testLongPackets(self):
        df = self.a.connectionForAddr(('127.0.0.1', 1181)).sendRequest('values', {'length' : 1, 'num': 2000})
        df.addCallback(self.gotLongRsp)
        return df

    def gotLongRsp(self, dict):
        # Not quite accurate, but good enough
        self.failUnless(len(bencode(dict))-10 < UDP_PACKET_LIMIT)
        