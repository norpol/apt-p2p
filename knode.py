## Copyright 2002-2003 Andrew Loewenstern, All Rights Reserved
# see LICENSE.txt for license information

from node import Node
from twisted.internet.defer import Deferred
from const import reactor, NULL_ID


class IDChecker:
    def __init__(id):
        self.id = id

class KNode(Node):
    def checkSender(self, dict):
        try:
            senderid = dict['rsp']['sender']['id']
        except KeyError:
            print ">>>> No peer id in response"
            raise Exception, "No peer id in response."
        else:
            if self.id != NULL_ID and senderid != self.id:
                print "Got response from different node than expected."
                raise Exception, "Got response from different node than expected."
        return dict
        
    def ping(self, sender):
        df = self.conn.sendRequest('ping', {"sender":sender})
        df.addCallback(self.checkSender)
        return df
    def findNode(self, target, sender):
        df = self.conn.sendRequest('find_node', {"target" : target, "sender": sender})
        df.addCallback(self.checkSender)
        return df
    def storeValue(self, key, value, sender):
        df = self.conn.sendRequest('store_value', {"key" : key, "value" : value, "sender": sender})
        df.addCallback(self.checkSender)
        return df
    def findValue(self, key, sender):
        df =  self.conn.sendRequest('find_value', {"key" : key, "sender" : sender})
        df.addCallback(self.checkSender)
        return df
