## Copyright 2002-2003 Andrew Loewenstern, All Rights Reserved
# see LICENSE.txt for license information

import airhook
from twisted.internet.defer import Deferred
from twisted.protocols import basic
from bencode import bencode, bdecode
from twisted.internet import protocol

from twisted.internet import reactor
import time

import khash as hash

KRPC_TIMEOUT = 60

KRPC_ERROR = 1
KRPC_ERROR_METHOD_UNKNOWN = 2
KRPC_ERROR_RECEIVED_UNKNOWN = 3
KRPC_ERROR_TIMEOUT = 4

# commands
TID = 'tid'
REQ = 'req'
RSP = 'rsp'
TYP = 'typ'
ARG = 'arg'
ERR = 'err'

class hostbroker(protocol.DatagramProtocol):       
    def __init__(self, server):
        self.noisy = 0
        self.server = server
        # this should be changed to storage that drops old entries
        self.connections = {}
        
    def datagramReceived(self, datagram, addr):
        #print `addr`, `datagram`
        #if addr != self.addr:
        c = self.connectionForAddr(addr)
        c.datagramReceived(datagram)
        #if c.idle():
        #    del self.connections[addr]

    def connectionForAddr(self, addr):
        if addr == self.addr:
            raise Exception
        if not self.connections.has_key(addr):
            conn = self.protocol(addr, self.server, self.transport)
            self.connections[addr] = conn
        else:
            conn = self.connections[addr]
        return conn

    def makeConnection(self, transport):
        protocol.DatagramProtocol.makeConnection(self, transport)
        tup = transport.getHost()
        self.addr = (tup.host, tup.port)

## connection
class KRPC:
    noisy = 1
    def __init__(self, addr, server, transport):
        self.transport = transport
        self.factory = server
        self.addr = addr
        self.tids = {}

    def datagramReceived(self, str):
        # bdecode
        try:
            msg = bdecode(str)
        except Exception, e:
            if self.noisy:
                print "response decode error: " + `e`
            self.d.errback()
        else:
            #if self.noisy:
            #    print msg
            # look at msg type
            if msg[TYP]  == REQ:
                ilen = len(str)
                # if request
                #	tell factory to handle
                f = getattr(self.factory ,"krpc_" + msg[REQ], None)
                if f and callable(f):
                    msg[ARG]['_krpc_sender'] =  self.addr
                    try:
                        ret = apply(f, (), msg[ARG])
                    except Exception, e:
                        ## send error
                        out = bencode({TID:msg[TID], TYP:ERR, ERR :`e`})
                        olen = len(out)
                        self.transport.write(out, self.addr)
                    else:
                        if ret:
                            #	make response
                            out = bencode({TID : msg[TID], TYP : RSP, RSP : ret})
                        else:
                            out = bencode({TID : msg[TID], TYP : RSP, RSP : {}})
                        #	send response
                        olen = len(out)
                        self.transport.write(out, self.addr)

                else:
                    if self.noisy:
                        print "don't know about method %s" % msg[REQ]
                    # unknown method
                    out = bencode({TID:msg[TID], TYP:ERR, ERR : KRPC_ERROR_METHOD_UNKNOWN})
                    olen = len(out)
                    self.transport.write(out, self.addr)
                if self.noisy:
                    print "%s %s >>> %s - %s %s %s" % (time.asctime(), self.addr, self.factory.node.port, 
                                                    ilen, msg[REQ], olen)
            elif msg[TYP] == RSP:
                # if response
                # 	lookup tid
                if self.tids.has_key(msg[TID]):
                    df = self.tids[msg[TID]]
                    # 	callback
                    del(self.tids[msg[TID]])
                    df.callback({RSP : msg[RSP], '_krpc_sender': self.addr})
                else:
                    print 'timeout ' + `msg[RSP]['sender']`
                    # no tid, this transaction timed out already...
            elif msg[TYP] == ERR:
                # if error
                # 	lookup tid
                if self.tids.has_key(msg[TID]):
                    df = self.tids[msg[TID]]
                    # 	callback
                    df.errback(msg[ERR])
                    del(self.tids[msg[TID]])
                else:
                    # day late and dollar short
                    pass
            else:
                print "unknown message type " + `msg`
                # unknown message type
                df = self.tids[msg[TID]]
                # 	callback
                df.errback(KRPC_ERROR_RECEIVED_UNKNOWN)
                del(self.tids[msg[TID]])
                
    def sendRequest(self, method, args):
        # make message
        # send it
        msg = {TID : hash.newTID(), TYP : REQ,  REQ : method, ARG : args}
        str = bencode(msg)
        d = Deferred()
        self.tids[msg[TID]] = d
        def timeOut(tids = self.tids, id = msg[TID]):
            if tids.has_key(id):
                df = tids[id]
                del(tids[id])
                print ">>>>>> KRPC_ERROR_TIMEOUT"
                df.errback(KRPC_ERROR_TIMEOUT)
        reactor.callLater(KRPC_TIMEOUT, timeOut)
        self.transport.write(str, self.addr)
        return d
 
